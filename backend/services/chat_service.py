"""
ChatService — orchestration hub for conversational research.

Handles the full lifecycle of a user message:
1. Save user Message
2. Run IntentRouter → chat | research
3. Branch:
   - chat: load history → stream LLM reply → save assistant Message
   - research: create ResearchTask → run Planner → emit plan_card →
     pause for user action (plan_confirmation_node)

SSE events are yielded as dicts that the API layer serializes and emits.
"""

import asyncio
import json
import uuid
from typing import AsyncGenerator

from backend.services import conversation_service, task_service
from backend.services.intent_router import classify_intent
from backend.tools.llm import get_llm_provider
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# Per-conversation asyncio Events for user-intervention pauses.
# Key: message_id (str), Value: asyncio.Event that resolves when user acts.
_plan_events: dict[str, asyncio.Event] = {}
_plan_actions: dict[str, dict] = {}  # message_id → {action, modifications}


# ── Public API ──────────────────────────────────────────────────────────

async def handle_message(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    *,
    parent_message_id: uuid.UUID | None = None,
    model: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Process a user message and yield SSE event dicts.

    Caller (API endpoint) iterates this generator and writes each dict
    as an SSE event to the client connection.

    Yields:
        {"event": "message_created", "data": {...}}
        {"event": "chat_chunk" | "intent_classified" | "plan_generated" | ..., "data": {...}}
        {"event": "done", "data": {...}}
        {"event": "error", "data": {...}}
    """
    logger.info(
        "message_received",
        conv_id=str(conversation_id),
        user_id=str(user_id),
        model=model,
        preview=content[:80],
    )

    # 1. Auto-generate title from first user message（内部有 guard：仅当标题仍是 "New Conversation" 时才生效）
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

    # 3. Intent classification (always uses intent_router_model — not the user-selected model)
    intent = await classify_intent(content)
    yield _sse("intent_classified", {"intent": intent})

    # 4. Branch by intent
    try:
        if intent == "chat":
            async for event in _handle_chat(conversation_id, user_id, content, model=model):
                yield event
        elif intent == "research":
            async for event in _handle_research(conversation_id, user_id, content, user_msg.id, model=model):
                yield event
    except Exception as exc:
        logger.error(
            "chat_service_error",
            conversation_id=str(conversation_id),
            intent=intent,
            error=str(exc),
            exc_info=True,
        )
        yield _sse("error", {"message": "处理消息时发生内部错误", "intent": intent})

    yield _sse("done", {"conversationId": str(conversation_id), "model": model})


# ── Plan Action (T3b-012) ──────────────────────────────────────────────

def set_plan_action(message_id: uuid.UUID, action: str, modifications: str | None = None) -> None:
    """
    Record a user's plan action and signal the waiting pipeline node.

    Called by the API when POST /messages/{id}/plan-action is hit.
    """
    _plan_actions[str(message_id)] = {"action": action, "modifications": modifications}
    event = _plan_events.pop(str(message_id), None)
    if event:
        event.set()
    logger.info(
        "plan_action_received",
        message_id=str(message_id),
        action=action,
    )


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
    except asyncio.TimeoutError:
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
        logger.error("chat_reply_failed", conv_id=str(conversation_id), exc_info=True)
        yield _sse("error", {"message": "生成回复失败，请重试"})
        return

    # Save assistant message
    assistant_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=full_reply,
        message_type="text",
        model=model,
    )
    logger.info(
        "chat_reply_saved",
        conv_id=str(conversation_id),
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
) -> AsyncGenerator[dict, None]:
    """Start a research pipeline: create task → generate plan → emit plan_card."""
    # Create a hidden ResearchTask
    task = await task_service.create_task(
        user_id=user_id,
        topic=content,
    )
    logger.info("research_task_created", task_id=str(task.id), conv_id=str(conversation_id))

    # Generate research plan (using LLM Planner — simplified for Phase 3b)
    provider = get_llm_provider()
    plan_prompt = f"""You are a research planner. Generate a structured research plan for the topic:
"{content}"

Output a JSON with:
- questions: array of research questions
- keywords: array of search keywords (Chinese + English)
- priority_order: ordered list of question IDs
"""
    try:
        plan_response = await provider.chat(
            messages=[{"role": "user", "content": plan_prompt}],
            temperature=0.3,
            model=model,
        )
    except Exception:
        logger.error("plan_generation_failed", conv_id=str(conversation_id), exc_info=True)
        yield _sse("error", {"message": "生成研究计划失败，请重试"})
        return

    # Parse plan JSON from LLM response
    questions = []
    keywords = []
    plan_text = plan_response
    try:
        parsed = json.loads(plan_response)
        questions = parsed.get("questions", [])
        keywords = parsed.get("keywords", [])
        plan_text = json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        logger.warning("plan_json_parse_failed", conv_id=str(conversation_id))

    # Save plan_card message
    plan_msg = await conversation_service.save_message(
        conversation_id=conversation_id,
        role="assistant",
        content=plan_text,
        message_type="plan_card",
        parent_message_id=user_message_id,
        model=model,
        metadata={
            "task_id": str(task.id),
            "questions": questions,
            "keywords": keywords,
            "status": "pending_confirmation",
        },
    )

    logger.info(
        "plan_saved",
        conv_id=str(conversation_id),
        plan_msg_id=str(plan_msg.id),
        task_id=str(task.id),
        parent_message_id=str(user_message_id),
        model=model,
    )

    yield _sse("plan_generated", {
        "messageId": str(plan_msg.id),
        "taskId": str(task.id),
        "status": "pending_confirmation",
        "questions": questions,
        "keywords": keywords,
        "planText": plan_text,
        "model": model,
    })

    # In full implementation, wait here for user action via wait_for_plan_action()
    # For Phase 3b checkpoint: pipeline stops here, user clicks accept/modify/reject




# ── Helpers ─────────────────────────────────────────────────────────────

def _sse(event: str, data: dict) -> dict:
    """Build an SSE event dict."""
    return {"event": event, "data": data}
