"""
ChatService — orchestration hub for conversational research.

Handles the full lifecycle of a user message:
1. Save user Message
2. Branch by user-selected mode (chat | research) — NO LLM intent routing.
   - chat: load history → stream LLM reply → save assistant Message
   - research: create ResearchTask → run Planner agent → emit plan_card →
     pause for user accept/modify/reject (plan_confirmation) → run the full
     LangGraph pipeline (retrieve → analyze → gap loop → synthesize → write),
     mirroring each stage as a chat message card.

The mode is chosen explicitly by the user via the "深度研究" toggle in the UI,
not inferred by an LLM. This avoids silent misclassification and the extra
per-message LLM round-trip the old IntentRouter incurred.

Resilience (network interrupts): the research pipeline runs as a background
``asyncio.Task`` that pushes SSE events into a per-conversation
``asyncio.Queue``.  The SSE generator reads from that queue.  When the HTTP
client disconnects the background task *keeps running* and persists results
to the database.  A ``resume`` endpoint reconnects a new SSE stream to the
same queue so the user can watch progress after reconnecting.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any, Literal

from sqlalchemy import select

from backend.core.database import get_postgres_session
from backend.models.conversation import Message
from backend.services import conversation_service, research_service, task_service
from backend.tools.llm import get_llm_provider
from backend.utils.logging import get_logger
from backend.utils.sse import sse_event as _sse

logger = get_logger(__name__)

# ── User-intervention pauses (plan / gap) ──────────────────────────────
# Per-message asyncio Events.  Key: message_id (str).
_plan_events: dict[str, asyncio.Event] = {}
_plan_actions: dict[str, dict] = {}  # message_id → {action, modifications}

# ── Background-task resilience (network-interrupt survival) ────────────
# When a research pipeline is running, it pushes events into a per-conversation
# Queue.  The SSE generator reads from the queue.  If the SSE client
# disconnects the background task keeps running; a later resume call reconnects
# a new SSE stream to the SAME queue.
#
# Keyed by conv_id_str.
_research_queues: dict[str, asyncio.Queue] = {}       # conv_id_str → Queue[dict|None]
_research_bg_tasks: dict[str, asyncio.Task] = {}       # conv_id_str → Task
# Plan-action context retained so set_plan_action can restart the pipeline
# when the original SSE waiter is dead (disconnected before accepting).
_retained_plan_ctx: dict[str, dict] = {}  # plan_msg_id → {conv_id, user_id, plan_state, ...}


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
    """
    Process a user message and yield SSE event dicts.

    Caller (API endpoint) iterates this generator and writes each dict
    as an SSE event to the client connection.

    Args:
        mode: User-selected mode — "chat" (default) streams a conversational
            reply; "research" starts the research pipeline and emits a plan.
            Replaces the former LLM-based IntentRouter classification.

    Yields:
        {"event": "message_created", "data": {...}}
        {"event": "chat_chunk" | "plan_generated" | ..., "data": {...}}
        {"event": "done", "data": {...}}
        {"event": "error", "data": {...}}
        :param conversation_id:
        :param user_id:
        :param content:
        :param parent_message_id:
        :param mode:
        :param model:
    """
    logger.info(
        "message_received",
        conv_id=str(conversation_id),
        user_id=str(user_id),
        model=model,
        preview=content[:80],
    )

    # 1. Auto-generate title from first user message
    # （内部有 guard：仅当标题仍是 "New Conversation" 时才生效）
    await conversation_service.auto_generate_title(
        conversation_id, user_id, content,
    )

    # 2. Save user message
    user_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="user",
        content=content,
        message_type="text",
        parent_message_id=parent_message_id,
        model=model,
    )
    yield _sse("message_created", {"messageId": str(user_msg.id)})

    # 3. Branch by user-selected mode (replaces the former LLM intent routing)
    if mode == "research":
        conv_id_str = str(conversation_id)

        # Guard: refuse to start a second research pipeline while one is
        # already active for this conversation (would corrupt queue/task).
        if conv_id_str in _research_bg_tasks:
            existing = _research_bg_tasks[conv_id_str]
            if not existing.done():
                logger.warning(
                    "research_already_active",
                    conv_id=conv_id_str,
                )
                yield _sse("error", {"message": "该对话已有正在运行的研究，请等待完成"})
                yield _sse("done", {"conversationId": conv_id_str, "model": model})
                return

        # ── Background-task resilience ──────────────────────────────────
        # The research pipeline runs in a background asyncio.Task and pushes
        # events into a Queue.  The SSE generator merely reads the queue.
        # When the HTTP client disconnects (CancelledError) the bg task keeps
        # running; a later resume call creates a new SSE stream to the same
        # queue.  The queue is removed when the bg task completes normally.
        queue: asyncio.Queue = asyncio.Queue()
        _research_queues[conv_id_str] = queue

        async def _bg_research():
            try:
                async for event in _handle_research(
                    conversation_id, user_id, content, user_msg.id,
                    model=model, mode="research",
                ):
                    await queue.put(event)
            except Exception:
                logger.error(
                    "bg_research_crashed",
                    conv_id=conv_id_str,
                    exc_info=True,
                )
                await queue.put(_sse("error", {"message": "研究流水线内部错误"}))
            finally:
                await queue.put(None)  # sentinel — signals stream completion
                _research_bg_tasks.pop(conv_id_str, None)
                _research_queues.pop(conv_id_str, None)

        bg_task = asyncio.create_task(_bg_research())
        _research_bg_tasks[conv_id_str] = bg_task

        try:
            while True:
                event = await queue.get()
                if event is None:  # sentinel from bg task
                    break
                yield event
        except asyncio.CancelledError:
            # SSE client disconnected — the background task keeps running.
            # Queue + task are intentionally *not* cleaned up so resume can
            # reconnect.  The bg task's finally block will clean up when it
            # finishes naturally.
            logger.info(
                "sse_client_disconnected_research_continues",
                conv_id=conv_id_str,
            )
        yield _sse("done", {"conversationId": conv_id_str, "model": model})
        return

    # Chat mode (unchanged).
    try:
        async for event in _handle_chat(conversation_id, user_id, content, model=model, mode=mode):
            yield event
    except Exception as exc:
        logger.error(
            "chat_service_error",
            conversation_id=str(conversation_id),
            mode=mode,
            error=str(exc),
            exc_info=True,
        )
        yield _sse("error", {"message": "处理消息时发生内部错误", "mode": mode})

    yield _sse("done", {"conversationId": str(conversation_id), "model": model})


async def resume_research_stream(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Reconnect to an in-progress research pipeline after a network interrupt.

    Returns an SSE event stream that reads from the existing background task's
    queue.  If there is no active pipeline for this conversation the stream
    yields a single ``inactive`` event and closes (the frontend should then
    fall back to showing whatever was persisted to the database).
    """
    conv_id_str = str(conversation_id)
    queue = _research_queues.get(conv_id_str)

    if queue is None:
        # No active pipeline — check whether a plan is still pending.
        # If the user hasn't acted on the plan yet, the plan_card is already
        # in the message history; the frontend re-renders the action buttons
        # from the persisted metadata.
        logger.info(
            "resume_no_active_pipeline",
            conv_id=conv_id_str,
        )
        yield _sse("inactive", {
            "conversationId": conv_id_str,
            "message": "该对话没有正在运行的研究流水线",
        })
        yield _sse("done", {"conversationId": conv_id_str, "model": model})
        return

    logger.info(
        "resume_reconnected",
        conv_id=conv_id_str,
        user_id=str(user_id),
    )

    # Drain whatever is already queued first (avoid replaying old events to
    # a fresh client that may already have rendered some of them).  Keep the
    # sentinel (None) if present — it signals that the bg task is done.
    drained = 0
    sentinel_seen = False
    while not queue.empty():
        try:
            item = queue.get_nowait()
            drained += 1
            if item is None:
                sentinel_seen = True
            # (We discard the drained items; the client gets fresh events from
            # the message history endpoint for what it missed.)
        except asyncio.QueueEmpty:
            break
    if drained:
        logger.info("resume_drained_stale_events", conv_id=conv_id_str, count=drained)

    # If the sentinel was already in the queue, the bg task has finished.
    # Nothing more to stream — the client can read results from GET /messages.
    if sentinel_seen:
        logger.info("resume_pipeline_already_finished", conv_id=conv_id_str)
        yield _sse("inactive", {
            "conversationId": conv_id_str,
            "message": "研究流水线已完成，请刷新消息列表查看结果",
        })
        yield _sse("done", {"conversationId": conv_id_str, "model": model})
        return

    try:
        while True:
            event = await queue.get()
            if event is None:  # sentinel — bg task finished
                break
            yield event
    except asyncio.CancelledError:
        logger.info(
            "sse_client_disconnected_resume",
            conv_id=conv_id_str,
        )
    yield _sse("done", {"conversationId": conv_id_str, "model": model})


# ── Plan Action (T3b-012) ──────────────────────────────────────────────

async def set_plan_action(
    message_id: uuid.UUID, action: str, modifications: str | None = None,
) -> None:
    """
    Record a user's plan action and signal the waiting pipeline node.

    Called by the API when POST /messages/{id}/plan-action is hit.

    Resilience: if the SSE stream that called ``wait_for_plan_action`` was
    cancelled (client disconnected), there is no active waiter in
    ``_plan_events``.  In that case we restart the pipeline as a background
    task using the retained plan context so results are still persisted to
    the database and the user sees them on reload.
    """
    msg_id_str = str(message_id)
    _plan_actions[msg_id_str] = {"action": action, "modifications": modifications}
    event = _plan_events.pop(msg_id_str, None)

    if event is None:
        # No active waiter — the SSE stream died before the user acted.
        # The plan context was saved by _handle_research before the wait.
        ctx = _retained_plan_ctx.pop(msg_id_str, None)
        if ctx is not None and action in ("accept", "modify"):
            logger.info(
                "plan_action_restarting_pipeline",
                message_id=msg_id_str,
                action=action,
            )
            asyncio.create_task(
                _execute_plan_action_background(message_id, action, modifications, ctx)
            )
        elif action == "reject":
            await _update_plan_card_metadata(message_id, {"status": "rejected"})
            logger.info("plan_rejected_dead_waiter", message_id=msg_id_str)
        else:
            logger.info("plan_action_no_waiter_no_ctx", message_id=msg_id_str, action=action)
    else:
        event.set()

    logger.info(
        "plan_action_received",
        message_id=str(message_id),
        action=action,
    )


async def _execute_plan_action_background(
    plan_msg_id: uuid.UUID,
    action: str,
    modifications: str | None,
    ctx: dict,
) -> None:
    """Run the post-plan pipeline entirely in the background (no SSE client).

    Persists results as messages in the conversation so the user sees them
    after reloading.  This is the resilience path for when the SSE connection
    drops before the user acts on a plan.
    """
    conv_id = ctx["conversation_id"]
    conv_id_str = str(conv_id)
    plan = ctx["plan"]
    plan_state = ctx["plan_state"]
    plan_msg = ctx["plan_msg"]
    task_id_str = ctx["task_id_str"]
    user_message_id = ctx["user_message_id"]
    model = ctx["model"]

    # Reuse or create a queue.  If the original background task is still
    # running (e.g. timeout hasn't fired yet), share its queue so events
    # from both don't get split across two channels.
    queue = _research_queues.get(conv_id_str)
    if queue is None:
        queue = asyncio.Queue()
        _research_queues[conv_id_str] = queue

    async def _run():
        try:
            async for event in _execute_accepted_plan(
                plan_msg_id, action, modifications, plan, plan_state,
                plan_msg, task_id_str, conv_id, user_message_id, model,
            ):
                await queue.put(event)
        except Exception:
            logger.error(
                "bg_plan_action_crashed",
                conv_id=conv_id_str,
                exc_info=True,
            )
            await queue.put(
                _sse("error", {"message": "研究流水线内部错误"})
            )
        finally:
            await queue.put(None)
            _research_bg_tasks.pop(conv_id_str, None)
            _research_queues.pop(conv_id_str, None)

    bg_task = asyncio.create_task(_run())
    _research_bg_tasks[conv_id_str] = bg_task
    logger.info(
        "bg_pipeline_started_from_plan_action",
        conv_id=conv_id_str,
        action=action,
    )


async def _execute_accepted_plan(
    plan_msg_id: uuid.UUID,
    action: str,
    modifications: str | None,
    plan: dict,
    plan_state: dict[str, Any],
    plan_msg: Message,
    task_id_str: str,
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    model: str | None,
) -> AsyncGenerator[dict, None]:
    """Execute the pipeline after a plan is accepted/modified (resilience path)."""
    if action == "modify":
        async for event in _route_modify(
            {"action": action, "modifications": modifications},
            plan, plan_state, plan_msg, task_id_str,
            conversation_id, user_message_id, model, "research",
        ):
            yield event
    else:
        await _update_plan_card_metadata(plan_msg_id, {"status": "accepted"})
        yield _sse("plan_action", {"taskId": task_id_str, "action": action})
        async for event in _run_pipeline_from_plan(
            plan_state, plan, conversation_id, user_message_id, model, "research",
        ):
            yield event


async def wait_for_plan_action(message_id: uuid.UUID, timeout: float = 300.0) -> dict:
    """
    Wait for the user to act on a plan (T3b-012).

    Blocks until set_plan_action() is called, or timeout expires.
    Returns {"action": "accept|modify|reject", "modifications": "..."} or
    {"action": "timeout"} on expiry.
    """
    event = asyncio.Event()
    _plan_events[str(message_id)] = event
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except TimeoutError:
        _plan_events.pop(str(message_id), None)
        logger.warning("plan_action_timeout", message_id=str(message_id))
        return {"action": "timeout"}
    return _plan_actions.pop(str(message_id), {"action": "timeout"})


# ── Internal Handlers ───────────────────────────────────────────────────

async def _handle_chat(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    *,
    model: str | None = None,
    mode: Literal["chat", "research"] = "chat",
) -> AsyncGenerator[dict, None]:
    """Stream a chat reply with conversation history as context."""
    provider = get_llm_provider()

    # Load recent messages for context
    history = await conversation_service.get_messages(
        conversation_id, limit=20,
    )
    context_messages = [
        {"role": msg.role, "content": msg.content[:2000]}
        for msg in history["items"]
        if msg.role in ("user", "assistant")
    ]

    messages = [
        {"role": "system", "content": "You are a helpful AI research assistant."},
        *context_messages,
        {"role": "user", "content": content},
    ]

    full_reply = ""
    try:
        async for token in provider.chat_stream(
            messages=messages, temperature=0.7, model=model,
        ):
            full_reply += token
            yield _sse("chat_chunk", {"content": token})
    except Exception:
        logger.error(
            "chat_reply_failed",
            conv_id=str(conversation_id),
            user_id=str(user_id),
            exc_info=True,
        )
        yield _sse("error", {"message": "生成回复失败，请重试"})
        return

    # Save assistant message
    assistant_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=full_reply,
        message_type="text",
        model=model,
        metadata={"mode": mode},
    )
    logger.info(
        "chat_reply_saved",
        conv_id=str(conversation_id),
        user_id=str(user_id),
        message_id=str(assistant_msg.id),
        reply_len=len(full_reply),
        model=model,
    )


async def _handle_research(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    user_message_id: uuid.UUID,
    *,
    model: str | None = None,
    mode: Literal["chat", "research"] = "research",
) -> AsyncGenerator[dict, None]:
    """Start a research pipeline: create task → generate plan → await
    user confirmation → run the full pipeline (retrieve → analyze → gap
    loop → synthesize → write), surfacing each stage as a chat message card.

    The plan is generated by the Planner agent (research_service.generate_plan)
    and rendered as a plan_card. The pipeline then PAUSES for the user to
    accept / modify / reject via wait_for_plan_action(). On accept (or modify,
    which folds the user's notes into the topic) the LangGraph pipeline runs
    and its SSE events are forwarded to the client and mirrored into the
    conversation as retrieval_card / gap_question / report_card messages so
    they persist across reloads (Constitution III).
    """
    # Create a hidden ResearchTask.
    task = await task_service.create_task(user_id=user_id, topic=content)
    task_id_str = str(task.id)
    logger.info("research_task_created", task_id=str(task.id), conv_id=str(conversation_id))

    plan_state = {
        "task_id": task_id_str,
        "user_id": str(user_id),
        "conversation_id": str(conversation_id),
        "topic": content,
        "model": model,
    }

    # Generate the plan via the Planner agent.
    try:
        plan = await research_service.generate_plan(plan_state)
    except Exception:
        logger.error("plan_generation_failed", conv_id=str(conversation_id), exc_info=True)
        yield _sse("error", {"message": "生成研究计划失败，请重试"})
        return

    questions = _plan_questions_for_card(plan)
    keywords = _plan_keywords_for_card(plan)
    plan_text = json.dumps(plan, ensure_ascii=False, indent=2)

    # Save plan_card message.
    plan_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=plan_text,
        message_type="plan_card",
        parent_message_id=user_message_id,
        model=model,
        metadata={
            "task_id": task_id_str,
            "questions": questions,
            "keywords": keywords,
            "priority_order": [q["id"] for q in questions],
            "status": "pending_confirmation",
            "mode": mode,
        },
    )
    logger.info(
        "plan_saved",
        conv_id=str(conversation_id),
        plan_msg_id=str(plan_msg.id),
        task_id=task_id_str,
        parent_message_id=str(user_message_id),
        model=model,
    )

    yield _sse("plan_generated", {
        "messageId": str(plan_msg.id),
        "taskId": task_id_str,
        "status": "pending_confirmation",
        "questions": questions,
        "keywords": keywords,
        "planText": plan_text,
        "model": model,
    })

    # Register the waiter event BEFORE retaining plan context so
    # set_plan_action finds a live event and does not spuriously trigger
    # the dead-waiter recovery path (race window fix — review finding #4).
    plan_msg_id_str = str(plan_msg.id)
    plan_waiter = asyncio.Event()
    _plan_events[plan_msg_id_str] = plan_waiter
    _retained_plan_ctx[plan_msg_id_str] = {
        "conversation_id": conversation_id,
        "plan": plan,
        "plan_state": plan_state,
        "plan_msg": plan_msg,
        "task_id_str": task_id_str,
        "user_message_id": user_message_id,
        "model": model,
    }

    # ── Pause for user action (accept / modify / reject) ──────────────
    try:
        await asyncio.wait_for(plan_waiter.wait(), timeout=300.0)
    except TimeoutError:
        _plan_events.pop(plan_msg_id_str, None)
        logger.warning("plan_action_timeout", message_id=plan_msg_id_str)
        action = {"action": "timeout"}
    else:
        action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
    _retained_plan_ctx.pop(plan_msg_id_str, None)  # clean up — waiter resolved
    action_type = action.get("action", "timeout")

    if action_type == "reject":
        await _update_plan_card_metadata(plan_msg.id, {"status": "rejected"})
        yield _sse("plan_action", {"taskId": task_id_str, "action": "reject"})
        logger.info("plan_rejected", task_id=task_id_str, conv_id=str(conversation_id))
        return

    if action_type == "timeout":
        await _update_plan_card_metadata(plan_msg.id, {"status": "rejected"})
        yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
        logger.warning("plan_action_timeout", task_id=task_id_str)
        return

    # accept → run the pipeline with the original plan.
    # modify → B-plan router: classify the modification (augment vs revise),
    #   then dispatch to the matching outlet. Replaces the old "fold notes into
    #   topic" dead code (no downstream agent ever read topic post-plan).
    if action_type == "modify":
        async for event in _route_modify(
            action, plan, plan_state, plan_msg, task_id_str,
            conversation_id, user_message_id, model, mode,
        ):
            yield event
        return

    # accept (or timeout already handled above)
    await _update_plan_card_metadata(plan_msg.id, {"status": "accepted"})
    yield _sse("plan_action", {"taskId": task_id_str, "action": action_type})
    async for event in _run_pipeline_from_plan(
        plan_state, plan, conversation_id, user_message_id, model, mode,
    ):
        yield event


async def _run_pipeline_from_plan(
    plan_state: dict[str, Any],
    plan: dict,
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
) -> AsyncGenerator[dict, None]:
    """Run the LangGraph pipeline from a confirmed plan, mirroring SSE events
    into durable conversation messages and forwarding them to the client.

    Shared by the accept path and the augment outlet of the modify router.
    """
    pipeline_state = {
        **plan_state,
        "research_plan": plan,
        "round_number": 1,
        "all_retrieval_results": [],
    }

    async for event in research_service.run_pipeline(
        pipeline_state, wait_gap=research_service.wait_for_gap_response, model=model,
    ):
        msg_id = await _mirror_pipeline_event(
            event, conversation_id, user_message_id, model, mode,
        )
        if msg_id and isinstance(event.get("data"), dict):
            event["data"]["messageId"] = str(msg_id)
        yield event


async def _route_modify(
    action: dict,
    plan: dict,
    plan_state: dict[str, Any],
    plan_msg: Message,
    task_id_str: str,
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
) -> AsyncGenerator[dict, None]:
    """B-plan modify router: classify the modification, then dispatch.

    - augment outlet: write the user's notes to ``user_focus_notes`` (read by
      Writer/Synthesizer), mark the card ``accepted_with_notes``, run pipeline.
    - revise outlet: run Planner in revise mode, refill the SAME plan_card with
      the revised plan (status ``revised``), re-emit ``plan_generated``, and
      loop back to ``wait_for_plan_action`` so the user can accept / re-modify /
      reject the revised plan. No stacking of plan cards (same message id).

    Classification never raises — on failure it defaults to augment (the safer
    outlet; see research_service.classify_modification D1).
    """
    modifications = (action.get("modifications") or "").strip()

    mode_decision = await research_service.classify_modification(plan, modifications)

    if mode_decision == "revise":
        async for event in _revise_outlet(
            plan, plan_state, plan_msg, task_id_str,
            conversation_id, user_message_id, model, mode,
            modifications,
        ):
            yield event
        return

    # augment outlet (also the fallback when classification is uncertain).
    plan_state["user_focus_notes"] = modifications
    await _update_plan_card_metadata(plan_msg.id, {
        "status": "accepted_with_notes",
        "user_focus_notes": modifications,
    })
    yield _sse("plan_action", {
        "taskId": task_id_str,
        "action": "modify",
        "modify_outlet": "augment",
        "user_focus_notes": modifications,
    })
    async for event in _run_pipeline_from_plan(
        plan_state, plan, conversation_id, user_message_id, model, mode,
    ):
        yield event


async def _revise_outlet(
    plan: dict,
    plan_state: dict[str, Any],
    plan_msg: Message,
    task_id_str: str,
    conversation_id: uuid.UUID,
    user_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
    modifications: str,
) -> AsyncGenerator[dict, None]:
    """Revise outlet of the modify router: produce a revised plan, refill the
    plan_card in place, and loop back to plan-action confirmation.

    On revise failure the task is not started; we surface an error and let the
    user retry from the (unchanged) original card.
    """
    try:
        revised = await research_service.revise_plan(plan_state, plan, modifications)
    except Exception:
        logger.error("plan_revise_failed", task_id=task_id_str, exc_info=True)
        yield _sse("error", {"message": "修订研究计划失败，请重试"})
        return

    if not revised or not revised.get("research_questions"):
        logger.warning("plan_revise_empty", task_id=task_id_str)
        yield _sse("error", {"message": "修订计划为空，请换一种表述重试"})
        return

    questions = _plan_questions_for_card(revised)
    keywords = _plan_keywords_for_card(revised)
    plan_text = json.dumps(revised, ensure_ascii=False, indent=2)

    # Refill the SAME plan_card with the revised plan (no new message).
    await _update_plan_card_metadata(plan_msg.id, {
        "status": "revised",
        "questions": questions,
        "keywords": keywords,
        "priority_order": [q["id"] for q in questions],
        "plan_text": plan_text,
        "last_modification": modifications,
    })

    # Re-emit plan_generated so the live client refreshes the card in place.
    yield _sse("plan_generated", {
        "messageId": str(plan_msg.id),
        "taskId": task_id_str,
        "status": "revised",
        "questions": questions,
        "keywords": keywords,
        "planText": plan_text,
        "model": model,
    })
    yield _sse("plan_action", {
        "taskId": task_id_str,
        "action": "modify",
        "modify_outlet": "revise",
    })

    # Loop back: wait for the user's decision on the REVISED plan. The same
    # message_id is reused. Register the event BEFORE retaining context to
    # avoid the race window (same fix as the initial plan confirmation).
    plan_msg_id_str = str(plan_msg.id)
    plan_waiter = asyncio.Event()
    _plan_events[plan_msg_id_str] = plan_waiter
    _retained_plan_ctx[plan_msg_id_str] = {
        "conversation_id": conversation_id,
        "plan": revised,
        "plan_state": plan_state,
        "plan_msg": plan_msg,
        "task_id_str": task_id_str,
        "user_message_id": user_message_id,
        "model": model,
    }
    try:
        await asyncio.wait_for(plan_waiter.wait(), timeout=300.0)
    except TimeoutError:
        _plan_events.pop(plan_msg_id_str, None)
        logger.warning("plan_action_timeout", message_id=plan_msg_id_str)
        action = {"action": "timeout"}
    else:
        action = _plan_actions.pop(plan_msg_id_str, {"action": "timeout"})
    _retained_plan_ctx.pop(plan_msg_id_str, None)
    action_type = action.get("action", "timeout")

    if action_type == "reject":
        await _update_plan_card_metadata(plan_msg.id, {"status": "rejected"})
        yield _sse("plan_action", {"taskId": task_id_str, "action": "reject"})
        logger.info("plan_rejected", task_id=task_id_str, conv_id=str(conversation_id))
        return

    if action_type == "timeout":
        await _update_plan_card_metadata(plan_msg.id, {"status": "rejected"})
        yield _sse("plan_action", {"taskId": task_id_str, "action": "timeout"})
        logger.warning("plan_action_timeout", task_id=task_id_str)
        return

    if action_type == "modify":
        # Re-enter the router with the REVISED plan as the new baseline.
        async for event in _route_modify(
            action, revised, plan_state, plan_msg, task_id_str,
            conversation_id, user_message_id, model, mode,
        ):
            yield event
        return

    # accept the revised plan → run the pipeline.
    await _update_plan_card_metadata(plan_msg.id, {"status": "accepted"})
    yield _sse("plan_action", {"taskId": task_id_str, "action": "accept"})
    async for event in _run_pipeline_from_plan(
        plan_state, revised, conversation_id, user_message_id, model, mode,
    ):
        yield event


async def _mirror_pipeline_event(
    event: dict,
    conversation_id: uuid.UUID,
    parent_message_id: uuid.UUID,
    model: str | None,
    mode: Literal["chat", "research"],
) -> uuid.UUID | None:
    """Persist a pipeline SSE event as a conversation Message when it
    represents a durable card (retrieval / gap / report).

    Returns the saved Message id (so the caller can stamp it into the SSE
    event's ``messageId``), or None for non-durable events.
    """
    name = event.get("event")
    data = event.get("data") or {}
    try:
        if name == "retrieval_complete":
            msg = await conversation_service.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content="",
                message_type="retrieval_card",
                parent_message_id=parent_message_id,
                model=model,
                metadata={
                    "task_id": data.get("taskId"),
                    "round": data.get("round", 1),
                    "source_count": data.get("sourceCount", 0),
                    "sources": data.get("sources", []),
                    "mode": mode,
                },
            )
            return msg.id
        elif name == "gap_question":
            msg = await conversation_service.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content="",
                message_type="gap_question",
                parent_message_id=parent_message_id,
                model=model,
                metadata={
                    "task_id": data.get("taskId"),
                    "gap_id": data.get("gapId"),
                    "description": data.get("description", ""),
                    "severity": data.get("severity", "moderate"),
                    "status": "pending",
                    "mode": mode,
                },
            )
            return msg.id
        elif name == "report_complete":
            msg = await conversation_service.save_message(
                conversation_id=conversation_id,
                role="assistant",
                content=data.get("abstract", ""),
                message_type="report_card",
                parent_message_id=parent_message_id,
                model=model,
                metadata={
                    "task_id": data.get("taskId"),
                    "report_id": data.get("reportId"),
                    "title": data.get("title", "研究报告"),
                    "sections": data.get("sections", []),
                    "citations": data.get("citations", []),
                    "gap_notes": data.get("gapNotes", ""),
                    "mode": mode,
                },
            )
            return msg.id
    except Exception:
        logger.warning("mirror_message_failed", event=name, exc_info=True)
    return None


async def _update_plan_card_metadata(message_id: uuid.UUID, updates: dict) -> None:
    """Merge ``updates`` into a plan_card message's metadata in place.

    Used by the modify router to refill the SAME plan_card (status, questions,
    keywords, plan_text, …) so revised/augmented plans update in place rather
    than stacking new cards.
    """
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(Message).where(Message.id == message_id)
        )
        msg = result.scalar_one_or_none()
        if msg is None:
            logger.warning("plan_card_not_found", message_id=str(message_id))
            return
        extra = dict(msg.extra or {})
        extra.update(updates)
        msg.extra = extra
        await session.commit()


def _plan_questions_for_card(plan: dict) -> list[dict]:
    """Return the plan's questions in a UI-friendly list of dicts."""
    out: list[dict] = []
    for q in (plan.get("research_questions") or []) if isinstance(plan, dict) else []:
        if not isinstance(q, dict):
            continue
        text = (q.get("question") or "").strip()
        if not text:
            continue
        subs = [
            (sq.get("question") or "").strip()
            for sq in (q.get("sub_questions") or [])
            if isinstance(sq, dict) and (sq.get("question") or "").strip()
        ]
        out.append({
            "id": q.get("id", ""),
            "question": text,
            "sub_questions": subs,
        })
    return out


def _plan_keywords_for_card(plan: dict) -> list[str]:
    """Return the plan's keywords as a flat list of strings."""
    raw = (plan.get("search_keywords") or []) if isinstance(plan, dict) else []
    out: list[str] = []
    if isinstance(raw, list):
        for k in raw:
            if isinstance(k, dict):
                kw = (k.get("keyword") or "").strip()
            elif isinstance(k, str):
                kw = k.strip()
            else:
                kw = ""
            if kw and kw not in out:
                out.append(kw)
    return out


