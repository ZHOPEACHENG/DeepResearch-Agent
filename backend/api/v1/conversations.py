"""
Conversation API endpoints — chat UI backend (Phase 3b).

Endpoints:
- GET    /conversations                    List conversations
- POST   /conversations                    Create conversation
- GET    /conversations/{id}               Get conversation + messages
- PATCH  /conversations/{id}              Update title
- DELETE /conversations/{id}              Delete + cascade
- GET    /conversations/{id}/messages      Message history (cursor)
- POST   /conversations/{id}/messages      Send message (SSE stream)
- POST   /messages/{id}/plan-action        Act on research plan
"""

import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from backend.api.deps import get_current_active_user
from backend.models.user import User
from backend.schemas.conversation import (
    ConversationCreate,
    ConversationListResponse,
    ConversationRead,
    ConversationUpdate,
    MessageListResponse,
    MessageRead,
    PlanActionRequest,
    SendMessageRequest,
)
from backend.services import chat_service, conversation_service
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations")


# ── T3b-014: CRUD ──────────────────────────────────────────────────────

@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """List user's conversations (newest first)."""
    logger.info(
        "api_list_conversations",
        user_id=str(current_user.id),
        page=page,
        search=search,
    )
    result = await conversation_service.list_conversations(
        current_user.id, page=page, page_size=page_size, search=search,
    )
    items = [ConversationRead(**conv) for conv in result["conversations"]]
    return ConversationListResponse(
        items=items, total=result["total"], page=page, page_size=page_size,
    )


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate = ConversationCreate(),
    current_user: User = Depends(get_current_active_user),
):
    """Create a new empty conversation."""
    logger.info(
        "api_create_conversation",
        user_id=str(current_user.id),
        title=body.title,
    )
    conv = await conversation_service.create_conversation(
        current_user.id, title=body.title, model=body.model,
    )
    return ConversationRead(**conv)


@router.get("/{conversation_id}", response_model=ConversationRead)
async def get_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Get conversation metadata. Use GET /{id}/messages for message history."""
    logger.info(
        "api_get_conversation",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
    )
    try:
        conv = await conversation_service.get_conversation(
            conversation_id, current_user.id,
        )
    except ValueError:
        logger.warning(
            "api_get_conversation_not_found",
            conv_id=str(conversation_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=404, detail="对话不存在")

    return ConversationRead(**conv)


@router.patch("/{conversation_id}", response_model=ConversationRead)
async def update_conversation(
    conversation_id: UUID,
    body: ConversationUpdate,
    current_user: User = Depends(get_current_active_user),
):
    """Update conversation title."""
    logger.info(
        "api_update_conversation",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
        title=body.title,
    )
    try:
        conv = await conversation_service.update_conversation_title(
            conversation_id, current_user.id, body.title,
        )
    except ValueError:
        logger.warning(
            "api_update_conversation_not_found",
            conv_id=str(conversation_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=404, detail="对话不存在")
    return ConversationRead(**conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """Delete a conversation and all its messages (cascade)."""
    logger.info(
        "api_delete_conversation",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
    )
    try:
        await conversation_service.delete_conversation(conversation_id, current_user.id)
    except ValueError:
        logger.warning(
            "api_delete_conversation_not_found",
            conv_id=str(conversation_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=404, detail="对话不存在")


# ── T3b-015: Send Message (SSE Stream) ──────────────────────────────────

@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID,
    body: SendMessageRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Send a message and receive SSE streaming response.

    The request body ``mode`` selects the branch — "chat" (default) streams a
    conversational reply; "research" starts the research pipeline. There is no
    LLM-based intent classification; the user picks the mode explicitly.

    Streams events: message_created → (chat_chunk | plan_generated | error) → done
    """
    # Verify conversation ownership through service layer
    try:
        await conversation_service.get_conversation(
            conversation_id, current_user.id,
        )
    except ValueError:
        logger.warning(
            "api_send_message_not_found",
            conv_id=str(conversation_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=404, detail="对话不存在")

    logger.info(
        "api_send_message",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
        preview=body.content[:80],
    )

    async def event_stream():
        try:
            async for sse_event in chat_service.handle_message(
                conversation_id, current_user.id,
                body.content,
                parent_message_id=body.parent_message_id,
                model=body.model,
                mode=body.mode,
            ):
                event_name = sse_event["event"]
                data_json = json.dumps(sse_event["data"], ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data_json}\n\n"
        except Exception:
            logger.error(
                "api_sse_stream_error",
                conv_id=str(conversation_id),
                user_id=str(current_user.id),
                exc_info=True,
            )
            error_data = json.dumps({"message": "数据流传输错误"}, ensure_ascii=False)
            yield f"event: error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── T3b-016: Message History ───────────────────────────────────────────

@router.get("/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: UUID,
    before_id: UUID | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_active_user),
):
    """Get paginated message history (cursor-based)."""
    logger.info(
        "api_get_messages",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
        before_id=str(before_id) if before_id else None,
    )
    # Verify ownership through service layer
    try:
        await conversation_service.get_conversation(
            conversation_id, current_user.id,
        )
    except ValueError:
        logger.warning(
            "api_get_messages_not_found",
            conv_id=str(conversation_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=404, detail="对话不存在")

    result = await conversation_service.get_messages(
        conversation_id, before_id=before_id, limit=limit,
    )
    items = [
        MessageRead(
            id=m.id, conversation_id=m.conversation_id, role=m.role,
            content=m.content, message_type=m.message_type,
            parent_message_id=m.parent_message_id, extra=m.extra or {},
            token_count=m.token_count, model=m.model, created_at=m.created_at,
        )
        for m in result["items"]
    ]
    return MessageListResponse(items=items, has_more=result["has_more"])


# ── T3b-017: Plan Action ───────────────────────────────────────────────

@router.post("/messages/{message_id}/plan-action")
async def plan_action(
    message_id: UUID,
    body: PlanActionRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Accept, modify, or reject a research plan."""
    # Verify message belongs to a conversation owned by the user
    try:
        conv_id = await conversation_service.get_message_conversation_id(message_id)
        await conversation_service.get_conversation(conv_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="消息不存在")

    if body.action == "modify" and not (body.modifications and body.modifications.strip()):
        raise HTTPException(status_code=422, detail="修改计划需要填写修改内容")

    logger.info(
        "api_plan_action",
        message_id=str(message_id),
        user_id=str(current_user.id),
        action=body.action,
    )
    await chat_service.set_plan_action(message_id, body.action, body.modifications)
    return {"status": "ok", "action": body.action}


# ── Research Resume (network-interrupt recovery) ──────────────────────────


@router.post("/{conversation_id}/research/resume")
async def resume_research(
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Reconnect to an in-progress research pipeline after a network interrupt.

    Returns an SSE stream that reads from the background task's event queue.
    If there is no active pipeline the stream yields an ``inactive`` event and
    closes — the frontend should then fall back to the persisted messages.
    """
    # Verify ownership
    try:
        await conversation_service.get_conversation(conversation_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="对话不存在")

    logger.info(
        "api_resume_research",
        conv_id=str(conversation_id),
        user_id=str(current_user.id),
    )

    async def event_stream():
        try:
            async for sse_event in chat_service.resume_research_stream(
                conversation_id, current_user.id,
            ):
                event_name = sse_event["event"]
                data_json = json.dumps(sse_event["data"], ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data_json}\n\n"
        except Exception:
            logger.error(
                "api_resume_stream_error",
                conv_id=str(conversation_id),
                exc_info=True,
            )
            yield (
                f"event: error\ndata: "
                f"{json.dumps({'message': '恢复连接失败'}, ensure_ascii=False)}\n\n"
            )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
