"""Knowledge base endpoints — document upload, search, QA. (Phase 7 implementation)"""

from fastapi import APIRouter

router = APIRouter(prefix="/knowledge")

# Endpoints implemented in Phase 7 (US5):
# POST /knowledge/documents
# GET /knowledge/documents
# GET /knowledge/documents/{id}
# DELETE /knowledge/documents/{id}
# GET /knowledge/search?q=
# POST /knowledge/ask
