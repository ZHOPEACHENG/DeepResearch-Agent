"""
FastAPI dependency injection utilities.

Provides:
- get_current_user: JWT extraction → DB lookup → User ORM injection
- get_current_active_user: extends get_current_user with active-check
- User isolation: all data-access queries MUST be scoped to current user
"""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select

from backend.core.database import get_postgres_session
from backend.core.security import decode_token
from backend.models.user import User

_auth_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_auth_scheme),
) -> User:
    """
    Extract and validate JWT from Authorization header, return the authenticated User.

    Raises 401 if token is missing, expired, or invalid.
    Raises 401 if user does not exist or is deactivated.
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
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if payload.type != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not an access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Lookup user
    session = get_postgres_session()
    async with session.begin():
        result = await session.execute(
            select(User).where(User.id == payload.sub)
        )
        user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated",
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """Convenience alias. Enforces active check (already done in get_current_user)."""
    return current_user


def user_scoped_query(user_id: UUID):
    """
    Return a filter condition that restricts a query to the given user.

    Usage:
        session.execute(select(ResearchTask).where(user_scoped_query(user_id)))
    """
    from backend.models.task import ResearchTask
    # This is a closure — used in service layer to build user-scoped where clauses
    pass  # Implemented per-model in service layer for clarity; this function
    # documents the pattern: always add `.where(Model.user_id == current_user.id)`
