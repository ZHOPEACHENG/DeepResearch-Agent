"""
Synthesizer — conflict resolution + merge.

Resolves conflicts between sources and merges gap-fill rounds into a single
final structured knowledge base that the Writer turns into a report. The
synthesizer is intentionally the *only* agent that reconciles disagreeing
sources and consolidates multiple analysis rounds, so the Writer receives a
single coherent view rather than competing summaries.

Constitution I: the merged knowledge keeps the citation_map from the
analyzer, so every retained claim stays traceable to its sources. Conflicts
that cannot be resolved are surfaced explicitly (never silently dropped).
Constitution II: the Synthesizer only merges + resolves — it does not
retrieve or write report prose.
"""

from __future__ import annotations

from typing import Any

from backend.agents.base import Agent
from backend.tools.llm import get_chat_model
from backend.schemas.llm_outputs import SynthesizerOutput
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger
from backend.utils.state import coerce_citation_map, conv_id

logger = get_logger(__name__)

_SYNTH_SYSTEM = """You are a meticulous research synthesizer.
You receive an initial knowledge summary, optional gap-fill notes, and the full
set of retrieved sources (id, title, type, abstract). Some sources may disagree.

Your job:
1. Resolve conflicts between sources: when sources disagree, state the
   disagreement and, where possible, explain which finding is better supported
   (by recency, peer-review, or evidence). Do NOT silently pick one.
2. Merge the gap-fill notes into the main summary so the final knowledge base
   is coherent and non-redundant.
3. Produce a final structured knowledge base grouped by research question,
   suitable for a report writer to turn into prose.

Rules:
- citation_map must cover every factual chunk in synthesized_content;
  every chunk cites ≥1 source.
- Keep synthesized_content comprehensive but well-structured with question
  headings.
- Output ONLY the JSON object."""


class SynthesizerAgent(Agent):
    """Resolve conflicts and merge rounds into a final knowledge base."""

    name = "synthesizer"
    description = "解决来源间冲突，合并补充检索结果，产出最终结构化知识库"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Merge the analyzer summary (+ gaps) into a final knowledge base.

        Reads:
            state["task_id"]
            state["knowledge_summary"]   — latest analyzer summary.
            state["knowledge_gaps"]      — gaps detected (for context).
            state["all_retrieval_results"] — full source set for conflict checks.

        Writes:
            state["synthesized_knowledge"] — dict with synthesized_content,
              conflicts, citation_map, open_questions.
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("synthesizer_no_task_id")
            raise ValueError("缺少 task_id，无法执行综合")
        task_id_str = str(task_id)

        summary = state.get("knowledge_summary") or {}
        gaps = state.get("knowledge_gaps") or []
        results = list(state.get("all_retrieval_results") or [])
        user_focus_notes = (state.get("user_focus_notes") or "").strip()

        logger.info(
            "synthesizer_started",
            task_id=task_id_str,
            sources=len(results),
            gaps=len(gaps),
            has_summary=bool(summary),
        )

        if not summary and not results:
            # Nothing to synthesize — produce a minimal stub so the Writer
            # can still emit a (mostly empty) report with gap notes.
            state["synthesized_knowledge"] = self._empty_synthesis(task_id_str)
            logger.warning("synthesizer_no_input", task_id=task_id_str)
            return state

        model = get_chat_model("default", temperature=0.2, max_tokens=4096)
        structured = model.with_structured_output(SynthesizerOutput, method="json_schema")
        messages = [
            {"role": "system", "content": _SYNTH_SYSTEM},
            {"role": "user", "content": self._build_prompt(
                summary, gaps, results, user_focus_notes,
            )},
        ]

        try:
            output: SynthesizerOutput = await structured.ainvoke(messages)
        except Exception:
            logger.error("synthesizer_llm_failed", task_id=task_id_str, exc_info=True)
            # Fall back to the analyzer summary verbatim so the pipeline
            # degrades gracefully instead of crashing.
            output = None

        if output is None:
            synthesized = {
                "task_id": task_id_str,
                "conversation_id": conv_id(state),
                "synthesized_content": _summary_content(summary),
                "conflicts": [],
                "citation_map": coerce_citation_map(summary.get("citation_map") or {}),
                "open_questions": [
                    g.get("description") for g in gaps if g.get("description")
                ],
                "generated_at": now_iso(),
            }
        else:
            synthesized = {
                "task_id": task_id_str,
                "conversation_id": conv_id(state),
                "synthesized_content": output.synthesized_content,
                "conflicts": [c.model_dump() for c in output.conflicts],
                "citation_map": coerce_citation_map(output.citation_map),
                "open_questions": output.open_questions,
                "generated_at": now_iso(),
            }

        state["synthesized_knowledge"] = synthesized

        logger.info(
            "synthesizer_completed",
            task_id=task_id_str,
            conflicts=len(synthesized["conflicts"]),
            open_questions=len(synthesized["open_questions"]),
        )
        return state

    # ── Helpers ────────────────────────────────────────────────────────

    def _build_prompt(
        self,
        summary: dict,
        gaps: list,
        results: list[dict],
        user_focus_notes: str = "",
    ) -> str:
        content = _summary_content(summary).strip()
        gap_lines = "\n".join(
            f"- [{g.get('severity', 'moderate')}] {g.get('description', '')}"
            for g in gaps
            if g.get("description")
        ) or "(none)"
        src_lines = []
        for r in results:
            rid = r.get("result_id") or r.get("_id") or "(no_id)"
            title = (r.get("title") or "").strip()
            stype = r.get("source_type", "")
            abstract = (r.get("abstract") or r.get("excerpt") or "").strip()[:1000]
            src_lines.append(f"- id={rid} | type={stype} | title={title}\n  content: {abstract}")
        sources_block = "\n".join(src_lines) or "(none)"
        # Augment outlet (B-plan): emphasise the user's focus when merging.
        focus_block = (
            f"用户特别要求关注（综合时优先保留/突出相关内容）:\n{user_focus_notes}\n\n"
            if user_focus_notes and user_focus_notes.strip()
            else ""
        )
        return (
            f"{focus_block}"
            f"Initial knowledge summary:\n{content or '(empty)'}\n\n"
            f"Knowledge gaps identified:\n{gap_lines}\n\n"
            f"Full source set ({len(results)}):\n{sources_block}\n\n"
            "Produce the final merged, conflict-resolved knowledge base."
        )

    def _empty_synthesis(self, task_id_str: str) -> dict[str, Any]:
        return {
            "task_id": task_id_str,
            "conversation_id": None,
            "synthesized_content": "*无可用知识，报告将以缺口说明为主。*",
            "conflicts": [],
            "citation_map": {},
            "open_questions": [],
            "generated_at": now_iso(),
        }


# ── Helpers ────────────────────────────────────────────────────────────


def _summary_content(summary: dict) -> str:
    """Best-effort content extraction from a knowledge summary dict."""
    return summary.get("content") or summary.get("summary_content") or ""
