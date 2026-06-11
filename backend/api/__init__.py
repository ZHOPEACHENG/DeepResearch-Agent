"""
FastAPI application-level configuration: error handlers, CORS, middleware.

Defines global error handlers in RFC 7807 Problem Details format.
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.schemas import ErrorResponse
from backend.utils.logging import get_logger

logger = get_logger(__name__)


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException,
) -> JSONResponse:
    """Handle HTTP exceptions (404, 422, etc.) with Problem Details format."""
    logger.warning(
        "http_exception",
        status_code=exc.status_code,
        detail=str(exc.detail),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            detail=str(exc.detail),
            status_code=exc.status_code,
            instance=request.url.path,
        ).model_dump(),
    )


async def generic_exception_handler(
    request: Request, exc: Exception,
) -> JSONResponse:
    """Handle unhandled exceptions (500). Never expose internal details."""
    logger.error(
        "unhandled_exception",
        error=str(exc),
        path=request.url.path,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            detail="Internal server error",
            status_code=500,
            instance=request.url.path,
        ).model_dump(),
    )
