"""Report viewing, citation detail, export endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from backend.api.deps import get_current_active_user
from backend.core.database import get_mongo_db, get_postgres_session
from backend.models.report import ResearchReport
from backend.models.user import User
from backend.utils.logging import get_logger

router = APIRouter(prefix="/reports")
logger = get_logger(__name__)


# ── T087: Citation detail ────────────────────────────────────────────────


@router.get("/{report_id}/citations/{index}")
async def get_citation_detail(
    report_id: str,
    index: int,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> dict:
    """Return full metadata for a single citation by its 1-based index
    in the report's citation list."""
    import uuid as _uuid

    try:
        rid = _uuid.UUID(report_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的报告 ID")

    session = get_postgres_session()
    async with session:
        from sqlalchemy import select

        result = await session.execute(
            select(ResearchReport).where(ResearchReport.id == rid)
        )
        report = result.scalar_one_or_none()
        if report is None:
            raise HTTPException(status_code=404, detail="报告不存在")

        citations: list = report.citations_json or []
        if index < 1 or index > len(citations):
            raise HTTPException(
                status_code=404,
                detail=f"引用索引 {index} 超出范围（共 {len(citations)} 条引用）",
            )

        cite: dict = citations[index - 1]
        source_ref = cite.get("sourceRef", "") or cite.get("retrieval_result_id", "")

        # ── Try to fetch full source content from MongoDB ──
        mongo_detail = None
        if source_ref:
            try:
                mongo = get_mongo_db()
                from bson import ObjectId

                doc = await mongo["retrieval_results"].find_one(
                    {"_id": ObjectId(source_ref[:24])}
                )
                if doc:
                    mongo_detail = {
                        "title": doc.get("title", ""),
                        "sourceType": doc.get("source_type", ""),
                        "url": doc.get("url", ""),
                        "abstract": doc.get("abstract", "") or doc.get("excerpt", ""),
                        "authors": doc.get("authors", []),
                        "publicationDate": doc.get("publication_date", ""),
                        "credibility": doc.get("credibility", cite.get("credibility", "medium")),
                        "rawContentAvailable": bool(doc.get("raw_content")),
                    }
            except Exception:
                logger.warning(
                    "citation_mongo_lookup_failed",
                    report_id=report_id,
                    index=index,
                    exc_info=True,
                )

        detail: dict = {
            "index": index,
            "text": cite.get("text", ""),
            "sourceRef": source_ref,
            "credibility": cite.get("credibility", "medium"),
        }

        if mongo_detail:
            detail["source"] = mongo_detail
        else:
            detail["source"] = {
                "title": cite.get("text", "未知来源"),
                "credibility": cite.get("credibility", "medium"),
            }

        return detail
