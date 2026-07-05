"""
ChatService — thin orchestration layer over LangGraph graphs.

``handle_message`` invokes either the chat graph (simple LLM reply) or the
research graph (full pipeline).  Both graphs share an ``InMemorySaver`` keyed by
``thread_id=conversation_id`` so message history is managed by
LangGraph's ``MessagesState``.

Plan / gap actions resume the interrupted research graph via
``graph.astream(Command(resume=...), config)``.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Literal

from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from backend.services import conversation_service, research_service, task_service
from backend.services.research_service import (
    PHASE_LABELS,
    get_chat_graph,
    get_research_graph,
    planner,
    summarize_sources,
)
from backend.utils.logging import get_logger
from backend.utils.sse import sse_event as _sse

logger = get_logger(__name__)

# ── Plan-action pause (asyncio.Event — decision 4) ──────────────────

_plan_events: dict[str, asyncio.Event] = {}
_plan_actions: dict[str, dict] = {}


def _is_thinking_conflict(error: BaseException) -> bool:
    """Return True when the error is caused by thinking mode rejecting tool_choice."""
    return (
        hasattr(error, "message") and "Thinking mode does not support" in str(error.message)
    ) or "Thinking mode does not support tool_choice" in str(error)


async def resume_plan_action(
    message_id: uuid.UUID, action: str, modifications: str | None = None,
) -> None:
    """Unblock the plan-action asyncio.Event (decision 4)."""
    _plan_actions[str(message_id)] = {"action": action, "modifications": modifications}
    event = _plan_events.pop(str(message_id), None)
    if event:
        event.set()


# ── Public API ──────────────────────────────────────────────────────────


async def handle_message(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    *,
    parent_message_id: uuid.UUID | None = None,
    model: str | None = None,
    mode: Literal["chat", "research"] = "chat",
    deep_thinking: bool = False,
) -> AsyncGenerator[dict, None]:
    """Entry point: save user message, dispatch to chat or research graph.

    Yields SSE event dicts: ``message_created`` → stream events → ``done``.
    """
    from backend.tools.llm import set_deep_thinking
    set_deep_thinking(deep_thinking)

    # Auto-title new conversations
    try:
        conv = await conversation_service.get_conversation(conversation_id, user_id)
        if conv.get("title") == "New Conversation":
            title = content[:50] + ("..." if len(content) > 50 else "")
            await conversation_service.update_conversation_title(conversation_id, user_id, title)
    except Exception:
        logger.warning("auto_title_failed", conv_id=str(conversation_id), exc_info=True)

    # Save user message
    user_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="user",
        content=content,
        message_type="text",
        parent_message_id=parent_message_id,
        model=model,
        metadata={"mode": mode},
    )
    yield _sse("message_created", {"messageId": str(user_msg.id)})

    config = {"configurable": {"thread_id": str(conversation_id)}}

    if mode == "chat":
        async for event in _run_chat(conversation_id, user_id, content, user_msg.id, config, model, mode):
            yield event
    else:
        async for event in _run_research(conversation_id, user_id, content, user_msg.id, config, model, mode):
            yield event

    yield _sse("done", {"conversationId": str(conversation_id), "model": model})



async def resume_gap_action(
    conversation_id: uuid.UUID, task_id: str, action: str,
) -> None:
    """Resume the research graph after a gap_confirm interrupt."""
    logger.info("resume_gap", task_id=task_id, action=action, conv_id=str(conversation_id))

    # 后续 retrieval_card / report_card 需要 parent_message_id 才能落库
    # （messages.parent_message_id 有外键约束）。这里查最近一条 gap_question
    # 消息，继承它的 parent（即原始 user message），把 UUID(int=0) 占位替换掉。
    parent_message_id = await _resolve_gap_parent(conversation_id)

    graph = get_research_graph()
    config = {"configurable": {"thread_id": str(conversation_id)}}
    async for chunk in graph.astream(
        Command(resume={"action": action}), config, stream_mode="updates",
    ):
        async for _ in _process_research_chunk(
            chunk, conversation_id, parent_message_id, None, "research", task_id,
        ):
            pass  # messages persisted to DB, no SSE client connected


async def _resolve_gap_parent(conversation_id: uuid.UUID) -> uuid.UUID:
    """Find the original user message id for an interrupted research thread.

    Looks for the most recent gap_question message and returns its
    parent_message_id. Falls back to the most recent user message, then
    to a nil UUID (only if no messages exist at all — the FK will still
    reject it, but that path shouldn't occur in practice).
    """
    try:
        result = await conversation_service.get_messages(conversation_id, limit=50)
    except Exception:
        logger.warning("gap_parent_lookup_failed", conv_id=str(conversation_id), exc_info=True)
        return uuid.UUID(int=0)
    for m in reversed(result.get("items", [])):
        if m.message_type == "gap_question" and m.parent_message_id:
            return m.parent_message_id
    for m in reversed(result.get("items", [])):
        if m.role == "user":
            return m.id
    return uuid.UUID(int=0)


# ── Chat mode ──────────────────────────────────────────────────────────


async def _run_chat(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    user_message_id: uuid.UUID,
    config: dict,
    model: str | None,
    mode: Literal["chat", "research"],
) -> AsyncGenerator[dict, None]:
    """Stream an LLM reply using the chat graph. History is auto-managed by MessagesState."""
    graph = get_chat_graph()
    graph_input = {"messages": [{"role": "user", "content": content}]}
    full_reply = ""

    try:
        async for msg, metadata in graph.astream(
            graph_input, config, stream_mode="messages"
        ):
            if hasattr(msg, "content") and msg.content:
                token = msg.content
                full_reply += token
                yield _sse("chat_chunk", {"content": token})
    except Exception:
        logger.error(
            "chat_reply_failed", conv_id=str(conversation_id),
            user_id=str(user_id), exc_info=True,
        )
        yield _sse("error", {"message": "生成回复失败，请重试"})
        return

    # Save assistant message
    if full_reply:
        await conversation_service.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content=full_reply,
            message_type="text",
            parent_message_id=user_message_id,
            model=model,
            metadata={"mode": mode},
        )


# ── Research mode ──────────────────────────────────────────────────────


async def _run_research(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    user_message_id: uuid.UUID,
    config: dict,
    model: str | None,
    mode: Literal["chat", "research"],
) -> AsyncGenerator[dict, None]:
    """Run the full research pipeline via the research graph."""
    logger.info("research_flow_started", task_id="", conv_id=str(conversation_id))

    task_id_str = ""
    try:
        # Create task
        task = await task_service.create_task(user_id, content)
        task_id_str = str(task.id)
        logger.info("research_task_created", task_id=task_id_str)
    except Exception as e:
        logger.error("research_task_create_failed", conv_id=str(conversation_id), exc_info=True)
        yield _sse("error", {"message": f"创建研究任务失败: {e}", "taskId": ""})
        return

    # ── Clarity check (decision 1: planner outside graph) ──
    try:
        logger.info("research_clarity_check_start", task_id=task_id_str)
        clarity = await research_service.check_clarity(content)
        logger.info("research_clarity_check_done", task_id=task_id_str, is_clear=clarity.get("is_clear"))
    except Exception as e:
        if _is_thinking_conflict(e):
            yield _sse("error", {
                "message": "深度思考模式不支持结构化输出，请关闭深度思考开关后重试",
                "code": "THINKING_CONFLICT", "taskId": task_id_str,
            })
            return
        raise

    if not clarity.get("is_clear"):
        # Save as a text message so the frontend shows the clarification
        clarifying_question = clarity.get("clarifying_question", "")
        clarify_msg = await conversation_service.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content=clarifying_question or "请进一步描述您的研究主题",
            message_type="text",
            parent_message_id=user_message_id,
            model=model,
            metadata={"mode": mode, "status": "needs_clarification", "task_id": task_id_str},
        )
        yield _sse("chat_chunk", {
            "messageId": str(clarify_msg.id),
            "content": clarifying_question or "请进一步描述您的研究主题",
            "taskId": task_id_str,
        })
        return

    # ── Generate plan (outside graph — decision 1) ──
    plan_state = {
        "topic": content, "task_id": task_id_str,
        "user_id": str(user_id), "conversation_id": str(conversation_id),
    }
    try:
        logger.info("research_planner_start", task_id=task_id_str)
        plan_state = await planner.run(plan_state)
        logger.info("research_planner_done", task_id=task_id_str)
    except Exception as e:
        if _is_thinking_conflict(e):
            yield _sse("error", {
                "message": "深度思考模式不支持结构化输出，请关闭深度思考开关后重试",
                "code": "THINKING_CONFLICT", "taskId": task_id_str,
            })
            return
        raise
    plan = plan_state.get("research_plan") or {}

    # Persist plan to MongoDB
    if plan:
        try:
            await task_service.store_stage_output(uuid.UUID(task_id_str), "plan", plan)
        except Exception:
            logger.warning("plan_store_failed", task_id=task_id_str, exc_info=True)

    # ── Emit plan_card and pause for user ──
    questions = [
        {"id": q.get("id", ""), "question": q.get("question", "")}
        for q in plan.get("research_questions", []) if isinstance(q, dict)
    ]
    keywords = [
        k.get("keyword", "") for k in plan.get("search_keywords", [])
        if isinstance(k, dict) and k.get("keyword")
    ]
    plan_msg = await conversation_service.save_message(
        conversation_id=conversation_id, role="assistant", content="",
        message_type="plan_card", parent_message_id=user_message_id, model=model,
        metadata={
            "task_id": task_id_str, "questions": questions,
            "keywords": keywords, "status": "pending_confirmation", "mode": mode,
        },
    )
    yield _sse("plan_generated", {
        "messageId": str(plan_msg.id),
        "taskId": task_id_str, "status": "pending_confirmation",
        "questions": questions, "keywords": keywords,
        "planText": plan.get("topic", content),
        "clarifyingQuestion": "", "model": model,
    })

    # ── Wait for user action (asyncio.Event — decision 4) ──
    plan_waiter = asyncio.Event()
    plan_msg_id_str = str(plan_msg.id)
    _plan_events[plan_msg_id_str] = plan_waiter
    try:
        await asyncio.wait_for(plan_waiter.wait(), timeout=300.0)
    except TimeoutError:
        _plan_events.pop(plan_msg_id_str, None)
        yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
        return
    action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
    action_type = action.get("action", "timeout")

    if action_type == "reject" or action_type == "timeout":
        yield _sse("plan_action", {"taskId": task_id_str, "action": action_type})
        return

    # ── Handle modify (decision 11: B-plan router) ──
    if action_type == "modify":
        try:
            modifications = (action.get("modifications") or "").strip()
            mode_decision = await research_service.classify_modification(plan, modifications)
        except Exception as e:
            if _is_thinking_conflict(e):
                yield _sse("error", {
                    "message": "深度思考模式不支持结构化输出，请关闭深度思考开关后重试",
                    "code": "THINKING_CONFLICT", "taskId": task_id_str,
                })
                return
            raise
        if mode_decision == "revise":
            try:
                # Revise: re-run planner in revise mode.
                # 用户修改意图同样需透传给下游（与 augment 对称）。
                plan_state["plan_to_revise"] = plan
                plan_state["modification_directive"] = modifications
                plan_state["user_focus_notes"] = modifications
                plan_state = await planner.run(plan_state)
            except Exception as e:
                if _is_thinking_conflict(e):
                    yield _sse("error", {
                        "message": "深度思考模式不支持结构化输出，请关闭深度思考开关后重试",
                        "code": "THINKING_CONFLICT", "taskId": task_id_str,
                    })
                    return
                raise
            plan = plan_state.get("research_plan") or {}
            # Re-emit revised plan_card on same message
            questions = [
                {"id": q.get("id", ""), "question": q.get("question", "")}
                for q in plan.get("research_questions", []) if isinstance(q, dict)
            ]
            keywords = [
                k.get("keyword", "") for k in plan.get("search_keywords", [])
                if isinstance(k, dict) and k.get("keyword")
            ]
            yield _sse("plan_generated", {
                "messageId": plan_msg_id_str, "taskId": task_id_str,
                "status": "revised", "questions": questions, "keywords": keywords,
                "planText": plan.get("topic", content),
            })
            # Re-wait for user on revised plan
            plan_waiter = asyncio.Event()
            _plan_events[plan_msg_id_str] = plan_waiter
            try:
                await asyncio.wait_for(plan_waiter.wait(), timeout=300.0)
            except TimeoutError:
                _plan_events.pop(plan_msg_id_str, None)
                yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
                return
            action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
            action_type = action.get("action", "timeout")
            if action_type in ("reject", "timeout"):
                yield _sse("plan_action", {"taskId": task_id_str, "action": action_type})
                return
        else:
            # Augment: set user_focus_notes for Writer/Synthesizer.
            # 不在这里发 SSE；后续主分支统一发带 augment 信息的 accept。
            plan_state["user_focus_notes"] = modifications

    # ── Start graph from retriever (plan confirmed) ──
    augment_notes = plan_state.get("user_focus_notes", "")
    if augment_notes:
        logger.info("research_graph_start", task_id=task_id_str, mode="augment")
        yield _sse("plan_action", {
            "taskId": task_id_str, "action": "accept",
            "modify_outcome": "augment", "user_focus_notes": augment_notes,
        })
    else:
        logger.info("research_graph_start", task_id=task_id_str)
        yield _sse("plan_action", {"taskId": task_id_str, "action": "accept"})
    graph_input = {
        "task_id": task_id_str,
        "user_id": str(user_id),
        "conversation_id": str(conversation_id),
        "research_plan": plan,
        "user_focus_notes": plan_state.get("user_focus_notes", ""),
        "analysis_round": 1,
        "all_retrieval_results": [],
    }
    try:
        async for chunk in get_research_graph().astream(
            graph_input, config, stream_mode="updates",
        ):
            # LangGraph 1.2.4 在 interrupt() 时抛 GraphInterrupt 是内部
            # 抑制型异常；updates 模式下 astream 不抛，而是发一个
            # {"__interrupt__": (Interrupt(value=..., id=...),)} chunk 后
            # 正常结束流。这里识别该 chunk，提取 payload 通知前端。
            if "__interrupt__" in chunk:
                interrupts = chunk["__interrupt__"]
                if interrupts:
                    payload = interrupts[0].value
                    if isinstance(payload, dict) and payload.get("type") == "gap_question":
                        gaps = payload.get("gaps") or []
                        data = {"taskId": task_id_str, "gaps": gaps, "round": payload.get("round", 1),
                               "status": "pending"}
                        msg = await conversation_service.save_message(
                            conversation_id=conversation_id, role="assistant", content="",
                            message_type="gap_question", parent_message_id=user_message_id, model=model,
                            metadata={
                                "taskId": task_id_str, "gaps": gaps,
                                "round": data["round"], "status": "pending", "mode": mode,
                            },
                        )
                        data["messageId"] = str(msg.id)
                        yield _sse("gap_question", data)
                return
            async for event in _process_research_chunk(
                chunk, conversation_id, user_message_id, model, mode, task_id_str,
            ):
                yield event
    except GraphInterrupt as gi:
        # 防御性兜底：未来 LangGraph 版本若恢复抛异常机制。
        # gi.args[0] 在抛出路径下是 (Interrupt(...),) tuple。
        interrupts = gi.args[0] if gi.args else ()
        payload = interrupts[0].value if interrupts else {}
        if isinstance(payload, dict) and payload.get("type") == "gap_question":
            gaps = payload.get("gaps") or []
            data = {"taskId": task_id_str, "gaps": gaps, "round": payload.get("round", 1),
                "status": "pending"}
            msg = await conversation_service.save_message(
                conversation_id=conversation_id, role="assistant", content="",
                message_type="gap_question", parent_message_id=user_message_id, model=model,
                metadata={
                    "taskId": task_id_str, "gaps": gaps,
                    "round": data["round"], "status": "pending", "mode": mode,
                },
            )
            data["messageId"] = str(msg.id)
            yield _sse("gap_question", data)
        return
    except Exception as e:
        if _is_thinking_conflict(e):
            yield _sse("error", {
                "message": "深度思考模式不支持结构化输出，请关闭深度思考开关后重试",
                "code": "THINKING_CONFLICT", "taskId": task_id_str,
            })
        else:
            logger.error(
                "research_failed", conv_id=str(conversation_id), user_id=str(user_id), exc_info=True,
            )
            yield _sse("error", {"message": "研究流程执行失败", "taskId": task_id_str})
        return


async def _process_research_chunk(
    chunk: dict,
    conversation_id: uuid.UUID,
    parent_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
    task_id_str: str,
) -> AsyncGenerator[dict, None]:
    """Convert a graph node update into SSE events + persist message cards."""
    for node_name, node_state in chunk.items():
        if not isinstance(node_state, dict):
            continue

        if node_name == "retriever":
            results = node_state.get("retrieval_results") or []
            all_results = node_state.get("all_retrieval_results") or []
            yield _sse("phase_change", {"phase": "retrieving", "message": PHASE_LABELS["retriever"]})
            data = {
                "taskId": task_id_str,
                "round": node_state.get("analysis_round", 1),
                "sourceCount": len(all_results),
                "roundCount": len(results),
                "sources": summarize_sources(all_results),
            }
            msg = await conversation_service.save_message(
                conversation_id=conversation_id,
                role="assistant", content="",
                message_type="retrieval_card",
                parent_message_id=parent_message_id, model=model,
                metadata={
                    "task_id": task_id_str,
                    "round": data["round"],
                    "source_count": data["sourceCount"],
                    "sources": data["sources"],
                    "mode": mode,
                },
            )
            data["messageId"] = str(msg.id)
            yield _sse("retrieval_complete", data)

        elif node_name == "analyzer":
            yield _sse("phase_change", {"phase": "analyzing", "message": PHASE_LABELS["analyzer"]})
            yield _sse("analysis_complete", {
                "taskId": task_id_str,
                "gapCount": len(node_state.get("knowledge_gaps") or []),
                "round": node_state.get("analysis_round", 1),
            })

        elif node_name == "synthesizer":
            yield _sse("phase_change", {"phase": "synthesizing", "message": PHASE_LABELS["synthesizer"]})

        elif node_name == "writer":
            report = node_state.get("final_report")
            if report:
                yield _sse("phase_change", {"phase": "writing", "message": PHASE_LABELS["writer"]})
                data = {
                    "messageId": "",
                    "taskId": task_id_str,
                    "reportId": report.get("report_id") or report.get("id"),
                    "model": model,
                    "title": report.get("title", "研究报告"),
                    "abstract": report.get("abstract", ""),
                    "sections": report.get("sections", []),
                    "citations": report.get("citations", []),
                    "gapNotes": report.get("gap_notes", ""),
                }
                msg = await conversation_service.save_message(
                    conversation_id=conversation_id,
                    role="assistant",
                    content=data["abstract"],
                    message_type="report_card",
                    parent_message_id=parent_message_id, model=model,
                    metadata={
                        "task_id": task_id_str,
                        "report_id": data["reportId"],
                        "title": data["title"],
                        "sections": data["sections"],
                        "citations": data["citations"],
                        "gap_notes": data["gapNotes"],
                        "mode": mode,
                    },
                )
                data["messageId"] = str(msg.id)
                yield _sse("report_complete", data)

        else:
            logger.debug("unhandled_graph_node", node=node_name)
