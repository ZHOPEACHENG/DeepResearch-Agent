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
    sub: str       # user_id as string
    type: str = "access"   # "access" | "refresh"
    exp: int | None = None


def create_access_token(user_id: str) -> str:
    """
    Create a short-lived JWT access token.

    Expiry configured via JWT_ACCESS_TOKEN_EXPIRE_MINUTES (default: 30 min).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> str:
    """
    Create a long-lived JWT refresh token.

    Expiry configured via JWT_REFRESH_TOKEN_EXPIRE_DAYS (default: 7 days).
    """
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.jwt_refresh_token_expire_days
    )
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT token.

    Raises:
        JWTError: If token is expired, malformed, or invalid.
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    return TokenPayload(
        sub=payload["sub"],
        type=payload.get("type", "access"),
        exp=payload.get("exp"),
    )


# ── Account Lockout ──────────────────────────────────────────────────

MAX_LOGIN_ATTEMPTS = 3
LOCKOUT_DURATION_MINUTES = 15


def is_account_locked(login_attempts: int, locked_until: datetime | None) -> bool:
    """
    Check if an account is currently locked due to too many failed login attempts.

    Returns True if account is locked, False if login can proceed.
    """
    if locked_until is None:
        return False
    if locked_until > datetime.now(timezone.utc):
        return True
    return False


def record_failed_login(login_attempts: int) -> tuple[int, datetime | None]:
    """
    Increment failed login counter and potentially lock the account.

    Returns (new_attempts, locked_until).
    """
    attempts = login_attempts + 1
    if attempts >= MAX_LOGIN_ATTEMPTS:
        lock_until = datetime.now(timezone.utc) + timedelta(
            minutes=LOCKOUT_DURATION_MINUTES
        )
        return attempts, lock_until
    return attempts, None


def reset_login_attempts() -> tuple[int, None]:
    """Reset failed login counter after a successful login."""
    return 0, None
