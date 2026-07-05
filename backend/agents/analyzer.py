"""
Analyzer — knowledge integration + gap detection.

Reads retrieved sources and the research question tree, produces a structured
knowledge summary with citation_map and classified knowledge gaps.  Gap filling
is handled by the outer LangGraph loop — the Analyzer itself does NOT search.

Constitution I: every statement carries retrieval-result ids in the citation_map.
Constitution II: only integrates + analyses — never searches or writes prose.
"""

from __future__ import annotations

import uuid
from typing import Any

from backend.agents.base import Agent
from backend.schemas.llm_outputs import AnalyzerOutput
from backend.services import task_service
from backend.tools.llm import get_chat_model
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger
from backend.utils.state import coerce_citation_map, conv_id

logger = get_logger(__name__)

# ── System prompt ──────────────────────────────────────────────────────

_ANALYZER_SYSTEM = """You are a rigorous academic knowledge-integration analyst.
You receive research questions and a set of retrieved sources (each with id, type,
title, abstract). Your job:

1. Integrate findings from all sources into a coherent knowledge summary in
   Markdown, organised by research question.

2. Build a citation_map that maps every knowledge chunk ID (chunk_1, chunk_2, …)
   to the source result_id(s) that support it.  Every chunk MUST cite ≥1 source.

3. Identify remaining knowledge gaps, classify each by severity:
   - critical: a core question has no source coverage at all, OR multiple
     sources give contradictory findings on the same key fact
   - moderate: a sub-question has weak or thin coverage
   - minor: nice-to-have depth that is missing but does not undermine the answer
   For each gap provide a suggested_query for follow-up retrieval.

Output language: summary_content in Chinese; keywords in English / academic terms."""


class AnalyzerAgent(Agent):
    """Integrate retrieved sources into a knowledge summary with gap detection.

    The agent produces a single LLM call via ``with_structured_output()``.
    It does NOT search — gap filling is orchestrated by the outer LangGraph
    loop through the Retriever agent.
    """

    name = "analyzer"
    description = "整合检索结果，产出结构化知识摘要与缺口分析"

    # ── Public interface ──────────────────────────────────────────────

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Analyse retrieved sources and produce knowledge_summary + gaps.

        Reads:
            state["task_id"], state["research_plan"],
            state["all_retrieval_results"]

        Writes:
            state["knowledge_summary"], state["knowledge_gaps"]
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("analyzer_no_task_id")
            raise ValueError("缺少 task_id，无法执行分析")
        task_id_str = str(task_id)

        initial_results = list(state.get("all_retrieval_results") or [])
        questions = self._question_lines(state.get("research_plan") or {})

        logger.info(
            "analyzer_started",
            task_id=task_id_str,
            sources=len(initial_results),
            questions=len(questions),
        )

        # ── Empty-source fallback ──
        if not initial_results:
            summary, gaps = self._empty_summary_with_gaps(
                task_id_str, questions, conv_id(state),
            )
            state["knowledge_summary"] = summary
            state["knowledge_gaps"] = gaps
            return state

        model = get_chat_model("analyzer", temperature=0.2, max_tokens=4096)
        structured = model.with_structured_output(AnalyzerOutput, method="json_schema")
        prompt = self._build_initial_prompt(questions, initial_results)
        messages = [
            {"role": "system", "content": _ANALYZER_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        try:
            output: AnalyzerOutput = await structured.ainvoke(messages)
        except Exception:
            logger.error("analyzer_llm_failed", task_id=task_id_str, exc_info=True)
            raise

        analysis_round = int(state.get("analysis_round", 1) or 1)
        summary = self._build_summary(output, task_id_str, state)
        gaps = self._build_gaps(output, task_id_str, state, analysis_round)

        # ── Persist to MongoDB (best-effort) ──
        try:
            await task_service.store_stage_output(
                uuid.UUID(task_id_str), "analyze", summary,
            )
        except Exception:
            logger.error("analyzer_store_failed", task_id=task_id_str, exc_info=True)

        if gaps:
            try:
                await task_service.store_stage_output(
                    uuid.UUID(task_id_str), "gap", gaps,
                )
            except Exception:
                logger.error("analyzer_gaps_store_failed", task_id=task_id_str, exc_info=True)

        state["knowledge_summary"] = summary
        state["knowledge_gaps"] = gaps

        logger.info(
            "analyzer_completed",
            task_id=task_id_str,
            gaps=len(gaps),
            round=analysis_round,
        )
        return state

    # ── Helpers ──────────────────────────────────────────────────────

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

    _MAX_PROMPT_CHARS = 24000

    def _build_initial_prompt(
        self, questions: list[str], results: list[dict],
    ) -> str:
        """Build the user message with questions and source list.

        Sources are capped to stay under ``_MAX_PROMPT_CHARS``.
        """
        q_block = "\n".join(questions) if questions else "(无明确研究问题)"
        prefix = f"你需要回答以下研究问题:\n{q_block}\n\n"
        footer = "请整合以上来源，产出知识摘要、引用映射和缺口分析。"

        budget = self._MAX_PROMPT_CHARS - len(prefix) - len(footer)
        src_lines: list[str] = []
        chars_used = 0
        for r in results:
            rid = r.get("result_id") or r.get("_id") or "(no_id)"
            title = (r.get("title") or "").strip()
            stype = r.get("source_type", "")
            abstract = (r.get("abstract") or r.get("excerpt") or "").strip()[:800]
            line = f"- id={rid} | type={stype} | title={title}\n  content: {abstract}"
            if chars_used + len(line) > budget:
                break
            src_lines.append(line)
            chars_used += len(line)

        omitted = len(results) - len(src_lines)
        sources_block = "\n".join(src_lines)
        if omitted > 0:
            sources_block += f"\n\n(另有 {omitted} 条检索结果因长度限制省略)"

        return (
            f"{prefix}"
            f"检索结果 ({len(results)} 条，展示 {len(src_lines)} 条):\n{sources_block}\n\n"
            f"{footer}"
        )

    def _build_summary(
        self, output: AnalyzerOutput, task_id_str: str, state: dict,
    ) -> dict[str, Any]:
        """Normalise the typed AnalyzerOutput into the canonical summary dict."""
        content = output.summary_content.strip()
        return {
            "task_id": task_id_str,
            "conversation_id": conv_id(state),
            "phase": "initial_synthesis",
            "content": content,
            "summary_content": content,
            "citation_map": coerce_citation_map(output.citation_map),
            "generated_at": now_iso(),
        }

    def _build_gaps(
        self,
        output: AnalyzerOutput,
        task_id_str: str,
        state: dict,
        analysis_round: int,
    ) -> list[dict[str, Any]]:
        """Normalise gaps from the typed AnalyzerOutput."""
        gaps: list[dict[str, Any]] = []
        for i, g in enumerate(output.knowledge_gaps, start=1):
            description = g.description.strip()
            if not description:
                continue
            severity = g.severity
            if severity not in ("critical", "moderate", "minor"):
                severity = "moderate"
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": conv_id(state),
                "gap_id": f"{task_id_str}_r{analysis_round}_gap_{i}",
                "description": description,
                "related_question_id": str(g.related_question_id or ""),
                "severity": severity,
                "suggested_query": str(g.suggested_query or ""),
                "triggered_retrieval": False,
                "retrieval_round": analysis_round,
                "retrieval_status": "identified",
                "identified_at": now_iso(),
            })
        return gaps

    def _empty_summary_with_gaps(
        self, task_id_str: str, questions: list[str],
        conversation_id: str | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Produce an empty summary + a critical gap per core question."""
        summary = {
            "task_id": task_id_str,
            "conversation_id": conversation_id,
            "phase": "initial_synthesis",
            "content": "*暂无可用检索来源，无法形成知识摘要。*",
            "summary_content": "*暂无可用检索来源，无法形成知识摘要。*",
            "citation_map": {},
            "generated_at": now_iso(),
        }
        gaps: list[dict[str, Any]] = []
        for q in questions:
            qid, _, text = q.strip().partition(": ")
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": conversation_id,
                "gap_id": f"{task_id_str}_gap_{len(gaps) + 1}",
                "description": f"缺少针对该问题的资料：{text}",
                "related_question_id": qid.strip(),
                "severity": "critical",
                "suggested_query": text,
                "triggered_retrieval": False,
                "retrieval_round": 0,
                "retrieval_status": "identified",
                "identified_at": now_iso(),
            })
        return summary, gaps
