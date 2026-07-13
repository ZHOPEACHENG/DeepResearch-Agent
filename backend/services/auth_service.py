"""
Auth service — registration, login, token refresh, logout, account lockout.

Coordinates between:
- backend.core.security (JWT, password hashing, lockout tracking)
- backend.models.user (User ORM)
- backend.core.database (PostgreSQL session)
- backend.schemas.user (Pydantic request/response validation)

All operations are user-scoped and log structured audit events.
"""

from datetime import UTC, datetime

from jose import JWTError
from sqlalchemy import select

import backend.models  # noqa: F401 — ensure all ORM models are registered
from backend.core.database import get_postgres_session
from backend.core.security import (
    MAX_LOGIN_ATTEMPTS,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_account_locked,
    record_failed_login,
    verify_password,
)
from backend.models import User
from backend.schemas.user import TokenPair, UserRead, UserRegisterRequest
from backend.utils.log_mask import mask_email, mask_username
from backend.utils.logging import get_logger

logger = get_logger(__name__)

class AccountLockedError(PermissionError):
    """Raised when an account is temporarily locked due to repeated failed login attempts."""
    pass


# Pre-computed bcrypt hash for constant-time dummy verification when a user
# is not found, preventing timing-based email enumeration.
# Hash of a fixed internal string — never corresponds to a real password.
_DUMMY_HASH = (
    "$2b$12$LJ3m4ys3LkPGQrFJIjFgEeXVBmJMR0ZqMiekcKn5oFNdx8QoXb2Gq"
)


# ── Registration ────────────────────────────────────────────────────────


async def register_user(req: UserRegisterRequest) -> TokenPair:
    """
    Register a new user account.

    1. Validate username and email uniqueness
    2. Hash password
    3. Persist User row
    4. Return access + refresh token pair (auto-login on registration)

    Raises:
        ValueError: If username or email already exists.
    """
    session = get_postgres_session()

    async with session:
        # Check uniqueness
        existing = await session.execute(
            select(User).where(
                (User.username == req.username) | (User.email == str(req.email))
            )
        )
        conflict = existing.scalars().first()
        if conflict is not None:
            conflict_field = (
                "username" if conflict.username == req.username else "email"
            )
            logger.warning(
                "register_conflict",
                username=mask_username(req.username),
                email=mask_email(str(req.email)),
                conflict=conflict_field,
            )
            raise ValueError("用户名或邮箱已被注册")

        # Create user
        user = User(
            username=req.username,
            email=str(req.email),
            password_hash=hash_password(req.password),
            display_name=req.display_name,
            institution=req.institution,
        )
        session.add(user)
        await session.flush()
        user_id = str(user.id)

        # Persist the user before generating tokens
        await session.commit()

    # Generate token pair (outside session to avoid holding connection)
    # New users start with token_version=0
    access_token = create_access_token(user_id, token_version=0)
    refresh_token = create_refresh_token(user_id, token_version=0)

    logger.info("user_registered", user_id=user_id, username=mask_username(req.username))
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


# ── Login ───────────────────────────────────────────────────────────────


async def login_user(email: str, password: str) -> TokenPair:
    """
    Authenticate a user by email and password.

    1. Lookup user by email
    2. Check account lockout (423 if locked)
    3. Verify password (401 if wrong)
    4. On success: reset failed attempts, issue token pair
    5. On failure: increment failed attempts, potentially lock account

    Raises:
        ValueError: Invalid email or password (generic message for security).
        PermissionError: Account is temporarily locked.
    """
    session = get_postgres_session()

    async with session:
        result = await session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()

        if user is None:
            # Constant-time mitigation: verify against a dummy hash so that
            # "user not found" and "wrong password" take the same bcrypt time.
            verify_password(password, _DUMMY_HASH)
            logger.warning("login_user_not_found")
            raise ValueError("邮箱或密码错误")

        # Check lockout
        if is_account_locked(user.locked_until):
            remaining = int((user.locked_until - datetime.now(UTC)).total_seconds())
            logger.warning(
                "login_account_locked",
                user_id=str(user.id),
                locked_until=user.locked_until.isoformat(),
            )
            raise AccountLockedError(
                f"账户因 {MAX_LOGIN_ATTEMPTS} 次登录失败已被锁定，请在 {remaining // 60 + 1} 分钟后重试"
            )

        # Verify password
        if not verify_password(password, user.password_hash):
            attempts, locked_until = record_failed_login(user.login_attempts, user_id=str(user.id))
            user.login_attempts = attempts
            user.locked_until = locked_until
            await session.commit()
            logger.warning(
                "login_password_invalid",
                user_id=str(user.id),
                attempts=attempts,
                locked=locked_until is not None,
            )
            raise ValueError("邮箱或密码错误")

        # Success — reset failed attempts
        attempts, locked_until = 0, None
        user.login_attempts = attempts
        user.locked_until = locked_until
        await session.commit()

        user_id = str(user.id)
        token_ver = user.token_version

    # Issue tokens outside session
    access_token = create_access_token(user_id, token_version=token_ver)
    refresh_token = create_refresh_token(user_id, token_version=token_ver)

    logger.info("user_logged_in", user_id=user_id)
    return TokenPair(access_token=access_token, refresh_token=refresh_token)


# ── Token Refresh ───────────────────────────────────────────────────────


async def refresh_access_token(refresh_token: str) -> TokenPair:
    """
    Validate a refresh token and issue a new token pair (rotation).

    1. Decode and validate the refresh token
    2. Verify it is a refresh-type token
    3. Verify the user still exists and is active
    4. Verify token_version matches — reject if token has been used before
    5. Atomically increment token_version to invalidate all old tokens
    6. Issue new access + refresh token pair with the new version

    If a token with a stale version is presented, it indicates either:
    - A previous refresh already consumed this token family (normal rotation)
    - Token reuse by an attacker (security event — logged at warning level)

    Raises:
        ValueError: Token is invalid, wrong type, revoked, or user not found/active.
    """
    # Decode the refresh token
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        logger.warning("refresh_token_decode_failed")
        raise ValueError("刷新令牌无效或已过期")

    if payload.type != "refresh":
        logger.warning("refresh_token_wrong_type", actual=payload.type)
        raise ValueError("令牌类型错误，非刷新令牌")

    # Verify user still exists and is active, and check token version.
    # SELECT ... FOR UPDATE locks the row to prevent concurrent refreshes
    # with the same token from both succeeding (race-condition protection).
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User)
            .where(User.id == payload.sub)
            .with_for_update()
        )
        user = result.scalar_one_or_none()

        if user is None:
            logger.warning("refresh_token_user_not_found", sub=payload.sub)
            raise ValueError("用户不存在")

        if not user.is_active:
            logger.warning("refresh_token_inactive_user", sub=payload.sub)
            raise ValueError("账户已被停用")

        # ── Token version check (rotation enforcement) ──────────────────
        if payload.ver != user.token_version:
            # Stale token: normal lifecycle (already rotated past this version).
            # Future version: data integrity anomaly — should never happen.
            event = "refresh_stale_token" if payload.ver < user.token_version \
                else "refresh_future_token_version"
            log_level = "info" if payload.ver < user.token_version else "warning"
            getattr(logger, log_level)(
                event,
                user_id=str(user.id),
                token_ver=payload.ver,
                current_ver=user.token_version,
            )
            raise ValueError("刷新令牌已失效，请重新登录")

        # Increment version — row is locked, no concurrent modification possible
        user.token_version += 1
        new_version = user.token_version
        await session.commit()

    user_id = str(user.id)

    # Issue new token pair with the updated version
    new_access_token = create_access_token(user_id, token_version=new_version)
    new_refresh_token = create_refresh_token(user_id, token_version=new_version)

    logger.info("token_refreshed", user_id=user_id, ver=new_version)
    return TokenPair(access_token=new_access_token, refresh_token=new_refresh_token)


# ── Logout ──────────────────────────────────────────────────────────────


async def logout_user(user_id: str) -> None:
    """
    Log out the user on ALL devices by incrementing token_version.

    This immediately invalidates all previously issued refresh tokens.
    Access tokens remain valid until their natural expiry (up to 30 min)
    — real-time access token revocation requires a deny-list (future work).

    Any refresh attempt with an old token after logout will fail because
    the token's ver claim no longer matches the incremented DB value.
    """
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            logger.warning("logout_user_not_found", user_id=user_id)
            return

        user.token_version += 1
        await session.commit()

    logger.info("user_logged_out", user_id=user_id, ver=user.token_version)


# ── Profile ─────────────────────────────────────────────────────────────


async def get_user_profile(user_id: str) -> UserRead:
    """Retrieve the authenticated user's profile."""
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

    if user is None:
        logger.warning("profile_read_user_not_found", user_id=user_id)
        raise ValueError("用户不存在")

    logger.info("profile_read", user_id=user_id)
    return UserRead.model_validate(user)


async def update_user_profile(
    user_id: str,
    display_name: str | None = None,
    institution: str | None = None,
    email: str | None = None,
) -> UserRead:
    """
    Update the authenticated user's profile fields.

    Only provided fields are updated (partial update). If email is changed,
    uniqueness is validated.

    Raises:
        ValueError: If the new email is already taken.
    """
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise ValueError("用户不存在")

        changed: list[str] = []

        if display_name is not None:
            user.display_name = display_name
            changed.append("display_name")
        if institution is not None:
            user.institution = institution
            changed.append("institution")

        if email is not None and email != user.email:
            # Validate uniqueness
            existing = await session.execute(
                select(User).where(User.email == email, User.id != user_id)
            )
            if existing.scalar_one_or_none() is not None:
                logger.warning(
                    "profile_update_email_conflict",
                    user_id=user_id,
                )
                raise ValueError(f"邮箱 '{email}' 已被使用")
            user.email = email
            changed.append("email")

        await session.commit()
        await session.refresh(user)
        profile = UserRead.model_validate(user)

    logger.info("user_profile_updated", user_id=user_id, changed_fields=changed)
    return profile


async def change_user_password(
    user_id: str, old_password: str, new_password: str
) -> None:
    """
    Change the authenticated user's password.

    1. Verify the old password
    2. Hash and persist the new password

    Raises:
        ValueError: If the old password is incorrect.
    """
    session = get_postgres_session()
    async with session:
        result = await session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

        if user is None:
            raise ValueError("用户不存在")

        if not verify_password(old_password, user.password_hash):
            logger.warning("password_change_bad_old", user_id=user_id)
            raise ValueError("当前密码错误")

        user.password_hash = hash_password(new_password)
        await session.commit()

    logger.info("user_password_changed", user_id=user_id)
