"""
Security utilities: JWT token creation/validation + bcrypt password hashing.

Handles:
- Access token (short-lived) and refresh token (long-lived) generation
- Token validation and payload extraction
- bcrypt password hashing and verification
- Account lockout tracking
"""

from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from backend.core.config import settings
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ── Password Hashing ─────────────────────────────────────────────────

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return _pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return _pwd_context.verify(plain_password, hashed_password)


# ── JWT Token Management ─────────────────────────────────────────────

class TokenPayload(BaseModel):
    """Decoded JWT payload."""
    sub: str
    type: str = ""
    exp: int | None = None
    ver: int = 0  # token_version — incremented on refresh/logout to revoke old tokens


def create_access_token(user_id: str, token_version: int = 0) -> str:
    """
    Create a short-lived JWT access token.

    Expiry configured via JWT_ACCESS_TOKEN_EXPIRE_MINUTES (default: 30 min).
    The ver claim is embedded for completeness but not validated on every request
    (access tokens are short-lived; revocation is enforced at refresh time).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "type": "access",
        "ver": token_version,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    logger.info("access_token_created", sub=str(user_id), expires=expire.isoformat())
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str, token_version: int = 0) -> str:
    """
    Create a long-lived JWT refresh token.

    Expiry configured via JWT_REFRESH_TOKEN_EXPIRE_DAYS (default: 7 days).
    The ver claim binds the token to a specific token_version — when the version
    is incremented (on refresh or logout), all previously issued tokens are revoked.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "ver": token_version,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    logger.info(
        "refresh_token_created",
        sub=str(user_id),
        ver=token_version,
        expires=expire.isoformat(),
    )
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT token.

    Raises:
        JWTError: If token is expired, malformed, invalid, or missing required claims.
    """
    token_preview = token[:8] + "..." if len(token) > 8 else token
    try:
        raw = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        logger.warning("token_decode_failed", token=token_preview)
        raise

    sub = raw.get("sub")
    if sub is None:
        logger.warning("token_missing_sub", token=token_preview)
        raise JWTError("Token missing 'sub' claim")

    return TokenPayload(
        sub=sub,
        type=raw.get("type", ""),
        exp=raw.get("exp"),
        ver=raw.get("ver", 0),
    )


# ── Account Lockout ──────────────────────────────────────────────────

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 15


def is_account_locked(locked_until: datetime | None) -> bool:
    """
    Check if an account is currently locked due to too many failed login attempts.

    Returns True if account is locked, False if login can proceed.
    """
    if locked_until is None:
        return False
    if locked_until > datetime.now(timezone.utc):
        return True
    return False


def record_failed_login(login_attempts: int, user_id: str = "") -> tuple[int, datetime | None]:
    """
    Increment failed login counter and potentially lock the account.

    Returns (new_attempts, locked_until).
    """
    attempts = login_attempts + 1
    if attempts >= MAX_LOGIN_ATTEMPTS:
        lock_until = datetime.now(timezone.utc) + timedelta(
            minutes=LOCKOUT_DURATION_MINUTES
        )
        logger.warning(
            "account_locked",
            attempts=attempts,
            max_attempts=MAX_LOGIN_ATTEMPTS,
            locked_until=lock_until.isoformat(),
            user_id=user_id,
        )
        return attempts, lock_until
    logger.info("failed_login_recorded", attempts=attempts, user_id=user_id)
    return attempts, None


def reset_login_attempts() -> tuple[int, None]:
    """Reset failed login counter after a successful login."""
    return 0, None
