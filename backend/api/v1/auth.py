"""
Auth endpoints — registration, login, token refresh, logout.

Token lifecycle: login/register → token pair with token_version embedded.
Refresh rotates the pair and increments version, invalidating all old tokens.
Logout increments version, invalidating all tokens immediately (full sign-out).

Note: access tokens are NOT validated against token_version on every request
to avoid a DB query per API call. A stolen access token remains usable for up
to its TTL (default 30 min). Real-time revocation of access tokens requires a
server-side deny-list (not yet implemented).
"""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import get_current_user
from backend.models.user import User
from backend.schemas.user import (
    RefreshRequest,
    TokenPair,
    UserLoginRequest,
    UserRegisterRequest,
)
from backend.services.auth_service import (
    AccountLockedError,
    login_user,
    logout_user,
    refresh_access_token,
    register_user,
)
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/auth")


@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(req: UserRegisterRequest):
    """
    Register a new account and return an access + refresh token pair.

    Requires unique username (3–50 chars, alphanumeric + underscore/hyphen)
    and unique email. Password must be ≥8 chars with upper + lower + digit.
    """
    try:
        token_pair = await register_user(req)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    except Exception:
        logger.error(
            "register_unexpected_error",
            username=req.username,
            email=str(req.email),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="注册失败，请稍后重试",
        )
    return token_pair


@router.post(
    "/login",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and receive tokens",
)
async def login(req: UserLoginRequest):
    """
    Authenticate with email + password.

    Returns access + refresh token pair on success.
    - 401: Invalid credentials
    - 423: Account temporarily locked after 5 consecutive failures
    """
    try:
        token_pair = await login_user(str(req.email), req.password)
    except AccountLockedError as e:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception:
        logger.error("login_unexpected_error", email=str(req.email), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="登录失败，请稍后重试",
        )
    return token_pair


@router.post(
    "/refresh",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Refresh access token (token rotation)",
)
async def refresh(req: RefreshRequest):
    """
    Exchange a valid refresh token for a new access + refresh token pair.

    - 401: Refresh token is invalid, expired, or wrong type.
    """
    try:
        token_pair = await refresh_access_token(req.refresh_token)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )
    except Exception:
        logger.error("refresh_unexpected_error", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="令牌刷新失败，请重新登录",
        )
    return token_pair


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out and invalidate all tokens",
)
async def logout(current_user: User = Depends(get_current_user)):
    """
    Log out the current user — increments token_version to immediately
    invalidate ALL refresh tokens across all devices.

    Note: existing access tokens remain valid until their natural expiry
    (up to 30 minutes). The client should still discard its tokens.
    """
    try:
        await logout_user(str(current_user.id))
    except Exception:
        logger.error(
            "logout_unexpected_error",
            user_id=str(current_user.id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="退出登录失败，请重试",
        )
