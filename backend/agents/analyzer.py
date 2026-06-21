"""
Analyzer agent — knowledge integration + gap detection (T053, T054).

Integrates the retrieved sources into a structured knowledge summary that
maps every knowledge chunk back to its source retrieval results
(``citation_map``), then compares coverage against the research questions
to identify missing information, conflicting findings, and uncovered
sub-questions. Sets ``trigger_supplementary_retrieval`` so the orchestration
can decide whether to run another gap-fill round.

Constitution I (Research Credibility First): every synthesized statement
in the summary carries the ids of the retrieval results it came from, so a
later conclusion can be traced back to its sources. AI-generated analysis
is explicitly separated from verbatim source content in the prompt.
Constitution II: the Analyzer only integrates + detects gaps — it does not
retrieve or write prose.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.agents.base import Agent
from backend.services import task_service
from backend.tools.llm import get_llm_provider, safe_json_loads
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger
from backend.utils.state import coerce_citation_map, conv_id

logger = get_logger(__name__)

_ANALYZER_SYSTEM = """You are a rigorous academic knowledge-integration analyst.
You are given a set of retrieved sources (each with an id, title, source_type,
and abstract/excerpt) and the research questions to answer.

Your job:
1. Integrate the sources into a structured knowledge summary in Markdown.
2. Group findings under the relevant research questions.
3. Clearly separate your synthesis from direct source quotes (mark quotes).
4. For every factual statement, record which source ids support it.
5. Identify knowledge GAPS: questions/sub-questions with no coverage,
   conflicting findings between sources, or missing recent data.

Return STRICT JSON only (no markdown fences, no prose) with this exact shape:
{
  "summary_content": "Markdown text of the integrated knowledge summary",
  "citation_map": {
    "chunk_1": ["retrieval_result_id_a", "retrieval_result_id_b"]
  },
  "knowledge_gaps": [
    {
      "related_question_id": "q1.2",
      "description": "what is missing and why it matters",
      "severity": "critical|moderate|minor",
      "suggested_query": "a search query that could fill this gap"
    }
  ]
}

Rules:
- citation_map keys are stable chunk ids (chunk_1, chunk_2, ...); values are
  arrays of retrieval result ids that the chunk draws from. Every chunk MUST
  cite at least one source.
- Only list gaps that genuinely exist given the provided sources.
- severity: critical = a core question is unanswerable; moderate = a
  sub-question is thin; minor = nice-to-have depth missing.
- Output ONLY the JSON object."""


class AnalyzerAgent(Agent):
    """Integrate sources into a cited knowledge summary and detect gaps."""

    name = "analyzer"
    description = "整合多源结果为带引用的知识摘要，并检测知识缺口"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Build a knowledge summary + gap list from the retrieved sources.

        Reads:
            state["task_id"]
            state["conversation_id"]          — optional.
            state["research_plan"]            — question tree for coverage check.
            state["all_retrieval_results"]    — cumulative retrieval docs.
            state["round_number"]             — labels the summary phase.

        Writes:
            state["knowledge_summary"]  — dict (KnowledgeSummary-shaped).
            state["knowledge_gaps"]     — list[dict] (KnowledgeGap-shaped).
            state["trigger_supplementary_retrieval"] — bool.

        If there are no sources at all, a degenerate empty summary is produced
        and every core question is flagged as a critical gap so the pipeline
        can attempt another retrieval round rather than writing an empty report.
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("analyzer_no_task_id")
            raise ValueError("缺少 task_id，无法执行分析")
        task_id_str = str(task_id)
        results = list(state.get("all_retrieval_results") or [])
        round_number = int(state.get("round_number", 1) or 1)

        questions = self._question_lines(state.get("research_plan") or {})

        logger.info(
            "analyzer_started",
            task_id=task_id_str,
            round=round_number,
            sources=len(results),
            questions=len(questions),
        )

        if not results:
            summary, gaps = self._empty_summary_with_gaps(task_id_str, questions)
            state["knowledge_summary"] = summary
            state["knowledge_gaps"] = gaps
            state["trigger_supplementary_retrieval"] = bool(gaps)
            logger.warning(
                "analyzer_no_sources",
                task_id=task_id_str,
                gaps=len(gaps),
            )
            return state

        provider = get_llm_provider()
        messages = [
            {"role": "system", "content": _ANALYZER_SYSTEM},
            {"role": "user", "content": self._build_user_prompt(questions, results)},
        ]

        raw = await _chat_with_empty_retry(
            provider, messages, task_id_str, temperature=0.2, max_tokens=4096,
        )

        parsed = safe_json_loads(raw)
        if parsed is None:
            logger.warning(
                "analyzer_json_parse_failed",
                task_id=task_id_str,
                raw_len=len(raw),
                preview=raw[:200],
            )
            parsed = {}
            state["analyzer_degraded"] = True

        summary = self._build_summary(parsed, task_id_str, state, round_number)
        gaps = self._build_gaps(parsed, task_id_str, state)

        # Persist to MongoDB (best-effort; pipeline continues if it fails).
        try:
            await task_service.store_stage_output(
                uuid.UUID(task_id_str), "analyze", summary
            )
        except Exception:
            logger.error("analyzer_store_failed", task_id=task_id_str, exc_info=True)

        if gaps:
            try:
                await task_service.store_stage_output(
                    uuid.UUID(task_id_str), "gap", gaps
                )
            except Exception:
                logger.error("analyzer_gaps_store_failed", task_id=task_id_str, exc_info=True)

        state["knowledge_summary"] = summary
        state["knowledge_gaps"] = gaps
        # Trigger another round only when there are critical/moderate gaps
        # AND we have not exhausted the gap-round budget (orchestration
        # checks the budget; here we just signal intent).
        state["trigger_supplementary_retrieval"] = any(
            g.get("severity") in ("critical", "moderate") for g in gaps
        )

        logger.info(
            "analyzer_completed",
            task_id=task_id_str,
            round=round_number,
            gaps=len(gaps),
            trigger=state["trigger_supplementary_retrieval"],
        )
        return state

    # ── Helpers ────────────────────────────────────────────────────────

    def _question_lines(self, plan: dict[str, Any]) -> list[str]:
        """Flatten the question tree into 'qid: question' lines."""
        out: list[str] = []
        for q in plan.get("research_questions", []) if isinstance(plan, dict) else []:
            if not isinstance(q, dict):
                continue
            qid = q.get("id", "?")
            text = (q.get("question") or "").strip()
            if text:
                out.append(f"{qid}: {text}")
            for sq in q.get("sub_questions", []) or []:
                if not isinstance(sq, dict):
                    continue
                sid = sq.get("id", "?")
                stext = (sq.get("question") or "").strip()
                if stext:
                    out.append(f"  {sid}: {stext}")
        return out

    def _build_user_prompt(self, questions: list[str], results: list[dict]) -> str:
        q_block = "\n".join(questions) if questions else "(no explicit questions provided)"
        src_lines = []
        for r in results:
            rid = r.get("result_id") or r.get("_id") or "(no_id)"
            title = (r.get("title") or "").strip()
            stype = r.get("source_type", "")
            abstract = (r.get("abstract") or r.get("excerpt") or "").strip()[:1200]
            src_lines.append(f"- id={rid} | type={stype} | title={title}\n  content: {abstract}")
        sources_block = "\n".join(src_lines)
        return (
            f"Research questions to cover:\n{q_block}\n\n"
            f"Retrieved sources ({len(results)}):\n{sources_block}\n\n"
            "Produce the integrated knowledge summary, citation_map, and gaps."
        )

    def _build_summary(
        self, parsed: dict, task_id_str: str, state: dict, round_number: int
    ) -> dict[str, Any]:
        content = (parsed.get("summary_content") or "").strip()
        citation_map = parsed.get("citation_map") or {}
        clean_map = coerce_citation_map(citation_map)
        cid = conv_id(state)
        return {
            "task_id": task_id_str,
            "conversation_id": cid,
            "phase": (
                f"gap_fill_round_{round_number - 1}"
                if round_number > 1
                else "initial_synthesis"
            ),
            "content": content,
            "summary_content": content,
            "citation_map": clean_map,
            "generated_at": now_iso(),
        }

    def _build_gaps(self, parsed: dict, task_id_str: str, state: dict) -> list[dict[str, Any]]:
        raw_gaps = parsed.get("knowledge_gaps") or []
        if not isinstance(raw_gaps, list):
            return []
        gaps: list[dict[str, Any]] = []
        for i, g in enumerate(raw_gaps):
            if not isinstance(g, dict):
                continue
            description = (g.get("description") or "").strip()
            if not description:
                continue
            severity = (g.get("severity") or "moderate").strip()
            if severity not in ("critical", "moderate", "minor"):
                severity = "moderate"
            cid = conv_id(state)
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": cid,
                "gap_id": f"{task_id_str}_gap_{i + 1}",
                "description": description,
                "related_question_id": str(g.get("related_question_id") or ""),
                "severity": severity,
                "suggested_query": str(g.get("suggested_query") or ""),
                "triggered_retrieval": severity in ("critical", "moderate"),
                "retrieval_round": int(state.get("round_number", 1) or 1) + 1,
                "retrieval_status": "pending",
                "identified_at": now_iso(),
            })
        return gaps

    def _empty_summary_with_gaps(
        self, task_id_str: str, questions: list[str]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Produce an empty summary + a critical gap per core question."""
        summary = {
            "task_id": task_id_str,
            "conversation_id": None,
            "phase": "initial_synthesis",
            "content": "*暂无可用检索来源，无法形成知识摘要。*",
            "summary_content": "*暂无可用检索来源，无法形成知识摘要。*",
            "citation_map": {},
            "generated_at": now_iso(),
        }
        gaps: list[dict[str, Any]] = []
        for q in questions:
            # q is "qid: text" or "  qid: text"
            qid, _, text = q.strip().partition(": ")
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": None,
                "gap_id": f"{task_id_str}_gap_{len(gaps) + 1}",
                "description": f"缺少针对该问题的资料：{text}",
                "related_question_id": qid.strip(),
                "severity": "critical",
                "suggested_query": text,
                "triggered_retrieval": True,
                "retrieval_round": 2,
                "retrieval_status": "pending",
                "identified_at": now_iso(),
            })
        return summary, gaps


# ── Shared helpers ─────────────────────────────────────────────────────


async def _chat_with_empty_retry(
    provider, messages: list[dict], task_id_str: str,
    temperature: float = 0.2, max_tokens: int = 4096, max_retries: int = 1,
) -> str:
    """Call the LLM, retrying once if the response is empty.

    Some models (notably flash/small variants with strict JSON prompts)
    occasionally return an empty string for very long inputs.  One retry
    often resolves it; if not, we return the empty string and let the
    caller apply its fallback.
    """
    for attempt in range(max_retries + 1):
        try:
            raw = await provider.chat(
                messages=messages, temperature=temperature, max_tokens=max_tokens,
            )
        except Exception:
            logger.error("analyzer_llm_failed", task_id=task_id_str, exc_info=True)
            raise
        if raw and raw.strip():
            return raw
        logger.warning(
            "analyzer_llm_empty_response",
            task_id=task_id_str,
            attempt=attempt + 1,
        )
    return raw  # return the (empty) last attempt; caller handles it


