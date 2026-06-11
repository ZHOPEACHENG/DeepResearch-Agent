"""Report viewing, citation detail, export endpoints. (Phase 6+8 implementation)"""

from fastapi import APIRouter

router = APIRouter(prefix="/reports")

# Endpoints implemented in Phase 6 (US4) + Phase 8 (US6):
# GET /reports/{task_id}
# GET /reports/{task_id}/citations/{index}
# GET /reports/{task_id}/export?format=markdown|pdf
