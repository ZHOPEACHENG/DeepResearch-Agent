"""
v1 API router — aggregates all sub-routers under /api/v1.

Endpoints are grouped by OpenAPI tag:
- Auth: registration, login, token refresh, logout
- Users: profile management
- Tasks: research task CRUD + lifecycle control
- Research: SSE streaming, stage outputs
- Reports: report viewing, citation detail, export
- Knowledge: document upload, search, QA
"""

from fastapi import APIRouter

from backend.api.v1 import auth, conversations, dashboard, knowledge, reports, research, tasks, users
from backend.core.config import settings

router = APIRouter(prefix="/api/v1")

# Include sub-routers
router.include_router(auth.router, tags=["Auth"])
router.include_router(users.router, tags=["Users"])
router.include_router(conversations.router, tags=["Conversations"])
router.include_router(tasks.router, tags=["Tasks"], deprecated=True)
router.include_router(research.router, tags=["Research"])
router.include_router(reports.router, tags=["Reports"])
router.include_router(knowledge.router, tags=["Knowledge"])
router.include_router(dashboard.router, tags=["Dashboard"])


@router.get("/models")
async def get_available_models():
    """Return the list of models available for user selection in the chat UI."""
    return {"models": settings.available_models}
