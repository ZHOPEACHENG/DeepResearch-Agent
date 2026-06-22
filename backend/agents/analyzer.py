"""
Analyzer agent — ReAct knowledge-integration loop (T053, T054).

Replaces the single-shot LLM call with a ReAct (Reasoning + Acting) loop
that can search for missing information and reflect on findings before
declaring the analysis complete. The agent is given tools (search, think,
AnalysisComplete) and iterates until it is satisfied or hits the configured
iteration budget.

Constitution I (Research Credibility First): every synthesized statement
carries retrieval-result ids in the ``citation_map`` so later conclusions
are traceable back to their sources.
Constitution II: the Analyzer only integrates + analyses — it searches via
a lightweight ``execute_ephemeral_search`` call (NOT the full Retriever
agent) and does not write prose.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from backend.agents.base import Agent
from backend.core.config import settings
from backend.services import task_service
from backend.tools.llm import LLMResponse, get_llm_provider, safe_json_loads
from backend.tools.search import execute_ephemeral_search
from backend.utils.datetime import now_iso
from backend.utils.logging import get_logger
from backend.utils.state import coerce_citation_map, conv_id

logger = get_logger(__name__)

# ── Tool definitions (OpenAI function-calling format) ─────────────────

_ANALYZER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "执行一次学术/网络搜索，获取与特定查询相关的文献或网页。"
                "每次调用针对单个查询和单个来源类型。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词，应使用英文或中文学术关键词。",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["web", "arxiv", "semantic_scholar"],
                        "description": (
                            "目标来源类型：web(网络), arxiv(学术预印本), "
                            "semantic_scholar(学术文献)"
                        ),
                    },
                },
                "required": ["query", "source_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "记录你的分析思考过程。用此工具来反思已知信息、识别剩余缺口、"
                "规划下一步搜索策略。不要与 search 并行调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "当前的分析思考内容",
                    },
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "AnalysisComplete",
            "description": (
                "宣布分析已完成。必须调用此工具来结束分析循环。"
                "提供完整的知识摘要、引用映射和已识别的知识缺口。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary_content": {
                        "type": "string",
                        "description": "Markdown 格式的完整知识整合摘要",
                    },
                    "citation_map": {
                        "type": "object",
                        "description": (
                            "知识块 ID 到检索结果 ID 列表的映射，"
                            '如 {"chunk_1": ["id_a", "id_b"]}'
                        ),
                        "additionalProperties": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "knowledge_gaps": {
                        "type": "array",
                        "description": "已识别的知识缺口列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "related_question_id": {"type": "string"},
                                "description": {"type": "string"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["critical", "moderate", "minor"],
                                },
                                "suggested_query": {"type": "string"},
                            },
                        },
                    },
                },
                "required": ["summary_content", "citation_map", "knowledge_gaps"],
            },
        },
    },
]

# ── ReAct system prompt ───────────────────────────────────────────────

_ANALYZER_REACT_SYSTEM = """You are a rigorous academic knowledge-integration analyst.
You have access to three tools:

1. **think(thought)** — 反思当前已知信息、识别剩余知识缺口、规划搜索策略。
   每次调用 search 前后都应先 think。不要与 search 并行调用。

2. **search(query, source_type)** — 执行一次搜索以填补具体的知识缺口。
   source_type 可选 web（网络）、arxiv（学术预印本）、semantic_scholar（学术文献）。
   每次搜索消耗一次迭代，请优先搜索最关键的问题。

3. **AnalysisComplete(summary_content, citation_map, knowledge_gaps)** —
   提交最终分析结果，结束分析循环。

**工作流程**：
1. 先用 think 评估初始检索结果覆盖了哪些研究问题，哪些问题的信息不足。
2. 用 search 填补最关键的缺口。每次搜索要有明确目标。
3. 每次搜索后用 think 整合新发现、重新评估总体覆盖情况。
4. 当你认为知识库足以全面回答研究问题，或进一步搜索不会改善覆盖时，
   调用 AnalysisComplete 提交最终分析。
5. 你有一个有限的迭代次数——请高效使用。

**规则**：
- citation_map 的 key 是稳定的知识块 ID（chunk_1, chunk_2, ...），
  value 是支持该块的检索结果 ID 列表。每个 chunk 至少引用一个来源。
- 只列出在当前检索后真正存在的缺口。
- severity: critical = 核心问题无法回答；moderate = 子问题信息薄弱；
  minor = 锦上添花的深度缺失。
- 输出语言：summary_content 使用中文，关键词使用英文/学术术语。"""


class AnalyzerAgent(Agent):
    """ReAct 分析循环：搜索 → 思考 → 整合 → 发现缺口 → 完成。

    The agent iterates: think → search → integrate → … → AnalysisComplete.
    Each search is ephemeral (not persisted to MongoDB) and serves only to
    help the agent decide whether it has enough information.
    """

    name = "analyzer"
    description = "ReAct分析循环：搜索→思考→整合→发现缺口→完成"

    # ── Public interface ──────────────────────────────────────────────

    async def run(self, state: dict[str, Any]) -> dict[str, Any]:
        """Execute the ReAct analysis loop.

        Reads:
            state["task_id"], state["research_plan"],
            state["all_retrieval_results"]

        Writes:
            state["knowledge_summary"], state["knowledge_gaps"],
            state["analyzer_search_log"], and extends
            state["all_retrieval_results"] with ephemeral results.
        """
        task_id = state.get("task_id")
        if not task_id:
            logger.error("analyzer_no_task_id")
            raise ValueError("缺少 task_id，无法执行分析")
        task_id_str = str(task_id)

        initial_results = list(state.get("all_retrieval_results") or [])
        questions = self._question_lines(state.get("research_plan") or {})

        logger.info(
            "analyzer_react_started",
            task_id=task_id_str,
            sources=len(initial_results),
            questions=len(questions),
        )

        # ── Empty-source fallback (unchanged from pre-ReAct behaviour) ──
        if not initial_results:
            summary, gaps = self._empty_summary_with_gaps(task_id_str, questions)
            state["knowledge_summary"] = summary
            state["knowledge_gaps"] = gaps
            state["analyzer_search_log"] = []
            return state

        provider = get_llm_provider("analyzer")
        max_iters = settings.max_analyzer_iterations

        # ReAct working state.
        all_results: list[dict] = list(initial_results)
        search_log: list[dict] = []
        final_summary: dict | None = None
        final_gaps: list[dict] = []
        iteration = 0

        # Build the initial conversation.
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _ANALYZER_REACT_SYSTEM},
            {"role": "user", "content": self._build_react_initial_prompt(
                questions, initial_results,
            )},
        ]

        for iteration in range(1, max_iters + 1):
            logger.info(
                "analyzer_react_iteration",
                task_id=task_id_str,
                iteration=iteration,
                total_results=len(all_results),
            )

            # ── Call LLM with tools ──
            try:
                response: LLMResponse = await provider.chat_with_tools(
                    messages=messages,
                    tools=_ANALYZER_TOOLS,
                    tool_choice="auto",
                    temperature=0.2,
                    max_tokens=4096,
                )
            except Exception:
                logger.error(
                    "analyzer_react_llm_failed",
                    task_id=task_id_str,
                    iteration=iteration,
                    exc_info=True,
                )
                break  # exit loop; assemble from what we have

            # Append assistant turn to the conversation.
            assistant_msg: dict[str, Any] = {"role": "assistant", "content": response.content}
            if response.tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(
                         tc["arguments"], ensure_ascii=False,
                     )}}
                    for tc in response.tool_calls
                ]
            messages.append(assistant_msg)

            # ── No tool calls — model tried to respond in plain text ──
            if not response.tool_calls:
                parsed = safe_json_loads(response.content)
                if parsed and "summary_content" in parsed:
                    final_summary = parsed
                    logger.info("analyzer_react_complete_via_content", task_id=task_id_str)
                break

            # ── Process tool calls ──
            tool_results: list[dict[str, Any]] = []

            for tc in response.tool_calls:
                name = tc.get("name", "")
                args = tc.get("arguments", {})

                if name == "think":
                    thought = str(args.get("thought", ""))
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": f"[think] {thought}",
                    })
                    logger.debug("analyzer_react_think", iteration=iteration, preview=thought[:80])

                elif name == "search":
                    query = str(args.get("query", ""))
                    source_type = str(args.get("source_type", "web"))
                    if not query:
                        tool_results.append({
                            "role": "tool", "tool_call_id": tc["id"],
                            "content": "[search] 错误：查询词不能为空",
                        })
                        continue

                    ephemeral = await execute_ephemeral_search(
                        query=query, source_type=source_type, limit=5,
                    )

                    # Convert to dicts and add to all_results for citation support.
                    for i, r in enumerate(ephemeral):
                        result_dict = {
                            "result_id": f"react_{task_id_str}_{iteration}_{i}",
                            "task_id": task_id_str,
                            "title": r.title,
                            "url": r.url,
                            "abstract": r.abstract,
                            "excerpt": r.excerpt,
                            "source_type": r.source_type,
                            "authors": r.authors,
                            "publication_date": r.publication_date,
                            "credibility": r.credibility,
                            "round": 0,  # mark as ReAct-internal
                        }
                        all_results.append(result_dict)

                    search_log.append({
                        "iteration": iteration,
                        "query": query,
                        "source_type": source_type,
                        "result_count": len(ephemeral),
                    })

                    summary_line = (
                        f"[search] query='{query}' source={source_type} "
                        f"returned={len(ephemeral)} results"
                    )
                    if ephemeral:
                        titles = "; ".join(r.title[:60] for r in ephemeral[:3])
                        summary_line += f" | titles: {titles}"
                    tool_results.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": summary_line,
                    })

                    logger.info(
                        "analyzer_react_search",
                        task_id=task_id_str,
                        iteration=iteration,
                        query=query[:80],
                        source=source_type,
                        returned=len(ephemeral),
                    )

                elif name == "AnalysisComplete":
                    final_summary = args
                    tool_results.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": "[AnalysisComplete] 分析完成",
                    })
                    logger.info(
                        "analyzer_react_complete", task_id=task_id_str, iteration=iteration,
                    )
                    break  # exit tool-call processing loop (tool_results appended below)

            # Append tool results to conversation.
            messages.extend(tool_results)

            # If AnalysisComplete was called, exit iteration loop.
            if final_summary is not None:
                break

        # ── Post-loop: assemble final output ──
        if final_summary is None:
            logger.warning(
                "analyzer_react_no_complete",
                task_id=task_id_str,
                iterations=iteration,
            )
            # Try to salvage from the last assistant response.
            for msg in reversed(messages):
                if msg.get("role") == "assistant" and msg.get("content"):
                    parsed = safe_json_loads(str(msg["content"])) or {}
                    if parsed:
                        final_summary = parsed
                        break
            if final_summary is None:
                final_summary = {}

        summary = self._build_react_summary(final_summary, task_id_str, state)
        final_gaps = self._build_react_gaps(final_summary, task_id_str, state)

        # Persist to MongoDB (best-effort).
        try:
            await task_service.store_stage_output(
                uuid.UUID(task_id_str), "analyze", summary,
            )
        except Exception:
            logger.error("analyzer_store_failed", task_id=task_id_str, exc_info=True)

        if final_gaps:
            try:
                await task_service.store_stage_output(
                    uuid.UUID(task_id_str), "gap", final_gaps,
                )
            except Exception:
                logger.error("analyzer_gaps_store_failed", task_id=task_id_str, exc_info=True)

        state["knowledge_summary"] = summary
        state["knowledge_gaps"] = final_gaps
        state["analyzer_search_log"] = search_log

        # Add ReAct-internal results to cumulative list for citation tracing.
        react_results = [
            r for r in all_results
            if r.get("result_id", "").startswith("react_")
        ]
        if react_results:
            prior = list(state.get("all_retrieval_results") or [])
            prior.extend(react_results)
            state["all_retrieval_results"] = prior

        logger.info(
            "analyzer_react_finished",
            task_id=task_id_str,
            total_iterations=iteration,
            total_searches=len(search_log),
            total_results=len(all_results),
            gaps=len(final_gaps),
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

    # Limit the total prompt body to avoid exceeding model context windows.
    # Each ReAct iteration adds assistant + tool messages on top.
    _MAX_PROMPT_CHARS = 24000

    def _build_react_initial_prompt(
        self, questions: list[str], results: list[dict],
    ) -> str:
        """Build the user message that kicks off the ReAct loop.

        Sources are capped to stay under ``_MAX_PROMPT_CHARS`` so the
        conversation stays within reasonable model context limits.
        """
        q_block = "\n".join(questions) if questions else "(无明确研究问题)"
        prefix = f"你需要回答以下研究问题:\n{q_block}\n\n"
        footer = (
            "请先使用 think 评估初始结果覆盖了哪些问题、哪些问题的信息不足，"
            "然后使用 search 查找缺失的信息。"
            "完成后调用 AnalysisComplete 提交最终分析。"
        )

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
            f"初始检索结果 ({len(results)} 条，展示 {len(src_lines)} 条):\n{sources_block}\n\n"
            f"{footer}"
        )

    def _build_react_summary(
        self, parsed: dict, task_id_str: str, state: dict,
    ) -> dict[str, Any]:
        """Normalise the parsed AnalysisComplete output into the canonical summary shape."""
        content = (parsed.get("summary_content") or "").strip()
        citation_map = coerce_citation_map(parsed.get("citation_map") or {})
        return {
            "task_id": task_id_str,
            "conversation_id": conv_id(state),
            "phase": "react_analysis",
            "content": content,
            "summary_content": content,
            "citation_map": citation_map,
            "generated_at": now_iso(),
        }

    def _build_react_gaps(
        self, parsed: dict, task_id_str: str, state: dict,
    ) -> list[dict[str, Any]]:
        """Normalise gaps from the parsed AnalysisComplete output."""
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
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": conv_id(state),
                "gap_id": f"{task_id_str}_react_gap_{i + 1}",
                "description": description,
                "related_question_id": str(g.get("related_question_id") or ""),
                "severity": severity,
                "suggested_query": str(g.get("suggested_query") or ""),
                "triggered_retrieval": False,  # no more outer gap loops
                "retrieval_round": 0,
                "retrieval_status": "identified_by_react",
                "identified_at": now_iso(),
            })
        return gaps

    def _empty_summary_with_gaps(
        self, task_id_str: str, questions: list[str],
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
            qid, _, text = q.strip().partition(": ")
            gaps.append({
                "task_id": task_id_str,
                "conversation_id": None,
                "gap_id": f"{task_id_str}_gap_{len(gaps) + 1}",
                "description": f"缺少针对该问题的资料：{text}",
                "related_question_id": qid.strip(),
                "severity": "critical",
                "suggested_query": text,
                "triggered_retrieval": False,
                "retrieval_round": 0,
                "retrieval_status": "identified_by_react",
                "identified_at": now_iso(),
            })
        return summary, gaps
