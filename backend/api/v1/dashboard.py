"""
Dashboard stats endpoint — aggregated counts for the overview page.

T120: Frontend DashboardPage needs total conversations, active/completed
research counts, and knowledge-base document count.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from backend.api.deps import get_current_user
from backend.core.database import get_postgres_session
from backend.models.conversation import Conversation
from backend.models.document import Document
from backend.models.task import ResearchTask
from backend.models.user import User
from backend.schemas.dashboard import DashboardStatsResponse
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/dashboard")


@router.get(
    "/stats",
    response_model=DashboardStatsResponse,
    summary="Get dashboard statistics for the current user",
)
async def dashboard_stats(current_user: User = Depends(get_current_user)):
    """Return aggregated counts for the dashboard overview page."""
    user_id = current_user.id
    session = get_postgres_session()
    async with session:
        # Total conversations
        conv_total = await session.scalar(
            select(func.count(Conversation.id)).where(
                Conversation.user_id == user_id,
            )
        ) or 0

        # Active research (running + paused tasks)
        active_research = await session.scalar(
            select(func.count(ResearchTask.id)).where(
                ResearchTask.user_id == user_id,
                ResearchTask.status.in_(["running", "paused"]),
            )
        ) or 0

        # Completed research
        completed_research = await session.scalar(
            select(func.count(ResearchTask.id)).where(
                ResearchTask.user_id == user_id,
                ResearchTask.status == "completed",
            )
        ) or 0

        # Knowledge-base document count
        doc_count = await session.scalar(
            select(func.count(Document.id)).where(
                Document.user_id == user_id,
            )
        ) or 0

    logger.info(
        "dashboard_stats_retrieved",
        user_id=str(user_id),
        conversations=conv_total,
        active_research=active_research,
        completed=completed_research,
        documents=doc_count,
    )

    return DashboardStatsResponse(
        totalConversations=conv_total,
        activeResearchCount=active_research,
        completedResearchCount=completed_research,
        knowledgeDocCount=doc_count,
    )
