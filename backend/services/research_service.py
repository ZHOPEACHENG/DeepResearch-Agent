"""
Research orchestration — LangGraph workflow (T061–T064).

Builds and runs the multi-agent research pipeline as a LangGraph
``StateGraph``:

    plan → [plan_confirmation ← user action]
         → retrieve → analyze → synthesize → write

The Analyzer uses a ReAct loop (search → think → AnalysisComplete) to
detect and fill knowledge gaps internally, eliminating the outer gap_loop
cycle. Workflow state is checkpointed to MongoDB at each node boundary so
a paused/failed run can resume (T062, Constitution III). The runner is an
async generator that yields SSE event dicts so ChatService can stream
phase_change / retrieval_complete / analysis_complete / report_complete /
error / complete events to the client (T063).

Constitution II: orchestration depends only on the ``Agent`` contract and
the ``AgentRegistry`` — adding/replacing an agent needs no change here.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.agents.analyzer import AnalyzerAgent
from backend.agents.planner import PlannerAgent
from backend.agents.retriever import RetrieverAgent
from backend.agents.synthesizer import SynthesizerAgent
from backend.agents.writer import WriterAgent
from backend.core.database import get_mongo_db
from backend.services import task_service
from backend.tools.llm import get_llm_provider, safe_json_loads
from backend.utils.datetime import now_dt
from backend.utils.logging import get_logger
from backend.utils.sse import sse_event as _sse

logger = get_logger(__name__)

# Agent instances — created once, reused across runs.
_planner = PlannerAgent()
_retriever = RetrieverAgent()
_analyzer = AnalyzerAgent()
_synthesizer = SynthesizerAgent()
_writer = WriterAgent()

# Checkpoint collection in MongoDB (snapshots of full workflow state).
_CHECKPOINT_COLLECTION = "research_checkpoints"


# ── State schema ───────────────────────────────────────────────────────


class ResearchGraphState(TypedDict, total=False):
    """Shared state passed between LangGraph nodes.

    With the Analyzer ReAct loop handling gap detection internally, the
    outer gap_loop and its supporting fields (trigger_supplementary_retrieval,
    gap_queries) have been removed. The DAG is now a simple linear pipeline.
    """

    task_id: str
    user_id: str
    conversation_id: str
    topic: str
    model: str

    research_plan: dict | None
    round_number: int                                  # always 1 (kept for compat)
    retrieval_results: list[dict]
    all_retrieval_results: list[dict]
    knowledge_summary: dict | None
    knowledge_gaps: list[dict]
    analyzer_search_log: list[dict]                    # ReAct search history
    synthesized_knowledge: dict | None
    final_report: dict | None

    error: str | None
    current_phase: str


# ── Checkpointing (T062) ───────────────────────────────────────────────


async def _save_checkpoint(state: dict[str, Any]) -> None:
    """Snapshot the full workflow state to MongoDB at a node boundary."""
    task_id = state.get("task_id")
    if not task_id:
        return
    try:
        db = get_mongo_db()
        # Store one checkpoint per task, overwritten each boundary.
        await db[_CHECKPOINT_COLLECTION].update_one(
            {"task_id": str(task_id)},
            {"$set": {"task_id": str(task_id), "state": _jsonable_state(state)}},
            upsert=True,
        )
    except Exception:
        logger.warning("checkpoint_save_failed", task_id=str(task_id), exc_info=True)


async def load_checkpoint(task_id: str) -> dict[str, Any] | None:
    """Load the last checkpoint state for a task, or None."""
    try:
        db = get_mongo_db()
        doc = await db[_CHECKPOINT_COLLECTION].find_one({"task_id": str(task_id)})
        return doc.get("state") if doc else None
    except Exception:
        logger.warning("checkpoint_load_failed", task_id=str(task_id), exc_info=True)
        return None


def _jsonable_state(state: dict[str, Any]) -> dict[str, Any]:
    """Ensure the state is JSON-serializable for Mongo persistence."""
    out: dict[str, Any] = {}
    for k, v in state.items():
        out[k] = _jsonable(v)
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


# ── Graph nodes ────────────────────────────────────────────────────────


async def _retrieve_node(state: ResearchGraphState) -> ResearchGraphState:
    """Retriever agent: multi-source search + dedup + store."""
    s: dict[str, Any] = dict(state)  # type: ignore[assignment]
    s = await _retriever.run(s)
    return s  # type: ignore[return-value]


async def _analyze_node(state: ResearchGraphState) -> ResearchGraphState:
    """Analyzer agent: integrate + gap detection."""
    s: dict[str, Any] = dict(state)  # type: ignore[assignment]
    s = await _analyzer.run(s)
    return s  # type: ignore[return-value]


async def _synthesize_node(state: ResearchGraphState) -> ResearchGraphState:
    """Synthesizer agent: conflict resolution + merge."""
    s: dict[str, Any] = dict(state)  # type: ignore[assignment]
    s = await _synthesizer.run(s)
    return s  # type: ignore[return-value]


async def _write_node(state: ResearchGraphState) -> ResearchGraphState:
    """Writer agent: report generation + persistence."""
    s: dict[str, Any] = dict(state)  # type: ignore[assignment]
    s = await _writer.run(s)
    return s  # type: ignore[return-value]


def build_research_graph() -> Any:
    """Compile the LangGraph research workflow.

    The plan + plan_confirmation happen in ChatService. This graph runs a
    simple linear pipeline: retrieve → analyze → synthesize → write → END.

    With the Analyzer ReAct loop handling gap detection internally, the
    outer gap_loop cycle has been removed.
    """
    graph = StateGraph(ResearchGraphState)
    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("analyze", _analyze_node)
    graph.add_node("synthesize", _synthesize_node)
    graph.add_node("write", _write_node)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "analyze")
    graph.add_edge("analyze", "synthesize")
    graph.add_edge("synthesize", "write")
    graph.add_edge("write", END)
    return graph.compile()


_GRAPH = build_research_graph()


# ── Pipeline runner (T063 SSE emitter + T064 graceful failure) ─────────


# Phase labels shown to the user during progress.
_PHASE_LABELS = {
    "retrieve": "正在检索资料",
    "analyze": "正在整合分析",
    "synthesize": "正在综合冲突与缺口",
    "write": "正在生成报告",
}


async def run_pipeline(
    state: dict[str, Any],
    *,
    model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Execute the research pipeline and yield SSE event dicts.

    The plan is assumed already generated and confirmed by the caller
    (ChatService). This runner drives the LangGraph graph from ``retrieve``
    through ``write`` in a simple linear pipeline (the outer gap_loop has
    been removed; gap detection is now internal to the Analyzer's ReAct loop).

    Args:
        state: Initial workflow state (must include task_id, topic,
            research_plan, conversation_id, user_id).
        model: LLM model name echoed back in report events.

    Yields:
        SSE event dicts: phase_change, retrieval_complete, analysis_complete,
        report_complete, error, complete.
    """
    task_id = state.get("task_id")
    task_id_str = str(task_id) if task_id else ""
    task_uuid = uuid.UUID(task_id_str) if task_id_str else None
    user_id = state.get("user_id")

    state.setdefault("all_retrieval_results", [])

    logger.info("pipeline_started", task_id=task_id_str)

    # Mark the task running (pending → running) if we have a user_id.
    # The transition is mandatory — if it fails (concurrency limit, DB
    # error, invalid transition) the pipeline MUST NOT proceed because the
    # final "completed" transition would be rejected and the task would
    # stay in pending forever with a persisted (orphan) report.
    if task_uuid and user_id:
        try:
            await task_service.update_task_status(
                task_uuid, "running",
                user_id=uuid.UUID(str(user_id)),
                current_phase="retrieving",
                progress_message="研究流水线已启动",
                started_at=now_dt(),
            )
        except Exception:
            logger.error("pipeline_aborted_running_failed", task_id=task_id_str, exc_info=True)
            await _mark_failed(task_uuid, user_id, "无法启动研究：状态转换失败")
            yield _sse("error", {"message": "启动研究流水线失败，请重试", "taskId": task_id_str})
            return

    merged_state: dict[str, Any] = dict(state)
    try:
        async for chunk in _GRAPH.astream(state, stream_mode="updates"):
            for node_state in chunk.values():
                if isinstance(node_state, dict):
                    merged_state.update(node_state)
            async for event in _process_chunk(
                chunk, task_id_str, task_uuid, user_id, model,
            ):
                yield event
            await _save_checkpoint(merged_state)
    except Exception as exc:
        logger.error("pipeline_failed", task_id=task_id_str, error=str(exc)[:300], exc_info=True)
        await _mark_failed(task_uuid, user_id, str(exc)[:500])
        yield _sse(
            "error",
            {"message": "研究流水线执行失败，已保存部分结果", "taskId": task_id_str},
        )
        return

    # Propagate the graph's accumulated outputs back to the caller's state.
    state.update(merged_state)
    final_report = merged_state.get("final_report")
    if final_report is None:
        await _mark_failed(task_uuid, user_id, "未能生成研究报告")
        yield _sse("error", {"message": "未能生成研究报告", "taskId": task_id_str})
        return

    # Success — mark the task completed.
    if task_uuid and user_id:
        try:
            await task_service.update_task_status(
                task_uuid, "completed",
                user_id=uuid.UUID(str(user_id)),
                current_phase="writing",
                progress_message="研究完成",
                completed_at=now_dt(),
            )
        except Exception:
            logger.warning("pipeline_status_complete_failed", task_id=task_id_str, exc_info=True)

    yield _sse("complete", {
        "taskId": task_id_str,
        "reportId": final_report.get("report_id") or final_report.get("id"),
        "reportRef": f"/research/{task_id_str}/stage-outputs?stage=report",
    })


async def _process_chunk(
    chunk: dict[str, Any],
    task_id_str: str,
    task_uuid: uuid.UUID | None,
    user_id: Any,
    model: str | None,
) -> AsyncGenerator[dict, None]:
    """Translate a LangGraph update chunk into SSE events + side effects.

    With the gap_loop removed, gap_question events are no longer emitted.
    The ``wait_gap`` parameter has been removed accordingly.
    """
    for node_name, node_state in chunk.items():
        if not isinstance(node_state, dict):
            continue

        await _checkpoint_pg(task_uuid, user_id, node_name)

        if node_name == "retrieve":
            results = node_state.get("retrieval_results") or []
            all_results = node_state.get("all_retrieval_results") or []
            yield _sse("phase_change", {
                "phase": "retrieving",
                "message": _PHASE_LABELS["retrieve"],
            })
            yield _sse("retrieval_complete", {
                "taskId": task_id_str,
                "round": int(node_state.get("round_number", 1) or 1),
                "sourceCount": len(all_results),
                "roundCount": len(results),
                "sources": _summarize_sources(all_results),
            })

        elif node_name == "analyze":
            yield _sse("phase_change", {"phase": "analyzing", "message": _PHASE_LABELS["analyze"]})
            gaps = node_state.get("knowledge_gaps") or []
            search_log = node_state.get("analyzer_search_log") or []
            yield _sse("analysis_complete", {
                "taskId": task_id_str,
                "gapCount": len(gaps),
                "searchLog": search_log,
            })

        elif node_name == "synthesize":
            yield _sse("phase_change", {
                "phase": "synthesizing",
                "message": _PHASE_LABELS["synthesize"],
            })

        elif node_name == "write":
            yield _sse("phase_change", {"phase": "writing", "message": _PHASE_LABELS["write"]})
            report = node_state.get("final_report")
            if report:
                yield _sse("report_complete", {
                    "messageId": "",
                    "taskId": task_id_str,
                    "reportId": report.get("report_id") or report.get("id"),
                    "model": model,
                    "title": report.get("title", "研究报告"),
                    "abstract": report.get("abstract", ""),
                    "sections": report.get("sections", []),
                    "citations": report.get("citations", []),
                    "gapNotes": report.get("gap_notes", ""),
                    "gap_notes": report.get("gap_notes", ""),
                })



# ── Helpers ────────────────────────────────────────────────────────────


def _summarize_sources(results: list[dict]) -> list[dict[str, Any]]:
    """Compact source list for the retrieval_card SSE payload."""
    out: list[dict[str, Any]] = []
    for r in results:
        out.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "sourceType": r.get("source_type", ""),
            "credibility": r.get("credibility", "unknown"),
        })
    return out


async def _checkpoint_pg(
    task_uuid: uuid.UUID | None, user_id: Any, phase: str
) -> None:
    """Persist the current phase to PostgreSQL (ResearchTask checkpoint)."""
    if not task_uuid or not user_id:
        return
    try:
        await task_service.save_checkpoint(
            task_uuid, uuid.UUID(str(user_id)), phase,
            _PHASE_LABELS.get(phase, phase),
        )
    except Exception:
        logger.warning("pg_checkpoint_failed", phase=phase, exc_info=True)


async def _mark_failed(task_uuid: uuid.UUID | None, user_id: Any, message: str) -> None:
    """Mark the task failed with an error message (T064)."""
    if not task_uuid or not user_id:
        return
    try:
        await task_service.update_task_status(
            task_uuid, "failed",
            user_id=uuid.UUID(str(user_id)),
            error_message=message,
            progress_message="研究失败",
        )
    except Exception:
        logger.warning("pipeline_mark_failed_failed", exc_info=True)



# ── Convenience: run the Planner (used by ChatService pre-confirmation) ─


async def check_clarity(topic: str) -> dict[str, Any]:
    """Check whether a user's research query is specific enough.

    Delegates to ``PlannerAgent.check_clarity()``. ChatService calls this
    before plan generation to avoid wasted pipeline runs on vague queries.
    """
    return await _planner.check_clarity(topic)


async def generate_plan(state: dict[str, Any]) -> dict[str, Any]:
    """Run the Planner agent and return the plan (without persisting).

    ChatService calls this to build the plan_card before the user confirms.
    The plan is also stored to MongoDB so the confirmed pipeline can reuse it.
    """
    state = await _planner.run(state)
    plan = state.get("research_plan") or {}
    task_id = state.get("task_id")
    if task_id and plan:
        try:
            await task_service.store_stage_output(uuid.UUID(str(task_id)), "plan", plan)
        except Exception:
            logger.warning("plan_store_failed", task_id=str(task_id), exc_info=True)
    return plan


# ── B-plan modify router (T??): classify + revise ──────────────────────
#
# The modify action is a *router*: one classification node decides whether the
# user's modification is an augmentation (add a viewpoint / narrow scope) or a
# revision (replace/delete core questions — change direction). Augment goes to
# a soft-guidance outlet (user_focus_notes read by Writer/Synthesizer); revise
# goes to the source-revision outlet (Planner revise mode produces a new plan).
# On any classification failure we default to augment — see D1 in the plan:
# wrongly classifying a revise as augment is the worse failure (the source plan
# is left wrong, retrieval/analysis set on the wrong direction), while wrongly
# classifying an augment as revise only costs one avoidable planner revision.


_CLASSIFY_SYSTEM = """You classify a user's modification to a research plan.

You receive the current plan (its research questions and search keywords) and
the user's modification text. Decide which kind of change it is:

- "augment": the user adds a viewpoint, narrows the scope, or asks to pay extra
  attention to something — WITHOUT rejecting or replacing the existing core
  research questions. The plan's direction stays the same.
- "revise": the user wants to replace or delete existing core questions, or
  fundamentally change the research direction (e.g. "drop the performance angle,
  analyse cost instead"). The plan's direction changes.

Return STRICT JSON only: {"mode": "augment"} or {"mode": "revise"}. No prose."""


async def classify_modification(
    original_plan: dict[str, Any], modifications: str
) -> str:
    """Classify a modify action as "augment" or "revise" (B-plan router).

    On ANY failure (LLM error, non-JSON, unexpected value) it returns
    "augment" — the safer default (D1). Never raises.
    """
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

    provider = get_llm_provider()
    messages = [
        {"role": "system", "content": _CLASSIFY_SYSTEM},
        {"role": "user", "content": (
            f"当前计划:\n{json.dumps(plan_digest, ensure_ascii=False)}\n\n"
            f"用户修改:\n{str(modifications).strip()}"
        )},
    ]

    raw = ""
    for attempt in range(2):  # one retry for empty responses (flash models)
        try:
            raw = await provider.chat(messages=messages, temperature=0.0, max_tokens=32)
        except Exception:
            logger.warning("modification_classify_failed", exc_info=True)
            return "augment"
        if raw and raw.strip():
            break
        logger.warning(
            "modification_classify_empty", attempt=attempt + 1,
        )

    parsed = safe_json_loads(raw)
    mode = parsed.get("mode") if isinstance(parsed, dict) else None
    if mode not in ("augment", "revise"):
        logger.warning(
            "modification_classify_unexpected",
            raw_preview=str(raw)[:120],
            mode=mode,
        )
        return "augment"

    logger.info("modification_classified", mode=mode)
    return mode


async def revise_plan(
    state: dict[str, Any], original_plan: dict[str, Any], directive: str
) -> dict[str, Any]:
    """Run the Planner in revise mode and return a revised plan.

    The Planner receives the original plan + the user's revision directive and
    produces a new plan that keeps the non-conflicting parts and replaces the
    conflicting ones. The revised plan is persisted to MongoDB (stage="plan_rev")
    so the run history is preserved.
    """
    revise_state = {
        **state,
        "plan_to_revise": original_plan,
        "modification_directive": directive,
    }
    state = await _planner.run(revise_state)
    plan = state.get("research_plan") or {}
    task_id = state.get("task_id")
    if task_id and plan:
        try:
            await task_service.store_stage_output(uuid.UUID(str(task_id)), "plan_rev", plan)
        except Exception:
            logger.warning("plan_rev_store_failed", task_id=str(task_id), exc_info=True)
    return plan


# ── Registry helper for main.py wiring ─────────────────────────────────


def register_agents() -> None:
    """Register all research agents with the AgentRegistry (Constitution V)."""
    from backend.agents.base import AgentRegistry

    for agent in (_planner, _retriever, _analyzer, _synthesizer, _writer):
        try:
            AgentRegistry.register(agent)
        except ValueError:
            # Already registered (e.g. reload) — safe to ignore.
            logger.debug("agent_already_registered", name=agent.name)
    logger.info(
        "research_agents_registered",
        agents=[a.name for a in (_planner, _retriever, _analyzer, _synthesizer, _writer)],
    )
