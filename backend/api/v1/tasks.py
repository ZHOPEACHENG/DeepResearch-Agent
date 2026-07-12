"""
Task API endpoints — CRUD + lifecycle control (US2).

Endpoints:
- GET    /tasks             List tasks (status filter + pagination)
- POST   /tasks             Create a new research task
- GET    /tasks/{task_id}   Get task detail
- DELETE /tasks/{task_id}   Delete task (cascade)
- POST   /tasks/{task_id}/start    Start research execution
- POST   /tasks/{task_id}/pause    Pause a running task
- POST   /tasks/{task_id}/resume   Resume a paused task
"""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.api.deps import get_current_active_user
from backend.models.user import User
from backend.schemas.task import TaskCreateRequest, TaskListResponse, TaskRead, TaskStatusRead
from backend.services import task_service
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/tasks")

# ── Conversion Helpers ──────────────────────────────────────────────────

def _task_to_read(task) -> TaskRead:
    """Convert an ORM ResearchTask to a TaskRead response."""
    return TaskRead(
        id=task.id,
        user_id=task.user_id,
        topic=task.topic,
        status=task.status,
        current_phase=task.current_phase,
        progress_pct=_compute_progress(task),
        progress_message=task.progress_message,
        elapsed_seconds=task.elapsed_seconds,
        tags=task.tags or [],
        error_message=task.error_message,
        retry_count=task.retry_count,
        config_json=task.config_json or {},
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _task_to_status(task) -> TaskStatusRead:
    """Convert an ORM ResearchTask to a TaskStatusRead (lightweight list view)."""
    return TaskStatusRead(
        id=task.id,
        topic=task.topic,
        status=task.status,
        current_phase=task.current_phase,
        progress_pct=_compute_progress(task),
        progress_message=task.progress_message,
        elapsed_seconds=task.elapsed_seconds,
        tags=task.tags or [],
        created_at=task.created_at,
        updated_at=task.updated_at,
        completed_at=task.completed_at,
    )


def _compute_progress(task) -> int:
    """Estimate progress percentage from current phase (0-100)."""
    phases = ["planning", "retrieving", "analyzing", "synthesizing", "writing"]
    if not task.current_phase:
        return 0
    try:
        idx = phases.index(task.current_phase)
        return (idx + 1) * 20  # 20→40→60→80→100
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════════════════
# T043: CRUD Endpoints
# ═══════════════════════════════════════════════════════════════════════

@router.get("", response_model=TaskListResponse)
async def list_tasks(
    status_filter: str | None = None,
    tag: str | None = Query(default=None, description="按标签筛选"),
    page: int = 1,
    page_size: int = 20,
    current_user: User = Depends(get_current_active_user),
):
    """
    List the authenticated user's tasks.

    - **status**: Optional filter (pending/running/paused/completed/failed).
    - **page** / **page_size**: Pagination (default 20, max 100).
    """
    logger.info(
        "api_list_tasks",
        user_id=str(current_user.id),
        status=status_filter,
        page=page,
    )
    result = await task_service.list_tasks(
        current_user.id,
        status=status_filter,
        page=page,
        page_size=min(page_size, 100),
    )
    if tag:
        result["tasks"] = [t for t in result["tasks"] if tag in (t.tags or [])]
        result["total"] = len(result["tasks"])
    items = [_task_to_status(t) for t in result["tasks"]]
    return TaskListResponse(
        items=items,
        total=result["total"],
        page=result["page"],
        page_size=result["page_size"],
    )


@router.post("", response_model=TaskRead, status_code=status.HTTP_201_CREATED)
async def create_task(
    body: TaskCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Create a new research task.

    - **topic**: Research topic (10-2000 characters).
    """
    logger.info(
        "api_create_task",
        user_id=str(current_user.id),
        topic_preview=body.topic[:60],
    )
    try:
        task = await task_service.create_task(
            user_id=current_user.id,
            topic=body.topic,
        )
    except ValueError as e:
        logger.warning(
            "api_create_task_rejected",
            user_id=str(current_user.id),
            error=str(e),
        )
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    return _task_to_read(task)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Get full detail for a single task (user-scoped).

    - **task_id**: UUID of the research task.
    """
    logger.info(
        "api_get_task",
        task_id=str(task_id),
        user_id=str(current_user.id),
    )
    try:
        task = await task_service.get_task(task_id, current_user.id)
    except ValueError as e:
        logger.warning(
            "api_get_task_not_found",
            task_id=str(task_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    return _task_to_read(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Delete a task and all associated data (cascade).

    Running tasks cannot be deleted — pause or wait for completion first.
    """
    logger.info(
        "api_delete_task",
        task_id=str(task_id),
        user_id=str(current_user.id),
    )
    try:
        await task_service.delete_task(task_id, current_user.id)
    except ValueError as e:
        detail = str(e)
        logger.warning(
            "api_delete_task_rejected",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=detail,
        )
        if "不存在" in detail or "not found" in detail.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
        if "running" in detail:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


# ═══════════════════════════════════════════════════════════════════════
# T044: Lifecycle Control Endpoints
# ═══════════════════════════════════════════════════════════════════════

@router.post("/{task_id}/start", response_model=TaskRead)
async def start_task(
    task_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Start executing a research task.

    Transitions: pending → running, failed → pending (retry).
    Enforces per-user concurrency limit (default: 3).
    """
    logger.info(
        "api_start_task",
        task_id=str(task_id),
        user_id=str(current_user.id),
    )
    try:
        task = await task_service.update_task_status(
            task_id,
            "running",
            user_id=current_user.id,
            started_at=datetime.now(timezone.utc),
        )
    except ValueError as e:
        detail = str(e)
        logger.warning(
            "api_start_task_rejected",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=detail,
        )
        if "不存在" in detail or "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )
    except RuntimeError as e:
        logger.warning(
            "api_start_task_limit",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=str(e),
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    return _task_to_read(task)


@router.post("/{task_id}/pause", response_model=TaskRead)
async def pause_task(
    task_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Pause a running research task.

    Transitions: running → paused.
    Current phase and progress are preserved for later resume.
    """
    logger.info(
        "api_pause_task",
        task_id=str(task_id),
        user_id=str(current_user.id),
    )
    try:
        task = await task_service.update_task_status(
            task_id,
            "paused",
            user_id=current_user.id,
        )
    except ValueError as e:
        detail = str(e)
        logger.warning(
            "api_pause_task_rejected",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=detail,
        )
        if "不存在" in detail or "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )

    return _task_to_read(task)


@router.post("/{task_id}/resume", response_model=TaskRead)
async def resume_task(
    task_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    """
    Resume a paused research task.

    Transitions: paused → running.
    Execution continues from the last saved checkpoint.
    """
    logger.info(
        "api_resume_task",
        task_id=str(task_id),
        user_id=str(current_user.id),
    )
    try:
        task = await task_service.update_task_status(
            task_id,
            "running",
            user_id=current_user.id,
        )
    except ValueError as e:
        detail = str(e)
        logger.warning(
            "api_resume_task_rejected",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=detail,
        )
        if "不存在" in detail or "not found" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=detail,
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )
    except RuntimeError as e:
        logger.warning(
            "api_resume_task_limit",
            task_id=str(task_id),
            user_id=str(current_user.id),
            error=str(e),
        )
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))

    return _task_to_read(task)
