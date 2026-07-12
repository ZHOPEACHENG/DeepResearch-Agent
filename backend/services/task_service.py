"""
Task management service: CRUD, state machine, checkpoint persistence,
stage output storage/retrieval, and concurrency control.

Implements US2 (Research Task Management) — T038 through T042.
All mutation operations use SELECT FOR UPDATE to prevent status-transition
race conditions; concurrency enforcement is folded into update_task_status
for atomicity.
"""

import uuid
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.database import get_mongo_db, get_postgres_session
from backend.models.task import ResearchTask
from backend.utils.datetime import now_dt
from backend.utils.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Internal Helpers
# ═══════════════════════════════════════════════════════════════════════

async def _get_task_for_user(
    session: AsyncSession,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> ResearchTask:
    """
    Fetch a task scoped to the owning user.

    Args:
        session: An active async session.
        task_id: Task UUID.
        user_id: Owning user UUID.
        for_update: If True, apply SELECT ... FOR UPDATE to lock the row
            until the transaction commits (prevents concurrent status races).

    Returns:
        The ResearchTask ORM instance.

    Raises:
        ValueError: If the task does not exist for this user.
    """
    query = select(ResearchTask).where(
        ResearchTask.id == task_id,
        ResearchTask.user_id == user_id,
    )
    if for_update:
        query = query.with_for_update()

    result = await session.execute(query)
    task = result.scalar_one_or_none()
    if task is None:
        logger.warning(
            "task_not_found",
            task_id=str(task_id),
            user_id=str(user_id),
        )
        raise ValueError(f"任务 {task_id} 不存在")
    return task


# ═══════════════════════════════════════════════════════════════════════
# T038: State Machine
# ═══════════════════════════════════════════════════════════════════════

VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":   {"running"},
    "running":   {"paused", "completed", "failed"},
    "paused":    {"running"},
    "failed":    {"pending"},
    "completed": set(),
}


def validate_transition(from_status: str, to_status: str) -> None:
    """
    Raise ValueError if the state transition is not allowed.

    Args:
        from_status: Current task status.
        to_status: Desired task status.

    Raises:
        ValueError: If the transition is not in VALID_TRANSITIONS.
    """
    allowed = VALID_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        detail = ", ".join(sorted(allowed)) if allowed else "(terminal)"
        logger.warning(
            "task_transition_rejected",
            from_status=from_status,
            to_status=to_status,
            allowed=detail,
        )
        raise ValueError(
            f"无效的状态变更: {from_status} → {to_status}，允许的操作: {detail}"
        )
    logger.info(
        "task_transition_validated",
        from_status=from_status,
        to_status=to_status,
    )


async def update_task_status(
    task_id: uuid.UUID,
    new_status: str,
    *,
    user_id: uuid.UUID,
    **extra_fields: Any,
) -> ResearchTask:
    """
    Atomically validate and update a task's status with user-scoped lookup.

    Uses SELECT FOR UPDATE to lock the row, preventing concurrent
    transitions from racing.  When ``new_status == "running"`` the
    per-user concurrency limit is enforced inside the same lock scope,
    so two parallel starts cannot both pass the check.

    Args:
        task_id: The task to update.
        new_status: Target status (must be a valid transition).
        user_id: Owning user (enforces data isolation).
        **extra_fields: Additional columns to update (e.g. error_message).

    Returns:
        The refreshed ResearchTask ORM instance.

    Raises:
        ValueError: If the task is not found or the transition is invalid.
        RuntimeError: If the concurrency limit is reached (only for →running).
    """
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id, for_update=True)

        old_status = task.status
        validate_transition(old_status, new_status)

        # ── Concurrency guard (T042): atomic with the status write ──
        # Lock ALL running rows for this user (not count with FOR UPDATE —
        # PostgreSQL forbids FOR UPDATE with aggregate functions).  Counting
        # the locked rows in Python gives the same result while keeping the
        # serialisation guarantee: another concurrent start will block until
        # we commit, then see our newly created running row.
        if new_status == "running":
            limit = settings.max_concurrent_tasks_per_user
            lock_result = await session.execute(
                select(ResearchTask)
                .where(
                    and_(
                        ResearchTask.user_id == user_id,
                        ResearchTask.status == "running",
                    )
                )
                .with_for_update()
            )
            running = len(lock_result.scalars().all())
            if running >= limit:
                logger.warning(
                    "concurrency_limit_reached",
                    user_id=str(user_id),
                    running=running,
                    limit=limit,
                )
                raise RuntimeError(
                    f"已达到最大并发任务数 {limit}，当前有 {running} 个运行中的任务，请等待任务完成或暂停后重试"
                )
            logger.info(
                "concurrency_check_passed",
                user_id=str(user_id),
                running=running,
                limit=limit,
            )

        task.status = new_status
        task.updated_at = now_dt()

        for key, value in extra_fields.items():
            if hasattr(task, key):
                setattr(task, key, value)

        await session.commit()
        await session.refresh(task)

    logger.info(
        "task_status_changed",
        task_id=str(task_id),
        old_status=old_status,
        new_status=new_status,
        extra=extra_fields,
    )
    return task


# ═══════════════════════════════════════════════════════════════════════
# T039: Task CRUD
# ═══════════════════════════════════════════════════════════════════════

async def create_task(
    user_id: uuid.UUID,
    topic: str,
    tags: list[str] | None = None,
    config: dict | None = None,
) -> ResearchTask:
    """
    Create a new research task for the given user.

    Validates topic ≥ 10 characters (spec constraint).

    Returns:
        The newly created ResearchTask ORM instance.

    Raises:
        ValueError: If topic is too short.
    """
    stripped = topic.strip()
    if len(stripped) < 5:
        logger.warning(
            "task_create_topic_too_short",
            user_id=str(user_id),
            length=len(stripped),
        )
        raise ValueError(
            f"研究主题至少需要 5 个字符（当前 {len(stripped)} 个）"
        )

    task = ResearchTask(
        user_id=user_id,
        topic=stripped,
        tags=tags or [],
        config_json=config or {},
    )

    session = get_postgres_session()
    async with session:
        session.add(task)
        await session.commit()
        await session.refresh(task)

    logger.info(
        "task_created",
        task_id=str(task.id),
        user_id=str(user_id),
        topic=stripped[:80],
    )
    return task


async def get_task(task_id: uuid.UUID, user_id: uuid.UUID) -> ResearchTask:
    """
    Retrieve a single task scoped to its owner (user isolation per FR-020).

    Raises:
        ValueError: If the task does not exist for this user.
    """
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id)

    return task


async def list_tasks(
    user_id: uuid.UUID,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """
    List tasks for a user with optional status filter and pagination.

    Returns:
        {"tasks": list[ResearchTask], "total": int, "page": int, "page_size": int}
    """
    session = get_postgres_session()
    async with session:
        conditions = [ResearchTask.user_id == user_id]
        if status:
            if status not in ResearchTask.VALID_STATUSES:
                logger.warning(
                    "task_list_invalid_status",
                    user_id=str(user_id),
                    status=status,
                )
                raise ValueError(
                    f"无效的状态 '{status}'，有效值: {ResearchTask.VALID_STATUSES}"
                )
            conditions.append(ResearchTask.status == status)

        # Total
        count_result = await session.execute(
            select(func.count()).select_from(ResearchTask).where(and_(*conditions))
        )
        total = count_result.scalar() or 0

        # Page
        offset = max(0, (page - 1)) * page_size
        query = (
            select(ResearchTask)
            .where(and_(*conditions))
            .order_by(ResearchTask.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        results = await session.execute(query)
        tasks = results.scalars().all()

    logger.info(
        "tasks_listed",
        user_id=str(user_id),
        status=status,
        total=total,
        page=page,
    )
    return {
        "tasks": list(tasks),
        "total": total,
        "page": page,
        "page_size": page_size,
    }


async def delete_task(task_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """
    Delete a task and cascade-clean its report + MongoDB stage outputs.

    A running task cannot be deleted — pause or wait for completion first.

    Raises:
        ValueError: If the task is not found or is currently running.
    """
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id, for_update=True)

        if task.status == "running":
            logger.warning(
                "task_delete_blocked_running",
                task_id=str(task_id),
                user_id=str(user_id),
            )
            raise ValueError(
                "无法删除正在运行的任务，请先暂停后再删除"
            )

        # Cascading delete — the ORM relationship cascade handles the report
        await session.delete(task)
        await session.commit()

    # Clean up MongoDB stage outputs + workflow checkpoints (full lifecycle,
    # Constitution IV — deletion cascades to all related data).
    try:
        db = get_mongo_db()
        task_id_str = str(task_id)
        for coll_name in STAGE_COLLECTIONS.values():
            result = await db[coll_name].delete_many({"task_id": task_id_str})
            if result.deleted_count:
                logger.info(
                    "mongo_stage_output_deleted",
                    task_id=task_id_str,
                    collection=coll_name,
                    deleted=result.deleted_count,
                )
        # Also remove the workflow checkpoint snapshot.
        cp_result = await db["research_checkpoints"].delete_many({"task_id": task_id_str})
        if cp_result.deleted_count:
            logger.info(
                "mongo_checkpoint_deleted",
                task_id=task_id_str,
                deleted=cp_result.deleted_count,
            )
    except Exception:
        # MongoDB cleanup is best-effort; PG data is already gone
        logger.warning(
            "mongo_cleanup_failed",
            task_id=str(task_id),
            exc_info=True,
        )

    logger.info("task_deleted", task_id=str(task_id), user_id=str(user_id))


# ═══════════════════════════════════════════════════════════════════════
# T040: Checkpoint Persistence
# ═══════════════════════════════════════════════════════════════════════

async def save_checkpoint(
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    phase: str,
    message: str,
    elapsed_seconds: int | None = None,
) -> None:
    """
    Persist the current execution checkpoint to PostgreSQL.

    Called at each stage boundary so the task can be resumed from the
    last completed stage after a pause or restart (FR-013).

    Checkpoints are sequential by design (single workflow per task) so
    no FOR UPDATE lock is needed.

    Raises:
        ValueError: If the task does not exist for this user.
    """
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id)

        task.current_phase = phase
        task.progress_message = message
        task.updated_at = now_dt()
        if elapsed_seconds is not None:
            task.elapsed_seconds = elapsed_seconds

        await session.commit()

    logger.info(
        "checkpoint_saved",
        task_id=str(task_id),
        phase=phase,
        message=message[:120],
        elapsed_seconds=elapsed_seconds,
    )


# ═══════════════════════════════════════════════════════════════════════
# T041: Stage Output Storage / Retrieval (MongoDB)
# ═══════════════════════════════════════════════════════════════════════

STAGE_COLLECTIONS: dict[str, str] = {
    "plan":      "research_plans",
    "plan_rev":  "research_plans",   # revised plans share the same collection
    "retrieval": "retrieval_results",
    "analyze":   "knowledge_summaries",
    "gap":       "knowledge_gaps",
}


async def store_stage_output(
    task_id: uuid.UUID,
    stage: str,
    data: dict | list[dict],
) -> None:
    """
    Persist a stage's output document(s) to MongoDB.

    Args:
        task_id: The owning task.
        stage: One of {"plan", "retrieval", "analyze", "gap"}.
        data: A single document dict or a list of document dicts.

    Raises:
        ValueError: If the stage name is unknown.
    """
    collection_name = STAGE_COLLECTIONS.get(stage)
    if collection_name is None:
        raise ValueError(
            f"未知阶段 '{stage}'，有效值: {list(STAGE_COLLECTIONS.keys())}"
        )

    db = get_mongo_db()
    collection = db[collection_name]
    task_id_str = str(task_id)

    try:
        if isinstance(data, list):
            docs = [{"task_id": task_id_str, **item} for item in data]
            if docs:
                result = await collection.insert_many(docs)
                logger.info(
                    "stage_output_stored",
                    task_id=task_id_str,
                    stage=stage,
                    count=len(result.inserted_ids),
                )
            else:
                logger.info(
                    "stage_output_stored_empty",
                    task_id=task_id_str,
                    stage=stage,
                )
        else:
            doc = {"task_id": task_id_str, **data}
            await collection.insert_one(doc)
            logger.info(
                "stage_output_stored",
                task_id=task_id_str,
                stage=stage,
                count=1,
            )
    except Exception:
        logger.error(
            "stage_output_store_failed",
            task_id=task_id_str,
            stage=stage,
            exc_info=True,
        )
        raise


async def get_stage_outputs(
    task_id: uuid.UUID,
    stage: str,
    *,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """
    Retrieve all stage output documents for a task from MongoDB.

    Args:
        task_id: The owning task.
        stage: One of {"plan", "retrieval", "analyze", "gap"}.
        limit: Maximum documents to return.

    Returns:
        List of documents with ``_id`` converted to string.

    Raises:
        ValueError: If the stage name is unknown.
    """
    collection_name = STAGE_COLLECTIONS.get(stage)
    if collection_name is None:
        raise ValueError(
            f"未知阶段 '{stage}'，有效值: {list(STAGE_COLLECTIONS.keys())}"
        )

    db = get_mongo_db()
    collection = db[collection_name]

    try:
        cursor = collection.find({"task_id": str(task_id)}).limit(limit)
        docs = await cursor.to_list(length=limit)

        for doc in docs:
            doc["_id"] = str(doc["_id"])

        logger.info(
            "stage_outputs_retrieved",
            task_id=str(task_id),
            stage=stage,
            count=len(docs),
        )
        return docs
    except Exception:
        logger.error(
            "stage_outputs_retrieve_failed",
            task_id=str(task_id),
            stage=stage,
            exc_info=True,
        )
        raise


# ── T114: Tag CRUD ───────────────────────────────────────────────────


async def add_tag(task_id: uuid.UUID, user_id: uuid.UUID, tag: str) -> list[str]:
    """Add a tag to a research task. Returns the updated tag list."""
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id)
        current = list(task.tags or [])
        if tag not in current:
            current.append(tag)
            task.tags = current
            await session.commit()
    return current


async def remove_tag(task_id: uuid.UUID, user_id: uuid.UUID, tag: str) -> list[str]:
    """Remove a tag from a research task. Returns the updated tag list."""
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id)
        current = list(task.tags or [])
        if tag in current:
            current.remove(tag)
            task.tags = current
            await session.commit()
    return current


async def get_tags(task_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    """Get all tags for a research task."""
    session = get_postgres_session()
    async with session:
        task = await _get_task_for_user(session, task_id, user_id)
        return list(task.tags or [])


