"""
Knowledge base service — RAG pipeline: upload, hybrid search, QA.
Phase 7 / US5 — polished RAG with proper pagination and error handling.

Pipeline:
  Upload → extract → chunk (sentence-boundary) → embed → ES index
  Search → hybrid (BM25 || vector kNN) → RRF fusion → paginate
  QA     → hybrid retrieve → batch context → LLM generate
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from backend.core.config import settings
from backend.core.database import get_es_client, get_postgres_session
from backend.models.document import Document
from backend.schemas.document import (
    MAX_FILE_SIZE_BYTES,
    AskRequest,
    AskResponse,
    AskSource,
    DocumentList,
    DocumentRead,
    DocumentUploadResponse,
    SearchResultItem,
    SearchResults,
    validate_file_type,
)
from backend.tools.parser import extract_text, is_password_protected_pdf, strip_markdown
from backend.utils.logging import get_logger

logger = get_logger(__name__)

_CHUNK_SIZE_CHARS = 800
_CHUNK_OVERLAP_CHARS = 150
_ES_INDEX = "document_chunks"
_RRF_K = 60

_UPLOAD_DIR = Path(settings.upload_dir)
if not _UPLOAD_DIR.is_absolute():
    _UPLOAD_DIR = Path(__file__).resolve().parent.parent / settings.upload_dir


def _ensure_upload_dir() -> Path:
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    return _UPLOAD_DIR


# ═══════════════════════════════════════════════════════════════════
# Document CRUD (unchanged)
# ═══════════════════════════════════════════════════════════════════


async def upload_document(
    user_id: uuid.UUID, filename: str, file_data: bytes,
) -> DocumentUploadResponse:
    file_type = validate_file_type(filename)
    file_size = len(file_data)
    if file_size == 0:
        raise ValueError("文件为空，无法上传")
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValueError(f"文件大小超过限制（最大 {MAX_FILE_SIZE_BYTES // (1024 * 1024)} MB）")
    if file_type == "pdf" and is_password_protected_pdf(file_data):
        raise ValueError("PDF 文件受密码保护，无法处理")

    upload_dir = _ensure_upload_dir()
    doc_id = uuid.uuid4()
    storage_path = upload_dir / f"{doc_id}.{file_type}"
    storage_path.write_bytes(file_data)

    session = get_postgres_session()
    async with session:
        doc = Document(
            id=doc_id, user_id=user_id, filename=filename,
            file_type=file_type, file_size_bytes=file_size,
            storage_path=str(storage_path), processing_status="pending",
            es_index_name=_ES_INDEX,
        )
        session.add(doc)
        await session.commit()

    logger.info("document_uploaded", doc_id=str(doc_id),
                filename=filename, file_type=file_type, file_size=file_size)
    asyncio.create_task(_process_document(doc_id, file_type, str(storage_path)))
    return DocumentUploadResponse(
        id=doc_id, filename=filename, file_type=file_type,
        file_size_bytes=file_size, processing_status="pending",
    )


async def ingest_text(
    user_id: uuid.UUID,
    content: str,
    title: str = "未命名文档",
    source_label: str = "",
) -> uuid.UUID | None:
    """Index plain text directly into the knowledge base — no file required.

    Used for auto-ingesting research reports so they become searchable.
    Chunking, embedding, and ES indexing run in the background.
    """
    if not content.strip():
        return None

    doc_id = uuid.uuid4()

    # Create a minimal DB record (diskless — content lives in ES only)
    session = get_postgres_session()
    async with session:
        doc = Document(
            id=doc_id, user_id=user_id, filename=title,
            file_type="txt", file_size_bytes=len(content.encode("utf-8")),
            storage_path="", processing_status="pending",
            es_index_name=_ES_INDEX,
        )
        session.add(doc)
        await session.commit()

    logger.info("text_ingested", doc_id=str(doc_id), title=title,
                char_count=len(content), source=source_label)

    # Process in background (chunk → embed → index)
    asyncio.create_task(_process_text(doc_id, content, source_label))
    return doc_id


async def list_documents(
    user_id: uuid.UUID, page: int = 1, page_size: int = 20,
) -> DocumentList:
    session = get_postgres_session()
    async with session:
        count_q = select(func.count(Document.id)).where(Document.user_id == user_id)
        total = (await session.execute(count_q)).scalar_one()
        items_q = (
            select(Document).where(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        )
        rows = (await session.execute(items_q)).scalars().all()
    return DocumentList(
        items=[DocumentRead.model_validate(d) for d in rows],
        total=total, page=page, page_size=page_size,
    )


async def get_document(doc_id: uuid.UUID, user_id: uuid.UUID) -> DocumentRead:
    session = get_postgres_session()
    async with session:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.user_id != user_id:
            raise ValueError("文档不存在")
        return DocumentRead.model_validate(doc)


async def get_document_content(
    doc_id: uuid.UUID, user_id: uuid.UUID,
) -> dict:
    """Fetch all chunks of a document from ES, sorted by chunk_index."""
    session = get_postgres_session()
    async with session:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.user_id != user_id:
            raise ValueError("文档不存在")

    try:
        es = get_es_client()
    except RuntimeError:
        return {"document_id": str(doc_id), "filename": doc.filename, "chunks": []}

    try:
        resp = await es.search(index=_ES_INDEX, body={
            "query": {
                "bool": {
                    "must": [{"term": {"document_id": str(doc_id)}}],
                    "filter": [{"term": {"user_id": str(user_id)}}],
                },
            },
            "sort": [{"chunk_index": "asc"}],
            "size": 1000,
            "_source": ["chunk_index", "text"],
        })
    except Exception:
        logger.warning("doc_content_fetch_failed", doc_id=str(doc_id), exc_info=True)
        return {"document_id": str(doc_id), "filename": doc.filename, "chunks": []}

    chunks = [
        {"chunkIndex": h["_source"]["chunk_index"], "text": h["_source"]["text"]}
        for h in resp.get("hits", {}).get("hits", [])
    ]
    return {"document_id": str(doc_id), "filename": doc.filename, "chunks": chunks}


async def delete_document(doc_id: uuid.UUID, user_id: uuid.UUID) -> None:
    session = get_postgres_session()
    async with session:
        doc = await session.get(Document, doc_id)
        if doc is None or doc.user_id != user_id:
            raise ValueError("文档不存在")
        try:
            p = Path(doc.storage_path)
            if p.exists():
                p.unlink()
        except OSError:
            pass
        try:
            es = get_es_client()
            await es.delete_by_query(
                index=_ES_INDEX,
                body={"query": {"term": {"document_id": str(doc_id)}}},
            )
        except Exception:
            pass
        await session.delete(doc)
        await session.commit()


# ═══════════════════════════════════════════════════════════════════
# Hybrid Search with proper pagination
# ═══════════════════════════════════════════════════════════════════


async def search_knowledge(
    user_id: uuid.UUID, query: str,
    page: int = 1, page_size: int = 20,
) -> SearchResults:
    """Hybrid search: BM25 + vector, RRF fusion, paginated."""
    if not query.strip():
        return SearchResults(query=query, results=[], total=0)

    try:
        es = get_es_client()
    except RuntimeError:
        return SearchResults(query=query, results=[], total=0)

    # Fetch enough candidates so we can paginate after fusion
    pool_size = page_size * 4

    bm25_hits, vector_hits = await asyncio.gather(
        _bm25_search(es, user_id, query, pool_size),
        _vector_search(es, user_id, query, pool_size),
        return_exceptions=True,
    )
    if isinstance(bm25_hits, Exception):
        logger.warning("search_bm25_failed", error=str(bm25_hits))
        bm25_hits = []
    if isinstance(vector_hits, Exception):
        logger.warning("search_vector_failed", error=str(vector_hits))
        vector_hits = []

    # RRF fusion — get enough to paginate
    fused = _rrf_fuse(bm25_hits, vector_hits, top_k=pool_size)

    # Slice for pagination
    start = (page - 1) * page_size
    page_hits = fused[start:start + page_size]
    total = len(fused)

    items = [_hit_to_result(h) for h in page_hits]
    items = await _enrich_filenames(user_id, items)

    return SearchResults(query=query, results=items, total=total)


# ═══════════════════════════════════════════════════════════════════
# RAG QA — simplified, fast
# ═══════════════════════════════════════════════════════════════════


async def ask_question(
    user_id: uuid.UUID, body: AskRequest,
) -> AskResponse:
    """RAG QA: hybrid retrieve → batch context → LLM generate."""
    question = body.question.strip()

    try:
        es = get_es_client()
    except RuntimeError:
        return AskResponse(
            question=question,
            answer="知识库搜索服务当前不可用，请稍后重试。",
            sources=[],
        )

    # ── 1. Hybrid retrieval ──────────────────────────────────────
    fetch_k = body.top_k * 3
    bm25_hits, vector_hits = await asyncio.gather(
        _bm25_search(es, user_id, question, fetch_k),
        _vector_search(es, user_id, question, fetch_k),
        return_exceptions=True,
    )
    if isinstance(bm25_hits, Exception):
        bm25_hits = []
    if isinstance(vector_hits, Exception):
        vector_hits = []

    if not bm25_hits and not vector_hits:
        return AskResponse(
            question=question,
            answer="未在知识库中找到相关内容。请尝试上传更多文档，或用不同的关键词搜索。",
            sources=[],
        )

    # ── 2. RRF fusion ────────────────────────────────────────────
    fused = _rrf_fuse(bm25_hits, vector_hits, top_k=body.top_k)

    # ── 3. Batch-fetch adjacent chunks (single ES query) ─────────
    context_blocks, sources = await _build_context(es, user_id, fused)

    if not context_blocks:
        return AskResponse(
            question=question,
            answer="未找到足够的上下文来回答这个问题。",
            sources=sources,
        )

    # ── 4. Generate answer ───────────────────────────────────────
    context = "\n\n---\n\n".join(context_blocks)
    try:
        from backend.tools.llm import get_chat_model
        model = get_chat_model("default", temperature=0.3, max_tokens=2048)
        prompt = _QA_PROMPT.format(context=context, question=question)
        response = await model.ainvoke([{"role": "user", "content": prompt}])
        answer = (getattr(response, "content", "") or "").strip()
    except Exception:
        logger.error("knowledge_qa_llm_failed", exc_info=True)
        return AskResponse(
            question=question, answer="回答生成失败，请稍后重试。",
            sources=sources, model=settings.llm_model,
        )

    return AskResponse(
        question=question, answer=answer, sources=sources,
        model=settings.llm_model,
    )


_QA_PROMPT = """你是一个精确、诚实的知识库问答助手。请严格基于以下检索到的文档内容回答问题。

## 规则
1. **只用**提供的文档内容作答，不得编造或使用外部知识。
2. 如果文档内容不足以回答，明确说明"根据已有文档，无法回答这个问题"，并简要指出缺少什么信息。
3. 回答时用 `[来源 N]` 标注引用的文档片段编号。
4. 如果多个来源有矛盾，指出矛盾并分别说明。
5. 用中文回答，尽量结构化呈现（用标题、列表等 Markdown 格式）。

## 文档内容

{context}

## 问题

{question}

## 回答"""


# ═══════════════════════════════════════════════════════════════════
# Retrieval helpers
# ═══════════════════════════════════════════════════════════════════

_ES_SOURCE = ["document_id", "chunk_index", "text", "page_number", "metadata"]


async def _bm25_search(es, user_id: uuid.UUID, query: str, size: int) -> list[dict]:
    resp = await es.search(index=_ES_INDEX, body={
        "query": {
            "bool": {
                "must": [{"match": {"text": query}}],
                "filter": [{"term": {"user_id": str(user_id)}}],
            },
        },
        "size": size,
        "_source": _ES_SOURCE,
    })
    return _parse_hits(resp)


async def _vector_search(es, user_id: uuid.UUID, query: str, size: int) -> list[dict]:
    try:
        from backend.tools.llm import get_embedding_model
        embedding = await get_embedding_model().aembed_query(query)
    except Exception:
        logger.warning("vector_embed_failed", exc_info=True)
        return []

    resp = await es.search(index=_ES_INDEX, body={
        "knn": {
            "field": "embedding",
            "query_vector": embedding,
            "k": size,
            "num_candidates": max(size, min(size * 3, 50)),
            "filter": {"term": {"user_id": str(user_id)}},
        },
        "size": size,
        "_source": _ES_SOURCE,
    })
    return _parse_hits(resp)


def _parse_hits(resp: dict) -> list[dict]:
    return [
        {
            "_id": h.get("_id", ""),
            "_score": float(h.get("_score", 0.0)),
            "document_id": h.get("_source", {}).get("document_id", ""),
            "chunk_index": h.get("_source", {}).get("chunk_index", 0),
            "text": h.get("_source", {}).get("text", ""),
            "page_number": h.get("_source", {}).get("page_number"),
            "metadata": h.get("_source", {}).get("metadata", {}),
        }
        for h in resp.get("hits", {}).get("hits", [])
    ]


def _hit_to_result(hit: dict) -> SearchResultItem:
    meta = hit.get("metadata") or {}
    return SearchResultItem(
        document_id=hit.get("document_id", ""),
        filename=meta.get("source_file", ""),
        chunk_index=hit.get("chunk_index", 0),
        text=hit.get("text", ""),
        page_number=hit.get("page_number"),
        score=hit.get("_score"),
        highlights=[],
    )


# ═══════════════════════════════════════════════════════════════════
# Reciprocal Rank Fusion
# ═══════════════════════════════════════════════════════════════════

def _rrf_fuse(list_a: list[dict], list_b: list[dict], top_k: int = 10) -> list[dict]:
    """Merge two ranked lists via RRF, deduplicate by (doc_id, chunk_index)."""
    scores: dict[tuple[str, int], float] = {}
    best: dict[tuple[str, int], dict] = {}

    for lst in (list_a, list_b):
        for rank, hit in enumerate(lst):
            key = (hit.get("document_id", ""), hit.get("chunk_index", 0))
            scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if key not in best or hit.get("_score", 0) > best[key].get("_score", 0):
                best[key] = hit

    ranked = sorted(scores, key=lambda k: scores[k], reverse=True)[:top_k]
    return [dict(best[k], _rrf_score=scores[k]) for k in ranked]


# ═══════════════════════════════════════════════════════════════════
# Context builder — single batch ES query for window expansion
# ═══════════════════════════════════════════════════════════════════

async def _build_context(
    es, user_id: uuid.UUID, hits: list[dict],
) -> tuple[list[str], list[AskSource]]:
    """Build QA context by fetching adjacent chunks in one batch ES query.

    Collects all (doc_id, chunk_idx-1, chunk_idx, chunk_idx+1) across
    all hits into a single ES terms query, then assembles the results.
    """
    # Collect unique (doc_id, chunk_index) triplets to fetch
    wanted: set[tuple[str, int]] = set()
    for h in hits:
        did = h.get("document_id", "")
        ci = h.get("chunk_index", 0)
        wanted.add((did, ci))
        if ci > 0:
            wanted.add((did, ci - 1))
        wanted.add((did, ci + 1))

    # Fetch all wanted chunks in one compound query
    chunk_texts: dict[tuple[str, int], str] = {}
    if wanted:
        try:
            # Use per-document range queries batched into should clauses
            by_doc: dict[str, list[int]] = {}
            for did, ci in wanted:
                by_doc.setdefault(did, []).append(ci)

            should_clauses: list[dict] = []
            for did, indices in by_doc.items():
                should_clauses.append({
                    "bool": {
                        "must": [{"term": {"document_id": did}}],
                        "filter": [
                            {"term": {"user_id": str(user_id)}},
                            {"terms": {"chunk_index": indices}},
                        ],
                    },
                })

            resp = await es.search(index=_ES_INDEX, body={
                "query": {"bool": {"should": should_clauses, "minimum_should_match": 1}},
                "size": len(wanted) + 10,
                "_source": ["text", "chunk_index", "document_id"],
            })
            for h in resp.get("hits", {}).get("hits", []):
                src = h.get("_source", {})
                chunk_texts[(src.get("document_id", ""), src.get("chunk_index", 0))] = \
                    src.get("text", "")
        except Exception:
            logger.warning("context_window_fetch_failed", exc_info=True)

    # Assemble context blocks (deduped)
    seen_keys: set[tuple[str, int]] = set()
    context_blocks: list[str] = []
    sources: list[AskSource] = []

    for hit in hits:
        did = hit.get("document_id", "")
        ci = hit.get("chunk_index", 0)
        if (did, ci) in seen_keys:
            continue
        seen_keys.add((did, ci))

        # Stitch prev + current + next
        parts = [chunk_texts.get((did, ci))] if chunk_texts.get((did, ci)) else []
        if ci > 0 and chunk_texts.get((did, ci - 1)):
            parts.insert(0, chunk_texts[(did, ci - 1)])
        if chunk_texts.get((did, ci + 1)):
            parts.append(chunk_texts[(did, ci + 1)])

        combined = "\n".join(p for p in parts if p) or hit.get("text", "")
        meta = hit.get("metadata") or {}
        label = meta.get("source_file", did)

        context_blocks.append(
            f"[来源 {len(context_blocks) + 1}] ({label}, 片段 {ci})\n{combined}"
        )
        sources.append(AskSource(
            document_id=did, filename=meta.get("source_file", ""),
            chunk_index=ci, excerpt=combined[:500],
            page_number=hit.get("page_number"),
        ))

    return context_blocks, sources


# ═══════════════════════════════════════════════════════════════════
# Text ingestion (diskless — for reports, etc.)
# ═══════════════════════════════════════════════════════════════════


async def _process_text(
    doc_id: uuid.UUID, text: str, source_label: str,
) -> None:
    """Chunk → embed → index for text that has no file on disk."""
    did = str(doc_id)

    session = get_postgres_session()
    async with session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            return
        owner_id = doc.user_id
        filename = doc.filename
        doc.processing_status = "processing"
        await session.commit()

    try:
        chunks = _chunk_text(text)
        if not chunks:
            raise RuntimeError("文本分块失败")
        logger.info("text_chunked", doc_id=did, chunk_count=len(chunks),
                    source=source_label)

        await _index_chunks(doc_id, owner_id, filename, "txt", chunks)

        s2 = get_postgres_session()
        async with s2:
            doc = await s2.get(Document, doc_id)
            if doc:
                doc.processing_status = "completed"
                doc.processed_at = datetime.now(UTC)
                await s2.commit()
        logger.info("text_processing_completed", doc_id=did,
                    chunk_count=len(chunks), source=source_label)

    except Exception as exc:
        logger.error("text_processing_failed", doc_id=did,
                     error=str(exc), source=source_label, exc_info=True)
        try:
            s3 = get_postgres_session()
            async with s3:
                doc = await s3.get(Document, doc_id)
                if doc:
                    doc.processing_status = "failed"
                    doc.processing_error = str(exc)[:500]
                    await s3.commit()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# Background document processing (file-based)
# ═══════════════════════════════════════════════════════════════════

async def _process_document(
    doc_id: uuid.UUID, file_type: str, storage_path: str,
) -> None:
    did = str(doc_id)
    session = get_postgres_session()
    async with session:
        doc = await session.get(Document, doc_id)
        if doc is None:
            return
        owner_id = doc.user_id
        filename = doc.filename
        doc.processing_status = "processing"
        await session.commit()

    try:
        text = extract_text(storage_path, file_type)
        if file_type == "md":
            text = strip_markdown(text)
        if not text.strip():
            raise RuntimeError("文档中未提取到可读文本内容")
        logger.info("document_text_extracted", doc_id=did, text_length=len(text))

        chunks = _chunk_text(text)
        if not chunks:
            raise RuntimeError("文本分块失败")
        logger.info("document_chunked", doc_id=did, chunk_count=len(chunks))

        await _index_chunks(doc_id, owner_id, filename, file_type, chunks)

        s2 = get_postgres_session()
        async with s2:
            doc = await s2.get(Document, doc_id)
            if doc:
                doc.processing_status = "completed"
                doc.processed_at = datetime.now(UTC)
                await s2.commit()
        logger.info("document_processing_completed", doc_id=did, chunk_count=len(chunks))

    except Exception as exc:
        logger.error("document_processing_failed", doc_id=did,
                     error=str(exc), exc_info=True)
        try:
            s3 = get_postgres_session()
            async with s3:
                doc = await s3.get(Document, doc_id)
                if doc:
                    doc.processing_status = "failed"
                    doc.processing_error = str(exc)[:500]
                    await s3.commit()
        except Exception:
            logger.error("document_failed_status_update_error", doc_id=did, exc_info=True)


# ═══════════════════════════════════════════════════════════════════
# Chunking — Chinese-aware sentence-boundary splitting
# ═══════════════════════════════════════════════════════════════════

# Match sentence-ending characters (Chinese + English)
_SENT_END = re.compile(r"[。！？.!?\n]")


def _chunk_text(text: str) -> list[dict[str, Any]]:
    """Split by sentence boundaries near target chunk size."""
    if not text.strip():
        return []

    chunks: list[dict[str, Any]] = []
    pos = 0
    idx = 0
    n = len(text)

    while pos < n:
        target = min(pos + _CHUNK_SIZE_CHARS, n)

        # Find last sentence boundary in [pos, target]
        cut = target
        for m in _SENT_END.finditer(text, pos, target):
            cut = m.end()

        # If no boundary found, expand search window forward a bit
        if cut == target and target < n:
            for m in _SENT_END.finditer(text, target, min(target + 200, n)):
                cut = m.end()
                break

        chunk_start = max(pos - _CHUNK_OVERLAP_CHARS, 0) if pos > 0 else pos
        chunk_text = text[chunk_start:cut].strip()

        if chunk_text:
            chunks.append({
                "text": chunk_text,
                "chunk_index": idx,
                "char_start": chunk_start,
                "char_end": cut,
            })
            idx += 1

        pos = cut

    return chunks


# ═══════════════════════════════════════════════════════════════════
# ES indexing
# ═══════════════════════════════════════════════════════════════════

async def _index_chunks(
    doc_id: uuid.UUID, user_id: uuid.UUID,
    filename: str, file_type: str, chunks: list[dict[str, Any]],
) -> None:
    from backend.tools.llm import get_embedding_model
    embed_model = get_embedding_model()
    es = get_es_client()

    for chunk in chunks:
        try:
            embedding = await embed_model.aembed_query(chunk["text"])
        except Exception:
            logger.warning("chunk_embed_failed", doc_id=str(doc_id),
                           chunk_index=chunk["chunk_index"], exc_info=True)
            continue

        try:
            await es.index(index=_ES_INDEX, body={
                "document_id": str(doc_id),
                "user_id": str(user_id),
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
                "page_number": None,
                "paragraph_index": chunk["chunk_index"],
                "embedding": embedding,
                "metadata": {
                    "source_file": filename,
                    "file_type": file_type,
                    "char_count": len(chunk["text"]),
                },
                "created_at": datetime.now(UTC).isoformat(),
            }, refresh=False)
        except Exception:
            logger.warning("chunk_es_index_failed", doc_id=str(doc_id),
                           chunk_index=chunk["chunk_index"], exc_info=True)

    try:
        await es.indices.refresh(index=_ES_INDEX)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

async def _enrich_filenames(
    user_id: uuid.UUID, items: list[SearchResultItem],
) -> list[SearchResultItem]:
    if not items:
        return items
    doc_ids = {i.document_id for i in items if i.document_id}
    if not doc_ids:
        return items
    session = get_postgres_session()
    async with session:
        rows = (await session.execute(
            select(Document.id, Document.filename).where(
                Document.id.in_([uuid.UUID(d) for d in doc_ids]),
                Document.user_id == user_id,
            )
        )).all()
        name_map = {str(r[0]): r[1] for r in rows}
    for item in items:
        item.filename = name_map.get(item.document_id) or item.filename or "未知文档"
    return items
