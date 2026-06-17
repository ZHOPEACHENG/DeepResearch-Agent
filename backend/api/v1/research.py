"""
Research flow endpoints — SSE streaming, stage outputs.

Phase 3 (US2): stage outputs retrieval
Phase 4 (US1): SSE streaming endpoint (T065)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from backend.api.deps import get_current_active_user
from backend.core.database import get_postgres_session
from backend.models.report import ResearchReport
from backend.models.user import User
from backend.services import task_service
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/research")


# ═══════════════════════════════════════════════════════════════════════
# T045: Stage Outputs
# ═══════════════════════════════════════════════════════════════════════

@router.get("/{task_id}/stage-outputs")
async def get_stage_outputs(
    task_id: UUID,
    stage: str | None = None,
    current_user: User = Depends(get_current_active_user),
):
    """
    Retrieve stage outputs for a research task.

    - **stage**: Filter by stage name (plan/retrieval/summary/gaps/report).
      Omit to return all stages.
    - **task_id**: UUID of the research task.

    The ``report`` stage is read from PostgreSQL; all other stages
    are stored in MongoDB.
    """
    logger.info(
        "api_get_stage_outputs",
        task_id=str(task_id),
        user_id=str(current_user.id),
        stage=stage,
    )

    # Verify the task exists and belongs to the user
    try:
        task = await task_service.get_task(task_id, current_user.id)
    except ValueError:
        logger.warning(
            "api_get_stage_outputs_task_not_found",
            task_id=str(task_id),
            user_id=str(current_user.id),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found",
        )

    # Build the response as plain dicts (MongoDB stores string UUIDs,
    # which don't cleanly map to Pydantic UUID fields)
    result: dict = {"taskId": str(task_id)}

    def _want(name: str) -> bool:
        return stage is None or stage == name

    # ── Plan ──────────────────────────────────────────────────────
    if _want("plan"):
        try:
            docs = await task_service.get_stage_outputs(task_id, "plan")
            result["plan"] = docs[0] if docs else None
        except Exception:
            logger.error(
                "api_stage_output_plan_failed",
                task_id=str(task_id),
                exc_info=True,
            )
            result["plan"] = None

    # ── Retrieval results (grouped by round) ───────────────────────
    if _want("retrieval"):
        try:
            docs = await task_service.get_stage_outputs(task_id, "retrieval")
            if docs:
                by_round: dict[int, list] = {}
                for doc in docs:
                    rn = doc.get("round_number", 1)
                    by_round.setdefault(rn, []).append(doc)
                result["retrievalRounds"] = [by_round[rn] for rn in sorted(by_round)]
            else:
                result["retrievalRounds"] = []
        except Exception:
            logger.error(
                "api_stage_output_retrieval_failed",
                task_id=str(task_id),
                exc_info=True,
            )
            result["retrievalRounds"] = []

    # ── Knowledge summaries ────────────────────────────────────────
    if _want("summary"):
        try:
            result["knowledgeSummaries"] = await task_service.get_stage_outputs(task_id, "analyze")
        except Exception:
            logger.error(
                "api_stage_output_summary_failed",
                task_id=str(task_id),
                exc_info=True,
            )
            result["knowledgeSummaries"] = []

    # ── Knowledge gaps ─────────────────────────────────────────────
    if _want("gaps"):
        try:
            result["knowledgeGaps"] = await task_service.get_stage_outputs(task_id, "gap")
        except Exception:
            logger.error(
                "api_stage_output_gaps_failed",
                task_id=str(task_id),
                exc_info=True,
            )
            result["knowledgeGaps"] = []

    # ── Report (PostgreSQL) ────────────────────────────────────────
    if _want("report"):
        try:
            session = get_postgres_session()
            async with session:
                query_result = await session.execute(
                    select(ResearchReport).where(ResearchReport.task_id == task_id)
                )
                report = query_result.scalar_one_or_none()
                if report:
                    result["report"] = {
                        "id": str(report.id),
                        "taskId": str(report.task_id),
                        "title": report.title,
                        "abstract": report.abstract,
                        "sections": report.sections_json,
                        "citations": report.citations_json,
                        "gapNotes": report.gap_notes,
                        "createdAt": report.created_at.isoformat() if report.created_at else None,
                        "updatedAt": report.updated_at.isoformat() if report.updated_at else None,
                    }
        except Exception:
            logger.error(
                "api_stage_output_report_failed",
                task_id=str(task_id),
                exc_info=True,
            )
            result["report"] = None

    return result
