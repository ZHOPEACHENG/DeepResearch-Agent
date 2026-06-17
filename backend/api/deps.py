"""
FastAPI dependency injection utilities.

Provides:
- get_current_user: JWT extraction → DB lookup → User ORM injection
- get_current_active_user: extends get_current_user with active-check
- User isolation: service-layer MUST filter queries by user.id from get_current_user
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, JWSError
from sqlalchemy import select

from backend.core.database import get_postgres_session
from backend.core.security import decode_token
from backend.models.user import User
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_auth_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_auth_scheme),
) -> User:
    """
    Extract and validate JWT from Authorization header, return the authenticated User.

    Raises 401 if token is missing, expired, or invalid.
    Raises 401 if user does not exist.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = decode_token(token)
    except (JWTError, JWSError):
        logger.warning("auth_token_invalid", token=token[:8] + "...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.type != "access":
        logger.warning("auth_wrong_token_type", actual=payload.type)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Lookup user — use async with to ensure session is closed after use
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User).where(User.id == payload.sub)
        )
        user = result.scalar_one_or_none()

    if user is None:
        logger.warning("auth_user_not_found", sub=payload.sub)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # ── Token version check ─────────────────────────────────────────
    # Rejects access tokens issued before a logout or refresh rotation.
    # This closes the 30-minute window where a revoked access token
    # would otherwise remain usable until natural expiry.
    if payload.ver != user.token_version:
        logger.warning(
            "auth_token_revoked",
            sub=payload.sub,
            token_ver=payload.ver,
            current_ver=user.token_version,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Identity + active status check. Use for all write/business endpoints."""
    if not current_user.is_active:
        logger.warning("auth_inactive_user", sub=str(current_user.id))
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )
    return current_user
