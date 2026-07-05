"""
Research orchestration — LangGraph workflow.

The planner runs outside the graph (decision 1). The graph starts from
``retriever`` and handles the gap-fill loop internally::

graph TD
    START["START"] --> retriever["retriever"]
    retriever --> analyzer["analyzer"]
    analyzer --> router{"critical gaps?"}
    router -->|NO| synthesizer["synthesizer"]
    router -->|YES| gap_confirm["gap_confirm\n(interrupt)"]
    gap_confirm -->|answer| retriever
    gap_confirm -->|skip| synthesizer
    synthesizer --> writer["writer"]
    writer --> END["END"]


Chat mode uses a separate ``chat_node`` graph for message streaming.

Both graphs share an ``InMemorySaver`` keyed by ``thread_id=conversation_id``.
"""

from __future__ import annotations

import json
from typing import Literal, Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState
from langgraph.types import interrupt

from backend.agents.analyzer import AnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.retriever import RetrieverAgent
from backend.agents.synthesizer import SynthesizerAgent
from backend.agents.writer import WriterAgent
from backend.core.config import settings
from backend.services import task_service
from backend.tools.llm import get_chat_model
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ── Agent instances ───────────────────────────────────────────────────

planner = PlannerAgent()
_retriever = RetrieverAgent()
_analyzer = AnalyzerAgent()
_synthesizer = SynthesizerAgent()
_writer = WriterAgent()

# ── Checkpointer (shared by both graphs) ─────────────────────────────

_checkpointer = InMemorySaver()


# ── State ──────────────────────────────────────────────────────────────


class ResearchState(MessagesState):
    """Shared state for the research pipeline graph.

    Inherits ``messages`` from ``MessagesState`` with ``add_messages`` reducer,
    so chat history is auto-managed by LangGraph.
    """

    # ── task identity ──
    task_id: str
    user_id: str | None
    conversation_id: str | None
    topic: str
    model: str | None

    # ── plan ──
    research_plan: dict | None
    modification_directive: str | None
    plan_to_revise: dict | None
    _plan_status: str  # "accepted" | "rejected" | "revised"

    # ── retrieval + analysis loop ──
    analysis_round: int
    gap_queries: list[str]
    retrieval_results: list[dict]
    all_retrieval_results: list[dict]
    knowledge_summary: dict | None
    knowledge_gaps: list[dict]
    _gap_action: str       # "answer" | "skip"

    # ── synthesis + reporting ──
    synthesized_knowledge: dict | None
    final_report: dict | None
    user_focus_notes: str


# ── Graph nodes ────────────────────────────────────────────────────────


async def _chat_node(state: ResearchState) -> dict[str, Any]:
    """Single LLM call for chat mode. History is in state["messages"]."""
    model = get_chat_model("default", temperature=0.7, model_override=state.get("model") or None)
    response = await model.ainvoke(state["messages"])
    return {"messages": [response]}




async def _retriever_node(state: ResearchState) -> dict[str, Any]:
    """Multi-source search, stores results in MongoDB."""
    agent_state: dict[str, Any] = {
        "task_id": state.get("task_id"),
        "conversation_id": state.get("conversation_id"),
        "user_id": state.get("user_id"),
        "research_plan": state.get("research_plan"),
        "analysis_round": state.get("analysis_round", 1),
        "gap_queries": state.get("gap_queries", []),
        "all_retrieval_results": state.get("all_retrieval_results", []),
    }
    result = await _retriever.run(agent_state)
    return {
        "retrieval_results": result.get("retrieval_results", []),
        "all_retrieval_results": result.get("all_retrieval_results", []),
    }


async def _analyzer_node(state: ResearchState) -> dict[str, Any]:
    """Integrate sources, detect knowledge gaps."""
    agent_state: dict[str, Any] = {
        "task_id": state.get("task_id"),
        "conversation_id": state.get("conversation_id"),
        "user_id": state.get("user_id"),
        "research_plan": state.get("research_plan"),
        "all_retrieval_results": state.get("all_retrieval_results", []),
        "analysis_round": state.get("analysis_round", 1),
    }
    result = await _analyzer.run(agent_state)
    return {
        "knowledge_summary": result.get("knowledge_summary"),
        "knowledge_gaps": result.get("knowledge_gaps", []),
    }


async def _gap_confirm_node(state: ResearchState) -> dict[str, Any]:
    """Pause for user to answer or skip knowledge gaps."""
    gaps = state.get("knowledge_gaps") or []
    critical = [g for g in gaps if g.get("severity") == "critical" and g.get("suggested_query")]

    decision = interrupt({
        "type": "gap_question",
        "gaps": critical,
        "round": state.get("analysis_round", 1),
    })

    action = decision.get("action", "skip") if isinstance(decision, dict) else "skip"

    if action == "answer" and critical:
        queries = [g["suggested_query"] for g in critical if g.get("suggested_query")]
        return {
            "_gap_action": "answer",
            "gap_queries": queries,
            "analysis_round": state.get("analysis_round", 1) + 1,
        }
    return {"_gap_action": "skip"}


async def _synthesizer_node(state: ResearchState) -> dict[str, Any]:
    """Resolve conflicts, merge knowledge."""
    agent_state: dict[str, Any] = {
        "task_id": state.get("task_id"),
        "conversation_id": state.get("conversation_id"),
        "knowledge_summary": state.get("knowledge_summary"),
        "knowledge_gaps": state.get("knowledge_gaps", []),
        "all_retrieval_results": state.get("all_retrieval_results", []),
        "user_focus_notes": state.get("user_focus_notes", ""),
    }
    result = await _synthesizer.run(agent_state)
    return {"synthesized_knowledge": result.get("synthesized_knowledge")}


async def _writer_node(state: ResearchState) -> dict[str, Any]:
    """Generate cited report, persist to PostgreSQL."""
    agent_state: dict[str, Any] = {
        "task_id": state.get("task_id"),
        "conversation_id": state.get("conversation_id"),
        "research_plan": state.get("research_plan"),
        "synthesized_knowledge": state.get("synthesized_knowledge"),
        "all_retrieval_results": state.get("all_retrieval_results", []),
        "user_focus_notes": state.get("user_focus_notes", ""),
    }
    result = await _writer.run(agent_state)
    return {"final_report": result.get("final_report")}


# ── Routing functions ─────────────────────────────────────────────────


def _route_after_analyze(state: ResearchState) -> Literal["gap_confirm", "synthesizer"]:
    gaps = state.get("knowledge_gaps") or []
    analysis_round = state.get("analysis_round", 1)
    critical = [g for g in gaps if g.get("severity") == "critical" and g.get("suggested_query")]
    if critical and analysis_round < settings.max_gap_rounds:
        return "gap_confirm"
    return "synthesizer"


def _route_after_gap(state: ResearchState) -> Literal["retriever", "synthesizer"]:
    if state.get("_gap_action") == "answer":
        return "retriever"
    return "synthesizer"


# ── Graph definitions ──────────────────────────────────────────────────


_chat_graph = None
_research_graph = None


def _build_chat_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("chat_node", _chat_node)
    graph.add_edge(START, "chat_node")
    graph.add_edge("chat_node", END)
    return graph.compile(checkpointer=_checkpointer)


def _build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("retriever", _retriever_node)
    graph.add_node("analyzer", _analyzer_node)
    graph.add_node("gap_confirm", _gap_confirm_node)
    graph.add_node("synthesizer", _synthesizer_node)
    graph.add_node("writer", _writer_node)

    graph.add_edge(START, "retriever")
    graph.add_edge("retriever", "analyzer")
    graph.add_conditional_edges("analyzer", _route_after_analyze, {
        "gap_confirm": "gap_confirm",
        "synthesizer": "synthesizer",
    })
    graph.add_conditional_edges("gap_confirm", _route_after_gap, {
        "retriever": "retriever",
        "synthesizer": "synthesizer",
    })
    graph.add_edge("synthesizer", "writer")
    graph.add_edge("writer", END)
    return graph.compile(checkpointer=_checkpointer)


def get_chat_graph():
    global _chat_graph
    if _chat_graph is None:
        _chat_graph = _build_chat_graph()
    return _chat_graph


def get_research_graph():
    global _research_graph
    if _research_graph is None:
        _research_graph = _build_research_graph()
    return _research_graph


# ── SSE helpers ──────────────────────────────────────────────────────


PHASE_LABELS = {
    "retriever": "正在检索资料",
    "analyzer": "正在整合分析",
    "gap_confirm": "检测到知识缺口，等待确认",
    "synthesizer": "正在综合冲突与缺口",
    "writer": "正在生成报告",
}


def summarize_sources(results: list[dict]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in results:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "sourceType": r.get("source_type", ""),
            "credibility": r.get("credibility", "unknown"),
        })
    return out


# ── Plan / clarity helpers (used by chat_service) ──────────────────────


async def check_clarity(topic: str) -> dict[str, Any]:
    return await planner.check_clarity(topic)


async def classify_modification(
    original_plan: dict[str, Any], modifications: str,
) -> str:
    """Classify a modify action as 'augment' or 'revise' (B-plan router)."""
    if not modifications or not str(modifications).strip():
        return "augment"

    questions = original_plan.get("research_questions") if isinstance(original_plan, dict) else None
    keywords = original_plan.get("search_keywords") if isinstance(original_plan, dict) else None
    plan_digest = {
        "research_questions": [
            q.get("question") for q in questions if isinstance(q, dict)
        ] if isinstance(questions, list) else [],
        "search_keywords": [
            k.get("keyword") for k in keywords if isinstance(k, dict)
        ] if isinstance(keywords, list) else [],
    }

    from backend.schemas.llm_outputs import ModificationClassifyOutput
    model = get_chat_model("default", temperature=0.0, max_tokens=32)
    structured = model.with_structured_output(ModificationClassifyOutput, method="json_schema")
    messages = [
        {"role": "system", "content": (
            'You classify a user modification to a research plan. '
            '"augment": add a viewpoint without rejecting core questions. '
            '"revise": replace/delete core questions, change direction. '
            'Return JSON only: {"mode": "augment"} or {"mode": "revise"}. No prose.'
        )},
        {"role": "user", "content": (
            f"当前计划:\n{json.dumps(plan_digest, ensure_ascii=False)}\n\n"
            f"用户修改:\n{str(modifications).strip()}"
        )},
    ]

    try:
        output: ModificationClassifyOutput = await structured.ainvoke(messages)
        mode = output.mode
    except Exception:
        logger.warning("modification_classify_failed", exc_info=True)
        return "augment"

    if mode not in ("augment", "revise"):
        return "augment"
    logger.info("modification_classified", mode=mode)
    return mode


# ── Registry ───────────────────────────────────────────────────────────


def register_agents() -> None:
    from backend.agents.base import AgentRegistry
    for agent in (planner, _retriever, _analyzer, _synthesizer, _writer):
        try:
            AgentRegistry.register(agent)
        except ValueError:
            pass
    logger.info(
        "research_agents_registered",
        agents=[a.name for a in (planner, _retriever, _analyzer, _synthesizer, _writer)],
    )
