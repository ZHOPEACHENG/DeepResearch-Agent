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

from backend.api.v1 import auth, users, tasks, research, reports, knowledge

router = APIRouter(prefix="/api/v1")

# Include sub-routers
router.include_router(auth.router, tags=["Auth"])
router.include_router(users.router, tags=["Users"])
router.include_router(tasks.router, tags=["Tasks"])
router.include_router(research.router, tags=["Research"])
router.include_router(reports.router, tags=["Reports"])
router.include_router(knowledge.router, tags=["Knowledge"])
