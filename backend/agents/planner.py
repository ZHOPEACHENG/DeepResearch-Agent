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
from backend.tools.llm import get_chat_model, safe_json_loads
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


_CLARITY_SYSTEM = '''
你的任务是评估一个研究查询是否足够清晰具体，可以开展研究。

按以下三步分析：
1. 将你从查询中理解到的内容总结在「understood」字段（中文）。
2. 逐条列出「unclear_aspects」：哪些维度太宽泛/模糊/缺失。
   -- 范围太广（如"新能源汽车发展趋势"）
   -- 缺少地域或时间限定
   -- 关键术语未定义
   -- 目标受众/角度不明确
3. 如果 is_clear=True，clarifying_question 留空。
   如果 is_clear=False，clarifying_question 要引导用户给出
   具体方向（给出 3-5 个具体选项，而不是开放式地请细化）。

判断标准：
- 查询包含：具体主题 + 可辨识的范围或角度 -> is_clear=True
- 查询只有一个宽泛的关键词/短语 -> is_clear=False
- 如果查询已包含至少一个具体限定（时间/地域/方法/角度），即使
  还能更细，也视为 is_clear=True

只返回纯 JSON（不要 Markdown 包装），形状为：
{"is_clear": false, "understood": "...", "unclear_aspects": ["..."], "clarifying_question": "..."}
'''


class PlannerAgent(Agent):
    """Decompose a topic into a question tree + prioritized keywords."""

    name = "planner"
    description = "分析研究主题，分解为层级问题树并生成带优先级的搜索关键词"

    async def check_clarity(self, topic: str) -> dict[str, Any]:
        """Check whether a user's research query is specific enough.

        Returns ``{"is_clear": bool, "understood": str, "unclear_aspects": list,
                  "clarifying_question": str}``.
        LLM failures default to ``is_clear=True`` to avoid blocking the pipeline.
        """
        model = get_chat_model("planner", temperature=0.0, max_tokens=512)
        structured = model.with_structured_output(ClarityCheckOutput, method="function_calling")
        messages = [
            {"role": "system", "content": _CLARITY_SYSTEM},
            {"role": "user", "content": f"研究查询：{topic}"},
        ]
        try:
            result = await structured.ainvoke(messages)
            if result is None:
                logger.warning("clarity_check_returned_none", topic=topic[:120])
                return {"is_clear": False, "clarifying_question": None}
            logger.info(
                "clarity_check_llm_succeed", topic=topic,
                is_clear=result.is_clear, unclear_count=len(result.unclear_aspects),
            )
            return {
                "is_clear": result.is_clear,
                "understood": result.understood,
                "unclear_aspects": result.unclear_aspects,
                "clarifying_question": result.clarifying_question,
            }
        except Exception:
            logger.warning("clarity_check_llm_failed", exc_info=True)
            return {"is_clear": False, "clarifying_question": None}

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
        task_id = state.get("task_id")
        try:
            output: PlanOutput = await structured.ainvoke(messages)
        except Exception:
            logger.error("planner_llm_failed", task_id=task_id, exc_info=True)
            output = None

        if output is None:
            logger.warning("planner_structured_returned_none", task_id=task_id)
            output = await _try_prompt_based_plan(model, messages, task_id)

        if output is None:
            logger.error("planner_both_paths_failed", task_id=task_id)
            raise RuntimeError(
                "Planner structured output returned None — model may not support tool_choice"
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


async def _try_prompt_based_plan(model, messages, task_id) -> PlanOutput | None:
    """Fallback: plain LLM call + safe_json_loads for plan generation."""
    json_instruction = (
        "\n\n请以纯 JSON 对象回复（不要用 Markdown 代码块包裹），格式如下：\n"
        '{\n'
        '  "research_questions": [\n'
        '    {"id": "q1", "question": "...", "sub_questions": [\n'
        '      {"id": "q1.1", "question": "...", "priority": 1}\n'
        '    ]}\n'
        '  ],\n'
        '  "search_keywords": [\n'
        '    {"keyword": "...", "language": "zh"|"en", "priority": 1}\n'
        '  ],\n'
        '  "expected_sources": ["web", "arxiv"]\n'
        '}'
    )
    fallback_messages = list(messages)
    if fallback_messages and fallback_messages[-1]["role"] == "user":
        fallback_messages[-1] = {
            **fallback_messages[-1],
            "content": fallback_messages[-1]["content"] + json_instruction,
        }
    try:
        resp = await model.ainvoke(fallback_messages)
    except Exception:
        logger.warning("planner_prompt_based_failed", task_id=task_id, exc_info=True)
        return None

    content = getattr(resp, "content", "") or ""
    parsed = safe_json_loads(content)
    if parsed is None:
        logger.warning(
            "planner_prompt_based_json_parse_failed",
            task_id=task_id,
            content_preview=content[:300],
        )
        return None

    try:
        return PlanOutput(**parsed)
    except Exception:
        logger.warning(
            "planner_prompt_based_validation_failed",
            task_id=task_id,
            exc_info=True,
        )
        return None


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
