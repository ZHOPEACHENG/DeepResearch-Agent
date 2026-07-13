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
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from backend.core.config import settings
from backend.core.database import get_postgres_session
from backend.models.conversation import Message
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

# ── Clarity pause (asyncio.Event — same pattern as plan-action) ─────
_clarify_events: dict[str, asyncio.Event] = {}
_clarify_responses: dict[str, str] = {}


async def resume_plan_action(
    message_id: uuid.UUID, action: str, modifications: str | None = None,
) -> None:
    """Unblock the plan-action asyncio.Event (decision 4)."""
    _plan_actions[str(message_id)] = {"action": action, "modifications": modifications}
    event = _plan_events.pop(str(message_id), None)
    if event:
        event.set()
    # Persist status so the card doesn't revert to pending on reload
    status_map = {"accept": "accepted", "modify": "revised", "reject": "rejected"}
    try:
        await conversation_service.update_message_metadata(
            message_id, {"status": status_map.get(action, action)},
        )
    except Exception:
        logger.warning("plan_action_status_persist_failed", exc_info=True)


async def set_clarity_response(task_id: str, response: str) -> None:
    """Unblock the clarity-check pause so the pipeline continues."""
    _clarify_responses[task_id] = response
    event = _clarify_events.pop(task_id, None)
    if event:
        event.set()
    # Persist status so the card shows "已澄清" on reload
    try:
        await _update_message_status_by_task(task_id, "clarifying_question", "clarified")
    except Exception:
        logger.warning("clarity_status_persist_failed", exc_info=True)


# ── Public API ──────────────────────────────────────────────────────────


async def handle_message(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    *,
    parent_message_id: uuid.UUID | None = None,
    model: str | None = None,
    mode: Literal["chat", "research"] = "chat",
) -> AsyncGenerator[dict, None]:
    """Entry point: save user message, dispatch to chat or research graph.

    Yields SSE event dicts: ``message_created`` → stream events → ``done``.
    """
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



async def _update_message_status_by_task(
    task_id: str, message_type: str, status: str,
) -> None:
    """Find the most recent message of *message_type* for *task_id* and set its status."""
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(Message.id)
            .where(
                Message.message_type == message_type,
                Message.extra["taskId"].as_string() == str(task_id),
            )
            .order_by(Message.created_at.desc())
            .limit(1),
        )
        msg_id = result.scalar_one_or_none()
        if msg_id is None:
            return
        result2 = await session.execute(select(Message).where(Message.id == msg_id))
        msg = result2.scalar_one_or_none()
        if msg is None:
            return
        current = dict(msg.extra or {})
        current["status"] = status
        msg.extra = current
        flag_modified(msg, "extra")  # force SQLAlchemy to detect JSONB change
        await session.commit()


async def _resolve_gap_parent(
    conversation_id: uuid.UUID, task_id: str,
) -> uuid.UUID:
    """Find the parent_message_id of the gap_question card for a task.

    Falls back to the nil UUID if no gap_question is found (should not happen
    in normal flow — resume_gap_action is only called after a gap_question
    was already persisted).
    """
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(Message.parent_message_id)
            .where(
                Message.conversation_id == conversation_id,
                Message.message_type == "gap_question",
                Message.extra["taskId"].as_string() == task_id,
            )
            .order_by(Message.created_at.desc())
            .limit(1),
        )
        parent_id = result.scalar_one_or_none()
        if parent_id is None:
            logger.warning(
                "gap_parent_not_found",
                conv_id=str(conversation_id),
                task_id=task_id,
            )
            return uuid.UUID(int=0)
        return parent_id


async def resume_gap_action(
    conversation_id: uuid.UUID, user_id: uuid.UUID, task_id: str, action: str,
) -> None:
    """Resume the research graph after a gap_confirm interrupt."""
    if action not in ("answer", "skip"):
        logger.error("resume_gap_bad_action", task_id=task_id, action=action)
        raise ValueError(f"无效的 gap action: {action}")

    logger.info("resume_gap", task_id=task_id, action=action)

    try:
        graph = get_research_graph()
        config = {"configurable": {"thread_id": str(conversation_id)}}

        # Resolve the correct parent_message_id for new messages produced by the
        # resumed graph.  We use the gap_question card's own parent (the user
        # message that started the research) so the FK constraint passes.
        parent_id = await _resolve_gap_parent(conversation_id, task_id)

        async for chunk in graph.astream(
            Command(resume={"action": action}), config, stream_mode="updates",
        ):
            # The resumed graph may hit another interrupt() if gaps persist
            # after a follow-up retrieval.  We must detect and persist the
            # new gap_question card here (same pattern as _run_research).
            if "__interrupt__" in chunk:
                interrupts = chunk["__interrupt__"]
                if interrupts:
                    payload = interrupts[0].value
                    if isinstance(payload, dict) and payload.get("type") == "gap_question":
                        gaps = payload.get("gaps") or []
                        rnd = payload.get("round", 1)
                        await conversation_service.save_message(
                            conversation_id=conversation_id,
                            role="assistant", content="",
                            message_type="gap_question",
                            parent_message_id=parent_id, model=None,
                            metadata={
                                "taskId": task_id, "gaps": gaps,
                                "round": rnd, "status": "pending", "mode": "research",
                            },
                        )
                        logger.info(
                            "resume_gap_interrupted_again",
                            task_id=task_id, gaps=len(gaps), round=rnd,
                        )
                # Graph is paused again; caller will return and the frontend
                # reload will pick up the new gap_question + intermediate cards.
                return
            async for _ in _process_research_chunk(
                chunk, conversation_id, user_id, parent_id, None, "research", task_id,
            ):
                pass  # messages persisted to DB, no SSE client connected
    except Exception:
        logger.error(
            "resume_gap_failed",
            task_id=task_id,
            conv_id=str(conversation_id),
            exc_info=True,
        )
        raise


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
    """Stream an LLM reply using the chat graph. History is loaded from the DB
    so the model has full context — including research cards — from prior turns."""
    graph = get_chat_graph()

    # Load existing messages so the model sees the full conversation,
    # not just the latest user message.  Research results are saved as
    # message cards (analysis_card, report_card, etc.) and must be in
    # context for the model to reference them.
    prior: list[dict] = []
    try:
        result = await conversation_service.get_messages(
            conversation_id, limit=settings.max_context_tokens // 4,
        )
        # Convert Message models to LangChain dict format.
        for m in result.get("items", []):
            msg_type = getattr(m, "message_type", "text") if hasattr(m, "message_type") else "text"
            role = getattr(m, "role", "user")
            content_text = getattr(m, "content", "")
            extra = getattr(m, "extra", {}) if hasattr(m, "extra") else {}
            # Build a descriptive text for card-type messages so the LLM can
            # understand the research output without the visual card UI.
            if msg_type == "report_card":
                title = extra.get("title", "研究报告")
                abstract = extra.get("abstract", "") or content_text
                sections = extra.get("sections", []) or []
                parts = [f"[研究报告] {title}"]
                if abstract:
                    parts.append(f"摘要: {abstract}")
                for s in sections:
                    h = s.get("heading", "")
                    c = s.get("content", "")
                    if h and c:
                        parts.append(f"\n## {h}\n{c}")
                content_text = "\n".join(parts)
            elif msg_type == "analysis_card":
                summary = extra.get("summary_preview", "") or ""
                gap_count = extra.get("gap_count", 0)
                content_text = f"[分析结果] 发现 {gap_count} 个知识缺口\n{summary}"
            elif msg_type == "retrieval_card":
                src_count = extra.get("source_count", 0)
                sources = extra.get("sources", []) or []
                lines = [f"[资料检索] 共检索到 {src_count} 条来源:"]
                for s in sources[:10]:
                    lines.append(f"- {s.get('title', '')} ({s.get('sourceType', '')})")
                content_text = "\n".join(lines)
            elif msg_type in ("gap_question", "clarifying_question"):
                # Skip — these are UI control messages, not LLM context
                continue
            elif role == "user" and not content_text:
                content_text = content  # fallback
            if content_text:
                prior.append({"role": role, "content": content_text[:4000]})
    except Exception:
        logger.warning(
            "chat_history_load_failed",
            conv_id=str(conversation_id),
            exc_info=True,
        )

    # Truncate to fit context window — roughly count tokens as chars/2.
    total_chars = sum(len(m["content"]) for m in prior)
    max_chars = (settings.max_context_tokens or 128000) * 2
    if total_chars > max_chars:
        # Keep the most recent messages within budget
        kept: list[dict] = []
        kept_chars = 0
        for m in reversed(prior):
            if kept_chars + len(m["content"]) > max_chars:
                break
            kept.insert(0, m)
            kept_chars += len(m["content"])
        prior = kept

    # The new user message goes last; add_messages prevents duplication.
    graph_input = {"messages": prior + [{"role": "user", "content": content}]}
    logger.info(
        "chat_context_loaded",
        conv_id=str(conversation_id),
        prior_messages=len(prior),
        total_chars=sum(len(m["content"]) for m in graph_input["messages"]),
    )
    full_reply = ""

    try:
        async for msg, _metadata in graph.astream(
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
    # Create task
    try:
        task = await task_service.create_task(user_id, content)
    except ValueError as e:
        yield _sse("error", {"message": str(e), "taskId": ""})
        return
    task_id_str = str(task.id)

    # ── Clarity check loop (decision 1: planner outside graph) ──
    # 主题不够清晰时，暂停并等待用户在对话框里回复细化内容，
    # 收到回复后重新检查，最多重试 2 轮。
    # 第 3 次检查无论结果如何都直接通过，防止无限循环。
    topic = content
    for clarity_round in range(3):
        try:
            clarity = await planner.check_clarity(topic)
        except Exception:
            logger.warning("clarity_check_failed", exc_info=True)
            clarity = {"is_clear": True}  # 检查失败就跳过澄清，直接执行
        if clarity.get("is_clear"):
            break  # 主题已清晰，进入计划生成
        if clarity_round >= 2:
            # 最后一轮，即使模型认为不清晰也强制通过
            logger.info(
                "clarity_max_rounds_reached",
                task_id=task_id_str,
                topic=topic[:200],
            )
            break

        question_text = clarity.get("clarifying_question", "") or "请进一步描述您的研究主题"

        # 组合模型的完整分析：先总结它理解的，再列出模糊点，最后提问
        understood = clarity.get("understood", "")
        unclear = clarity.get("unclear_aspects", []) or []
        parts = [question_text]
        if understood:
            parts.insert(0, f"我理解你想研究：{understood}")
        if unclear:
            parts.insert(-1 if understood else 0,
                         "以下方面需要进一步明确：\n" + "\n".join(f"  • {p}" for p in unclear))
        clarifying_content = "\n\n".join(parts)

        parent_msg_id = user_message_id
        if clarity_round > 0:
            parent_msg_id = (
                await conversation_service.save_message(
                    conversation_id=conversation_id,
                    role="user",
                    content=topic,
                    message_type="text",
                    model=model,
                    metadata={"mode": mode, "task_id": task_id_str},
                )
            ).id

        clarify_msg = await conversation_service.save_message(
            conversation_id=conversation_id,
            role="assistant",
            content=clarifying_content,
            message_type="clarifying_question",
            parent_message_id=parent_msg_id,
            model=model,
            metadata={"mode": mode, "task_id": task_id_str},
        )
        yield _sse("clarity_question", {
            "messageId": str(clarify_msg.id),
            "content": clarifying_content,
            "taskId": task_id_str,
        })

        # ── 暂停 SSE 流，等用户在对话框里回复 ──
        clarify_waiter = asyncio.Event()
        _clarify_events[task_id_str] = clarify_waiter
        try:
            await asyncio.wait_for(clarify_waiter.wait(), timeout=300.0)
        except TimeoutError:
            yield _sse("error", {
                "message": "等待澄清回复超时，请重新发起研究",
                "taskId": task_id_str,
            })
            return
        finally:
            _clarify_events.pop(task_id_str, None)
        clarification = _clarify_responses.pop(task_id_str, "").strip()
        if not clarification:
            yield _sse("error", {
                "message": "未收到澄清回复，请重新发起研究",
                "taskId": task_id_str,
            })
            return
        # Accumulate clarifications so the model sees the full context
        topic = f"{topic} —— {clarification}"

    # ── Generate plan (outside graph — decision 1) ──
    plan_state = {
        "topic": topic, "task_id": task_id_str,
        "user_id": str(user_id), "conversation_id": str(conversation_id),
    }
    try:
        plan_state = await planner.run(plan_state)
    except Exception:
        logger.error("planner_run_failed", task_id=task_id_str, exc_info=True)
        yield _sse("error", {"message": "研究计划生成失败，请重试", "taskId": task_id_str})
        return
    if plan_state is None:
        logger.error("planner_returned_none", task_id=task_id_str)
        yield _sse("error", {"message": "研究计划生成失败，请重试", "taskId": task_id_str})
        return
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
        yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
        return
    finally:
        _plan_events.pop(plan_msg_id_str, None)
    action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
    action_type = action.get("action", "timeout")

    if action_type == "reject" or action_type == "timeout":
        yield _sse("plan_action", {"taskId": task_id_str, "action": action_type})
        return

    # ── Handle modify (decision 11: B-plan router) ──
    if action_type == "modify":
        modifications = (action.get("modifications") or "").strip()
        mode_decision = await research_service.classify_modification(plan, modifications)
        if mode_decision == "revise":
            # Revise: re-run planner in revise mode
            plan_state["plan_to_revise"] = plan
            plan_state["modification_directive"] = modifications
            plan_state = await planner.run(plan_state)
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
                yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
                return
            finally:
                _plan_events.pop(plan_msg_id_str, None)
            action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
            action_type = action.get("action", "timeout")
            if action_type in ("reject", "timeout"):
                yield _sse("plan_action", {"taskId": task_id_str, "action": action_type})
                return
        else:
            # Augment: set user_focus_notes for Writer/Synthesizer
            plan_state["user_focus_notes"] = modifications
            yield _sse("plan_action", {
                "taskId": task_id_str, "action": "modify", "modifyOutcome": "augment",
                "userFocusNotes": modifications,
            })
            # Persist so the card shows "已采纳补充" on reload
            try:
                await conversation_service.update_message_metadata(
                    uuid.UUID(plan_msg_id_str),
                    {"status": "accepted_with_notes", "user_focus_notes": modifications},
                )
            except Exception:
                logger.warning("augment_status_persist_failed", exc_info=True)

    # ── Start graph from retriever (plan confirmed) ──
    yield _sse("plan_action", {"taskId": task_id_str, "action": "accept"})

    # Transition task to running for dashboard stats (T120)
    try:
        await task_service.update_task_status(task.id, "running", user_id=user_id)
    except Exception:
        logger.warning("task_running_transition_failed", task_id=task_id_str, exc_info=True)
    graph_input = {
        "task_id": task_id_str,
        "user_id": str(user_id),
        "conversation_id": str(conversation_id),
        "research_plan": plan,
        "user_focus_notes": plan_state.get("user_focus_notes", ""),
        "analysis_round": 1,
        "all_retrieval_results": [],
    }
    # Emit first phase label BEFORE graph execution so the frontend shows
    # "正在检索..." while the retriever is actually running (not after).
    yield _sse("phase_change", {"phase": "retrieving", "message": PHASE_LABELS["retriever"]})

    try:
        async for chunk in get_research_graph().astream(
            graph_input, config, stream_mode="updates",
        ):
            # LangGraph 1.2.4: interrupt() 不抛 GraphInterrupt，而是在
            # updates 流里插入 {"__interrupt__": (Interrupt(value=...),)}
            # 作为最后一个 chunk。必须在这里识别，否则间隙问题卡片永不发出。
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
                chunk, conversation_id, user_id, user_message_id, model, mode, task_id_str,
            ):
                yield event
    except GraphInterrupt as gi:
        # 防御性兜底 — LangGraph 未来版本可能恢复抛异常机制
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
        # Graph finished normally — mark task as completed
        try:
            await task_service.update_task_status(task.id, "completed", user_id=user_id)
        except Exception:
            logger.warning("task_complete_transition_failed", task_id=task_id_str, exc_info=True)
        return
    except Exception:
        logger.error(
            "research_failed", conv_id=str(conversation_id), user_id=str(user_id), exc_info=True,
        )
        # Mark task as failed on pipeline error
        try:
            await task_service.update_task_status(task.id, "failed", user_id=user_id,
                                                  error_message="研究流程执行失败")
        except Exception:
            logger.warning("task_failed_transition_failed", task_id=task_id_str, exc_info=True)
        yield _sse("error", {"message": "研究流程执行失败", "taskId": task_id_str})
        return


async def _process_research_chunk(
    chunk: dict,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    parent_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
    task_id_str: str,
) -> AsyncGenerator[dict, None]:
    """Convert a graph node update into SSE events + persist message cards."""
    for node_name, node_state in chunk.items():
        if node_name == "__interrupt__":
            # Handled by the outer astream loop; skip here.  A tuple of
            # Interrupt objects isn't a dict and would be silently skipped
            # by the isinstance guard below, but an explicit continue is
            # clearer and prevents accidental propagation.
            continue
        if not isinstance(node_state, dict):
            continue

        if node_name == "retriever":
            results = node_state.get("retrieval_results") or []
            all_results = node_state.get("all_retrieval_results") or []
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
            # Tee up the next phase label so the spinner updates before the
            # analyzer results arrive (which may take 30+ seconds).
            yield _sse("phase_change", {"phase": "analyzing", "message": PHASE_LABELS["analyzer"]})

        elif node_name == "analyzer":
            summary = node_state.get("knowledge_summary") or {}
            gaps = node_state.get("knowledge_gaps") or []
            gap_count = len(gaps)
            round_num = node_state.get("analysis_round", 1)
            # Persist a visible analysis_card so the user sees something in the
            # chat — not just a phase-label change in the status bar.
            analysis_card_data = {
                "taskId": task_id_str,
                "round": round_num,
                "gapCount": gap_count,
                "criticalCount": sum(1 for g in gaps if g.get("severity") == "critical"),
                "summaryPreview": (summary.get("content") or summary.get("summary_content") or ""),
            }
            msg = await conversation_service.save_message(
                conversation_id=conversation_id,
                role="assistant", content="",
                message_type="analysis_card",
                parent_message_id=parent_message_id, model=model,
                metadata={
                    "task_id": task_id_str,
                    "round": round_num,
                    "gap_count": gap_count,
                    "critical_count": analysis_card_data["criticalCount"],
                    "summary_preview": analysis_card_data["summaryPreview"],
                    "mode": mode,
                },
            )
            analysis_card_data["messageId"] = str(msg.id)
            yield _sse("analysis_complete", analysis_card_data)
            # Tee up synthesizer phase — only if no critical gaps found
            # (gap_confirm node will handle its own messaging if gaps exist)
            if not any(g.get("severity") == "critical" for g in gaps):
                yield _sse("phase_change", {"phase": "synthesizing", "message": PHASE_LABELS["synthesizer"]})

        elif node_name == "synthesizer":
            # Synthesizer is fast — after it finishes, tee up writer
            yield _sse("phase_change", {"phase": "writing", "message": PHASE_LABELS["writer"]})

        elif node_name == "writer":
            report = node_state.get("final_report")
            if report:
                data = {
                    "messageId": "",
                    "taskId": task_id_str,
                    "reportId": report.get("report_id") or report.get("id"),
                    "model": model,
                    "title": report.get("title", "研究报告"),
                    "abstract": report.get("abstract", ""),
                    "sections": report.get("sections", []),
                    "citations": report.get("citations", []),
                    "gap_notes": report.get("gap_notes", ""),
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
                        "gap_notes": data["gap_notes"],
                        "mode": mode,
                    },
                )
                data["messageId"] = str(msg.id)
                yield _sse("report_complete", data)

                # ── Auto-ingest report into knowledge base ──────────
                asyncio.create_task(_ingest_report_to_kb(
                    user_id=user_id,
                    title=report.get("title", "研究报告"),
                    abstract=report.get("abstract", ""),
                    sections=report.get("sections", []),
                    gap_notes=report.get("gap_notes", ""),
                ))

        else:
            logger.debug("unhandled_graph_node", node=node_name)


# ── Report → Knowledge Base auto-ingestion ───────────────────────────


async def _ingest_report_to_kb(
    user_id: uuid.UUID,
    title: str,
    abstract: str,
    sections: list[dict],
    gap_notes: str = "",
) -> None:
    """Convert a finished research report to plain text and index it into
    the knowledge base so it becomes searchable and QA-able alongside
    user-uploaded documents."""
    try:
        # Build a plain-text representation of the report
        parts = [f"# {title}", "", abstract, ""]
        for sec in sections:
            heading = sec.get("heading", "")
            content = sec.get("content", "")
            if heading or content:
                parts.append(f"## {heading}" if heading else "")
                parts.append(content)
                parts.append("")
        if gap_notes:
            parts.append("## 知识缺口说明")
            parts.append(gap_notes)

        text = "\n".join(parts).strip()

        from backend.services import knowledge_service
        doc_id = await knowledge_service.ingest_text(
            user_id=user_id, content=text, title=title,
            source_label="research_report",
        )
        logger.info("report_ingested_to_kb", title=title[:80],
                    doc_id=str(doc_id) if doc_id else "skipped",
                    char_count=len(text))
    except Exception:
        logger.error("report_ingest_to_kb_failed", title=title[:80],
                     exc_info=True)
