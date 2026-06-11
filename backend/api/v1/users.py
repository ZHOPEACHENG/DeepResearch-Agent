"""User profile endpoints. (Phase 5 implementation)"""

from fastapi import APIRouter

router = APIRouter(prefix="/users")

# Endpoints implemented in Phase 5 (US3):
# GET /users/me
# PATCH /users/me
# PUT /users/me/password
