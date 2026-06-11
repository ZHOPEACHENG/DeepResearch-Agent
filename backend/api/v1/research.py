"""Research flow endpoints — SSE streaming, stage outputs. (Phase 3+4 implementation)"""

from fastapi import APIRouter

router = APIRouter(prefix="/research")

# Endpoints implemented in Phase 3 (US2) + Phase 4 (US1):
# GET /research/{task_id}/stream (SSE)
# GET /research/{task_id}/stage-outputs
