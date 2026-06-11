"""
Pydantic schemas for User-related request/response validation.

Defines the API contract for registration, login, profile operations.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


# ── Request Schemas ──────────────────────────────────────────────────

class UserRegisterRequest(BaseModel):
    """Request body for user registration."""
    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-zA-Z0-9_-]+$",
        examples=["researcher"],
    )
    email: EmailStr
    password: str = Field(
        min_length=8,
        max_length=128,
        description="Must be ≥8 characters, include upper + lower case + digit",
    )
    display_name: str | None = Field(default=None, max_length=100)
    institution: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_password_strength(self) -> "UserRegisterRequest":
        pwd = self.password
        if not any(c.isupper() for c in pwd):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in pwd):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in pwd):
            raise ValueError("Password must contain at least one digit")
        return self


class UserLoginRequest(BaseModel):
    """Request body for user login."""
    email: EmailStr
    password: str


class UserUpdateRequest(BaseModel):
    """Request body for updating user profile."""
    display_name: str | None = Field(default=None, max_length=100)
    institution: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None


class ChangePasswordRequest(BaseModel):
    """Request body for changing password."""
    old_password: str
    new_password: str = Field(
        min_length=8, max_length=128,
    )

    @model_validator(mode="after")
    def validate_new_password_strength(self) -> "ChangePasswordRequest":
        pwd = self.new_password
        if not any(c.isupper() for c in pwd):
            raise ValueError("New password must contain at least one uppercase letter")
        if not any(c.islower() for c in pwd):
            raise ValueError("New password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in pwd):
            raise ValueError("New password must contain at least one digit")
        return self


# ── Response Schemas ─────────────────────────────────────────────────

class TokenPair(BaseModel):
    """Access + refresh token pair returned after login/refresh."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserRead(BaseModel):
    """Public user profile (returned by API)."""
    id: UUID
    username: str
    email: str
    display_name: str | None = None
    institution: str | None = None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
