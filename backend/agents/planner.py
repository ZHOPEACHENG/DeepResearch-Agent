"""
Planner — research planning.

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
from backend.tools.llm import get_chat_model
from backend.schemas.llm_outputs import ClarityCheckOutput, PlanOutput
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

Rules:
- 3 to 5 core questions; each with 0 to 3 sub-questions.
- Question ids are stable short strings (q1, q1.1, q2, ...).
- 6 to 12 search keywords, mixing Chinese and English; priority 1 = highest.
- expected_sources should list the source types you recommend."""


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

Rules:
- 3 to 5 core questions; each with 0 to 3 sub-questions.
- Question ids are stable short strings (q1, q1.1, q2, ...).
- 6 to 12 search keywords, mixing Chinese and English; priority 1 = highest.
- expected_sources should list the source types you recommend."""


_CLARITY_SYSTEM = (
    "你用来判断用户的查询是否足够具体以开展研究。"
    "如果查询清晰、具体且可研究，返回 is_clear=true。"
    "如果它模糊、歧义或范围太广，返回 is_clear=false，"
    "并提供一句简洁的中文澄清问题来帮助缩小范围。"
    "只返回严格 JSON："
    '{"is_clear": true, "clarifying_question": ""} '
    "或 "
    '{"is_clear": false, "clarifying_question": "你的问题是..."}。'
    "不要输出任何其他内容。"
)


class PlannerAgent(Agent):
    """Decompose a topic into a question tree + prioritized keywords."""

    name = "planner"
    description = "分析研究主题，分解为层级问题树并生成带优先级的搜索关键词"

    async def check_clarity(self, topic: str) -> dict[str, Any]:
        """Check whether a user's research query is specific enough.

        Returns ``{"is_clear": bool, "clarifying_question": str}``.
        LLM failures default to ``is_clear=True`` to avoid blocking the pipeline.
        """
        model = get_chat_model("planner", temperature=0.0, max_tokens=128)
        structured = model.with_structured_output(ClarityCheckOutput, method="function_calling")
        messages = [
            {"role": "system", "content": _CLARITY_SYSTEM},
            {"role": "user", "content": f"研究查询：{topic}"},
        ]
        try:
            result = await structured.ainvoke(messages)
            return {"is_clear": result.is_clear, "clarifying_question": result.clarifying_question}
        except Exception:
            logger.warning("clarity_check_llm_failed", exc_info=True)
            return {"is_clear": True, "clarifying_question": ""}

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

        model = get_chat_model("planner", temperature=0.3, max_tokens=2048)
        structured = model.with_structured_output(PlanOutput, method="function_calling")
        try:
            output: PlanOutput | None = await structured.ainvoke(messages)
        except Exception:
            logger.error(
                "planner_llm_failed",
                task_id=state.get("task_id"),
                exc_info=True,
            )
            raise

        if output is None:
            raise RuntimeError(
                "Planner structured output returned None — model refused tool call"
            )

        plan = _plan_output_to_dict(output, topic=topic, task_id=state.get("task_id"))
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


# ── Module-level helpers ────────────────────────────────────────────────


def _plan_output_to_dict(
    output: PlanOutput, *, topic: str, task_id: Any,
) -> dict[str, Any]:
    """Convert a typed ``PlanOutput`` to the canonical dict shape.

    Downstream consumers (Retriever, Writer) read ``research_plan`` from
    the workflow state and expect specific keys — this converter ensures
    the dict shape stays identical to the pre-LangChain version.
    """
    # Research questions — cap and fill empty IDs.
    questions: list[dict[str, Any]] = []
    for i, q in enumerate(output.research_questions[:_MAX_CORE_QUESTIONS], start=1):
        qid = q.id or f"q{i}"
        subs: list[dict[str, Any]] = []
        for j, sq in enumerate(q.sub_questions[:_MAX_SUB_QUESTIONS], start=1):
            sid = sq.id or f"{qid}.{j}"
            subs.append({"id": sid, "question": sq.question, "priority": sq.priority})
        questions.append({"id": qid, "question": q.question, "sub_questions": subs})

    # Search keywords — cap and deduplicate.
    keywords: list[dict[str, Any]] = []
    seen: set[str] = set()
    for k in output.search_keywords[:_MAX_KEYWORDS]:
        kw_lower = k.keyword.strip().lower()
        if not kw_lower or kw_lower in seen:
            continue
        seen.add(kw_lower)
        lang = k.language if k.language in ("en", "zh") else "en"
        keywords.append({"keyword": k.keyword.strip(), "language": lang, "priority": k.priority})

    return {
        "task_id": str(task_id) if task_id else "",
        "conversation_id": None,
        "topic": str(topic),
        "research_questions": questions,
        "search_keywords": keywords,
        "expected_sources": output.expected_sources,
        "generated_at": now_iso(),
    }
