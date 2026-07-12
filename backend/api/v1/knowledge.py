"""Knowledge base endpoints — document upload, search, QA. (Phase 7 / US5)

POST   /knowledge/documents       — upload a document
GET    /knowledge/documents       — list user's documents
GET    /knowledge/documents/{id}  — get document metadata
DELETE /knowledge/documents/{id}  — delete a document
GET    /knowledge/search?q=       — keyword search
POST   /knowledge/ask             — natural-language QA
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from backend.api.deps import get_current_active_user
from backend.models.user import User
from backend.schemas.document import (
    AskRequest,
    AskResponse,
    DocumentList,
    DocumentRead,
    DocumentUploadResponse,
    SearchResults,
)
from backend.services import knowledge_service
from backend.utils.logging import get_logger

router = APIRouter(prefix="/knowledge")
logger = get_logger(__name__)

# ── Document CRUD ──────────────────────────────────────────────────────


@router.post(
    "/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    file: Annotated[
        UploadFile, File(description="文档文件 (PDF/DOCX/TXT/MD)")
    ],
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DocumentUploadResponse:
    """Upload a document to the user's knowledge base.

    Supported formats: PDF, DOCX, TXT, Markdown.
    Maximum file size: 50 MB.
    Password-protected PDFs are rejected.

    The document is processed asynchronously (extract → chunk → embed → index).
    Check ``processingStatus`` via ``GET /knowledge/documents/{id}`` to monitor progress.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    try:
        file_data = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="文件读取失败")

    try:
        result = await knowledge_service.upload_document(
            user_id=current_user.id,
            filename=file.filename,
            file_data=file_data,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.error("document_upload_failed", user_id=str(current_user.id), exc_info=True)
        raise HTTPException(status_code=500, detail="文档上传失败，请稍后重试")

    return result


@router.get("/documents", response_model=DocumentList)
async def list_documents(
    current_user: Annotated[User, Depends(get_current_active_user)],
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页数量"),
) -> DocumentList:
    """List all documents in the user's knowledge base, newest first."""
    return await knowledge_service.list_documents(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )


@router.get("/documents/{document_id}", response_model=DocumentRead)
async def get_document(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> DocumentRead:
    """Get metadata and processing status for a single document."""
    try:
        doc_id = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文档 ID")

    try:
        return await knowledge_service.get_document(doc_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="文档不存在")


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a document and all its indexed chunks."""
    try:
        doc_id = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文档 ID")

    try:
        await knowledge_service.delete_document(doc_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="文档不存在")


@router.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Return all text chunks of a document for preview."""
    try:
        doc_id = uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的文档 ID")

    try:
        return await knowledge_service.get_document_content(doc_id, current_user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="文档不存在")


# ── Search & QA ────────────────────────────────────────────────────────


@router.get("/search", response_model=SearchResults)
async def search_knowledge(
    current_user: Annotated[User, Depends(get_current_active_user)],
    q: str = Query(..., min_length=1, max_length=500, description="搜索关键词"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize", description="每页数量"),
) -> SearchResults:
    """Full-text keyword search across the user's knowledge base.

    Returns matching document chunks with relevance scores and
    highlighted excerpts.
    """
    return await knowledge_service.search_knowledge(
        user_id=current_user.id,
        query=q,
        page=page,
        page_size=page_size,
    )


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    body: AskRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> AskResponse:
    """Answer a natural-language question using the user's knowledge base.

    1. Embed the question → vector search for relevant chunks
    2. Build a prompt with retrieved context
    3. Generate an answer citing specific document sources

    The response includes ``sources`` listing each referenced document
    and the relevant excerpt.
    """
    return await knowledge_service.ask_question(
        user_id=current_user.id,
        body=body,
    )
