"""
Planner agent — research planning (T051).

Analyzes a research topic and decomposes it into a hierarchical question
tree (core research questions + sub-questions) plus prioritized search
keywords. Output is a structured plan stored on the workflow state as
``research_plan`` and persisted to MongoDB by the Retriever/orchestration.

Constitution II: the Planner owns *only* planning — it never searches,
integrates, or writes. It communicates via the ``research_plan`` contract.
Constitution I: the plan records which questions the research must answer,
so every later conclusion can be traced back to a question in the tree.
"""

from __future__ import annotations

import json
from typing import Any

from backend.agents.base import Agent
from backend.tools.llm import get_llm_provider, safe_json_loads
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Max sub-questions per core question and core questions per plan — keeps the
# plan focused and the retrieval budget bounded (Constitution VII).
_MAX_CORE_QUESTIONS = 5
_MAX_SUB_QUESTIONS = 3
_MAX_KEYWORDS = 12

_PLANNER_SYSTEM = """You are a senior academic research planner.
Given a research topic, produce a structured research plan that another system will execute.

Decompose the topic into a small hierarchy of research questions (core questions,
each with a few sub-questions), and generate search keywords covering both the
user's likely language (Chinese) and English academic terminology.

Return STRICT JSON only (no markdown fences, no prose) with this exact shape:
{
  "research_questions": [
    {"id": "q1", "question": "core question text", "sub_questions": [
      {"id": "q1.1", "question": "sub question text", "priority": 1}
    ]}
  ],
  "search_keywords": [
    {"keyword": "keyword text", "language": "en|zh", "priority": 1}
  ],
  "expected_sources": ["web", "arxiv", "semantic_scholar"]
}

Rules:
- 3 to 5 core questions; each with 0 to 3 sub-questions.
- Question ids are stable short strings (q1, q1.1, q2, ...).
- 6 to 12 search keywords, mixing Chinese and English; priority 1 = highest.
- expected_sources should list the source types you recommend.
- Output ONLY the JSON object."""


_PLANNER_REVISE_SYSTEM = """You are a senior academic research planner revising an existing plan.

You receive the CURRENT plan (a JSON object with research_questions and
search_keywords) and a user's REVISION DIRECTIVE. The user wants to change the
research direction — replace or delete some core questions, shift the focus.

Produce a REVISED plan that:
- Honours the user's directive: replace/delete the conflicting core questions
  and add new ones the directive implies; regenerate search keywords to match
  the new direction.
- KEEPS the non-conflicting parts of the original plan (questions and keywords
  that still serve the revised direction).
- Keeps the same JSON shape as the original.

Return STRICT JSON only (no markdown fences, no prose) with this exact shape:
{
  "research_questions": [
    {"id": "q1", "question": "core question text", "sub_questions": [
      {"id": "q1.1", "question": "sub question text", "priority": 1}
    ]}
  ],
  "search_keywords": [
    {"keyword": "keyword text", "language": "en|zh", "priority": 1}
  ],
  "expected_sources": ["web", "arxiv", "semantic_scholar"]
}

Rules:
- 3 to 5 core questions; each with 0 to 3 sub-questions.
- Question ids are stable short strings (q1, q1.1, q2, ...).
- 6 to 12 search keywords, mixing Chinese and English; priority 1 = highest.
- expected_sources should list the source types you recommend.
- Output ONLY the JSON object."""


class PlannerAgent(Agent):
    """Decompose a topic into a question tree + prioritized keywords."""

    name = "planner"
    description = "分析研究主题，分解为层级问题树并生成带优先级的搜索关键词"

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """
        Generate (or revise) a research plan.

        Two modes:
        - Generate (default): reads ``state["topic"]`` and decomposes it into a
          question tree + keywords.
        - Revise: when ``state["plan_to_revise"]`` and
          ``state["modification_directive"]`` are present, produces a revised
          plan that keeps the non-conflicting parts of the original and replaces
          the conflicting ones per the user's directive. The topic is inherited
          from the original plan (revision does not change the topic text).

        Writes:
            state["research_plan"] — dict matching the MongoDB ResearchPlan
                                     document shape (questions, keywords, …).

        Raises:
            ValueError: if the topic is missing in generate mode.
        """
        plan_to_revise = state.get("plan_to_revise")
        directive = state.get("modification_directive")
        revise_mode = (
            isinstance(plan_to_revise, dict)
            and isinstance(directive, str)
            and directive.strip()
        )

        if revise_mode:
            # Revise mode: inherit the original topic (revision keeps the
            # subject; only the research direction changes).
            topic = plan_to_revise.get("topic") or state.get("topic") or ""
            provider = get_llm_provider()
            messages = [
                {"role": "system", "content": _PLANNER_REVISE_SYSTEM},
                {"role": "user", "content": (
                    f"当前计划:\n{json.dumps(plan_to_revise, ensure_ascii=False)}\n\n"
                    f"用户修订指令:\n{directive.strip()}"
                )},
            ]
            logger.info(
                "planner_revise_started",
                task_id=state.get("task_id"),
                user_id=state.get("user_id"),
                directive=directive[:120],
            )
        else:
            # Generate mode.
            topic = state.get("topic")
            if not topic or not str(topic).strip():
                logger.error("planner_no_topic", task_id=state.get("task_id"))
                raise ValueError("研究主题为空，无法生成研究计划")
            provider = get_llm_provider()
            messages = [
                {"role": "system", "content": _PLANNER_SYSTEM},
                {"role": "user", "content": f"研究主题：{topic}"},
            ]
            logger.info(
                "planner_started",
                task_id=state.get("task_id"),
                user_id=state.get("user_id"),
                topic=str(topic)[:120],
            )

        try:
            raw = await provider.chat(messages=messages, temperature=0.3, max_tokens=2048)
        except Exception:
            logger.error(
                "planner_llm_failed",
                task_id=state.get("task_id"),
                exc_info=True,
            )
            raise

        plan = self._parse_plan(raw, topic=topic, task_id=state.get("task_id"))
        state["research_plan"] = plan

        logger.info(
            "planner_completed",
            task_id=state.get("task_id"),
            user_id=state.get("user_id"),
            mode="revise" if revise_mode else "generate",
            core_questions=len(plan["research_questions"]),
            keywords=len(plan["search_keywords"]),
        )
        return state

    # ── Helpers ────────────────────────────────────────────────────────

    def _parse_plan(self, raw: str, *, topic: str, task_id: Any) -> dict[str, Any]:
        """Parse and normalize the LLM's JSON plan, with a safe fallback."""
        parsed = safe_json_loads(raw)
        if parsed is None:
            logger.warning(
                "planner_json_parse_failed",
                task_id=task_id,
                preview=raw[:200],
            )
            parsed = {}

        questions = self._normalize_questions(parsed.get("research_questions"))
        keywords = self._normalize_keywords(parsed.get("search_keywords"))
        expected = parsed.get("expected_sources") or ["web", "arxiv", "semantic_scholar"]

        return {
            "task_id": str(task_id) if task_id else "",
            "conversation_id": None,
            "topic": str(topic),
            "research_questions": questions,
            "search_keywords": keywords,
            "expected_sources": expected,
            "generated_at": now_iso(),
        }

    def _normalize_questions(self, raw: Any) -> list[dict[str, Any]]:
        """Coerce the LLM output into the canonical question-tree shape."""
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for i, q in enumerate(raw[:_MAX_CORE_QUESTIONS], start=1):
            if not isinstance(q, dict):
                continue
            qid = str(q.get("id") or f"q{i}")
            text = (q.get("question") or q.get("text") or "").strip()
            if not text:
                continue
            subs = self._normalize_sub_questions(q.get("sub_questions"), parent=qid)
            out.append({"id": qid, "question": text, "sub_questions": subs})
        return out

    def _normalize_sub_questions(self, raw: Any, *, parent: str) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for j, sq in enumerate(raw[:_MAX_SUB_QUESTIONS], start=1):
            if not isinstance(sq, dict):
                continue
            sid = str(sq.get("id") or f"{parent}.{j}")
            text = (sq.get("question") or sq.get("text") or "").strip()
            if not text:
                continue
            priority = sq.get("priority")
            out.append({
                "id": sid,
                "question": text,
                "priority": int(priority) if isinstance(priority, (int, float)) else j,
            })
        return out

    def _normalize_keywords(self, raw: Any) -> list[dict[str, Any]]:
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for k in raw[:_MAX_KEYWORDS]:
            if isinstance(k, str):
                kw, lang, prio = k.strip(), "en", len(out) + 1
            elif isinstance(k, dict):
                kw = (k.get("keyword") or "").strip()
                lang = (k.get("language") or "en").strip()
                prio = k.get("priority", len(out) + 1)
            else:
                continue
            if not kw or kw.lower() in seen:
                continue
            seen.add(kw.lower())
            out.append({
                "keyword": kw,
                "language": lang if lang in ("en", "zh") else "en",
                "priority": int(prio) if isinstance(prio, (int, float)) else len(out),
            })
        return out
