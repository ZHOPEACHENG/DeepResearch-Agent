"""
User profile endpoints — view, update profile, change password.

All endpoints require a valid access token. Profile updates are
partial (PATCH semantics): only provided fields are changed.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import get_current_active_user
from backend.models.user import User
from backend.schemas.user import (
    ChangePasswordRequest,
    UserRead,
    UserUpdateRequest,
)
from backend.services.auth_service import (
    change_user_password,
    get_user_profile,
    update_user_profile,
)
from backend.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/users")


@router.get(
    "/me",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
)
async def get_me(current_user: User = Depends(get_current_active_user)):
    """
    Return the authenticated user's full profile.
    """
    try:
        profile = await get_user_profile(str(current_user.id))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    except Exception:
        logger.error(
            "profile_read_unexpected_error",
            user_id=str(current_user.id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load profile",
        )
    return profile


@router.patch(
    "/me",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Update user profile (partial)",
)
async def update_me(
    req: UserUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Update the authenticated user's profile. Only provided fields are changed.

    - display_name: New display name (max 100 chars)
    - institution: New institution (max 200 chars)
    - email: New email — must be unique across all users
    """
    try:
        profile = await update_user_profile(
            user_id=str(current_user.id),
            display_name=req.display_name,
            institution=req.institution,
            email=str(req.email) if req.email else None,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT if "already" in str(e).lower()
            else status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        logger.error(
            "profile_update_unexpected_error",
            user_id=str(current_user.id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile",
        )
    return profile


@router.put(
    "/me/password",
    status_code=status.HTTP_200_OK,
    summary="Change password",
)
async def change_password(
    req: ChangePasswordRequest,
    current_user: User = Depends(get_current_active_user),
):
    """
    Change the authenticated user's password.

    Requires the current (old) password for verification.
    New password must be ≥8 chars with upper + lower + digit.
    """
    try:
        await change_user_password(
            user_id=str(current_user.id),
            old_password=req.old_password,
            new_password=req.new_password,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    except Exception:
        logger.error(
            "password_change_unexpected_error",
            user_id=str(current_user.id),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to change password",
        )
    return {"detail": "Password changed successfully"}
