"""
Conversation service: CRUD, message pagination, context window management.

Provides user-scoped conversation lifecycle management and message
history retrieval. Called by ChatService and the conversations API.
"""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, desc, func, select

from backend.core.config import settings
from backend.core.database import get_postgres_session
from backend.models.conversation import Conversation, Message
from backend.utils.logging import get_logger

logger = get_logger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────

async def _get_conv_for_user(session, conv_id: uuid.UUID, user_id: uuid.UUID) -> Conversation:
    """Fetch a conversation scoped to its owner. Raises ValueError if not found."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conv_id,
            Conversation.user_id == user_id,
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        logger.warning("conversation_not_found", conv_id=str(conv_id), user_id=str(user_id))
        raise ValueError(f"对话 {conv_id} 不存在")
    return conv


def _conversation_to_dict(conv: Conversation, message_count: int, last_message_preview: str | None) -> dict[str, Any]:
    """Build a plain dict suitable for ConversationRead(**dict)."""
    return {
        "id": conv.id,
        "title": conv.title,
        "model": conv.model,
        "message_count": message_count,
        "last_message_preview": last_message_preview,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
    }


def _message_stats_subqueries():
    """Return (message_count_subq, last_message_preview_subq) correlated scalar subqueries.

    Both correlate to the outer ``Conversation`` row — embed them directly in
    ``select(Conversation, count_subq, preview_subq)``. Executed once per row,
    both scan the indexed ``messages.conversation_id`` column.
    """
    msg_count = (
        select(func.count(Message.id))
        .where(Message.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
        .label("message_count")
    )
    last_preview = (
        select(Message.content)
        .where(
            Message.conversation_id == Conversation.id,
            Message.role == "user",
        )
        .order_by(Message.created_at.desc())
        .limit(1)
        .correlate(Conversation)
        .scalar_subquery()
        .label("last_message_preview")
    )
    return msg_count, last_preview


# ── Conversation CRUD ───────────────────────────────────────────────────

async def create_conversation(
    user_id: uuid.UUID,
    title: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Create a new empty conversation. Title auto-generated if not provided."""
    conv = Conversation(
        user_id=user_id,
        title=title or "New Conversation",
        model=model or settings.llm_model,
    )
    session = get_postgres_session()
    async with session:
        session.add(conv)
        await session.commit()
        await session.refresh(conv)

    logger.info("conversation_created", conv_id=str(conv.id), user_id=str(user_id))
    # New conversation has no messages — no stats query needed.
    return _conversation_to_dict(conv, message_count=0, last_message_preview=None)


async def get_conversation(
    conv_id: uuid.UUID,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Get conversation metadata with ownership check. Raises ValueError if not found."""
    session = get_postgres_session()
    async with session:
        msg_count_subq, last_preview_subq = _message_stats_subqueries()
        result = await session.execute(
            select(Conversation, msg_count_subq, last_preview_subq).where(
                Conversation.id == conv_id,
                Conversation.user_id == user_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            logger.warning(
                "conversation_not_found",
                conv_id=str(conv_id),
                user_id=str(user_id),
            )
            raise ValueError(f"对话 {conv_id} 不存在")
        conv = row[0]

    logger.info(
        "conversation_retrieved",
        conv_id=str(conv_id),
        user_id=str(user_id),
    )
    return _conversation_to_dict(
        conv,
        message_count=row.message_count or 0,
        last_message_preview=row.last_message_preview,
    )


async def list_conversations(
    user_id: uuid.UUID,
    *,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> dict[str, Any]:
    """List conversations for a user, sorted by last active time."""
    session = get_postgres_session()
    async with session:
        conditions = [Conversation.user_id == user_id]
        if search:
            conditions.append(Conversation.title.ilike(f"%{search}%"))

        # Total
        total_result = await session.execute(
            select(func.count()).select_from(Conversation).where(and_(*conditions))
        )
        total = total_result.scalar() or 0

        # Page — include message stats as correlated scalar subqueries
        msg_count_subq, last_preview_subq = _message_stats_subqueries()
        offset = max(0, (page - 1)) * page_size
        result = await session.execute(
            select(Conversation, msg_count_subq, last_preview_subq)
            .where(and_(*conditions))
            .order_by(desc(Conversation.updated_at))
            .offset(offset)
            .limit(page_size)
        )
        rows = result.all()

    items = [
        _conversation_to_dict(
            row[0],
            message_count=row.message_count or 0,
            last_message_preview=row.last_message_preview,
        )
        for row in rows
    ]

    logger.info(
        "conversations_listed",
        user_id=str(user_id),
        total=total,
        page=page,
    )
    return {
        "conversations": items,
        "total": total,
    }


async def update_conversation_title(
    conv_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
) -> dict[str, Any]:
    """Update a conversation's title."""
    session = get_postgres_session()
    async with session:
        conv = await _get_conv_for_user(session, conv_id, user_id)
        conv.title = title
        conv.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(conv)

        # Fetch message stats in the same session
        msg_count_subq, last_preview_subq = _message_stats_subqueries()
        stats_result = await session.execute(
            select(msg_count_subq, last_preview_subq)
            .select_from(Conversation)
            .where(Conversation.id == conv_id)
        )
        stats = stats_result.one()

    logger.info("conversation_title_updated", conv_id=str(conv_id), title=title)
    return _conversation_to_dict(
        conv,
        message_count=stats.message_count or 0,
        last_message_preview=stats.last_message_preview,
    )


async def delete_conversation(conv_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Delete a conversation and cascade its messages."""
    session = get_postgres_session()
    async with session:
        conv = await _get_conv_for_user(session, conv_id, user_id)
        await session.delete(conv)
        await session.commit()

    logger.info("conversation_deleted", conv_id=str(conv_id), user_id=str(user_id))


# ── Message Operations ──────────────────────────────────────────────────

async def save_message(
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    *,
    message_type: str = "text",
    parent_message_id: uuid.UUID | None = None,
    metadata: dict | None = None,
    token_count: int = 0,
    model: str | None = None,
) -> Message:
    """Persist a message to the conversation."""
    msg = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        message_type=message_type,
        parent_message_id=parent_message_id,
        extra=metadata or {},
        token_count=token_count,
        model=model,
    )
    session = get_postgres_session()
    async with session:
        session.add(msg)
        # Touch the conversation's updated_at
        await session.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .with_for_update()
        )
        conv_result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = conv_result.scalar_one()
        conv.updated_at = datetime.now(timezone.utc)
        conv.context_window_tokens += token_count
        # Update last_used_model on conversation when assistant replies with a model
        if role == "assistant" and model:
            conv.model = model

        await session.commit()
        await session.refresh(msg)

    logger.info(
        "message_saved",
        message_id=str(msg.id),
        conv_id=str(conversation_id),
        role=role,
        type=message_type,
        model=model,
    )
    return msg


async def get_message_conversation_id(msg_id: uuid.UUID) -> uuid.UUID:
    """Return the conversation_id for a message. Raises ValueError if not found."""
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(Message.conversation_id).where(Message.id == msg_id)
        )
        conv_id = result.scalar_one_or_none()
        if conv_id is None:
            raise ValueError(f"消息 {msg_id} 不存在")
        return conv_id


async def get_messages(
    conversation_id: uuid.UUID,
    *,
    before_id: uuid.UUID | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get messages for a conversation with cursor-based pagination."""
    session = get_postgres_session()
    async with session:
        conditions = [Message.conversation_id == conversation_id]
        if before_id:
            # Get created_at of the cursor message
            cursor_result = await session.execute(
                select(Message.created_at).where(Message.id == before_id)
            )
            cursor_ts = cursor_result.scalar_one_or_none()
            if cursor_ts:
                conditions.append(Message.created_at < cursor_ts)

        query = (
            select(Message)
            .where(and_(*conditions))
            .order_by(desc(Message.created_at))
            .limit(limit + 1)  # +1 to check has_more
        )
        result = await session.execute(query)
        messages = result.scalars().all()

    has_more = len(messages) > limit
    items = list(reversed(messages[:limit]))  # oldest first

    logger.info(
        "messages_retrieved",
        conv_id=str(conversation_id),
        count=len(items),
        has_more=has_more,
    )
    return {"items": items, "has_more": has_more}


async def auto_generate_title(conv_id: uuid.UUID, user_id: uuid.UUID, content: str) -> None:
    """Generate conversation title from first user message (first 50 chars)."""
    title = content.strip()[:50]
    if len(content.strip()) > 50:
        title += "..."
    session = get_postgres_session()
    async with session:
        conv = await _get_conv_for_user(session, conv_id, user_id)
        if conv.title == "New Conversation" and title:
            conv.title = title
            await session.commit()
            logger.info(
                "conversation_title_auto_generated",
                conv_id=str(conv_id),
                title=title,
            )
