"""
Elasticsearch index mappings for the Deep Research Platform.

Defines the index schema for DocumentChunk (knowledge base text indexing)
and RetrievalResult (full-text search over research sources).
"""

from backend.core.database import get_es_client
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ── Index Names ──────────────────────────────────────────────────────

DOCUMENT_CHUNK_INDEX = "document_chunks"
RETRIEVAL_RESULT_INDEX = "retrieval_results"

# ── DocumentChunk Mapping ────────────────────────────────────────────

DOCUMENT_CHUNK_MAPPING = {
    "settings": {
        "analysis": {
            "analyzer": {
                "chinese_analyzer": {
                    "type": "custom",
                    "tokenizer": "ik_max_word",
                    "filter": ["lowercase"],
                }
            }
        }
    },
    "mappings": {
        "properties": {
            "chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "chunk_index": {"type": "integer"},
            "text": {
                "type": "text",
                "analyzer": "chinese_analyzer",
                "search_analyzer": "ik_smart",
            },
            "embedding": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
            },
            "page_number": {"type": "integer"},
            "paragraph_index": {"type": "integer"},
            "metadata": {
                "properties": {
                    "source_file": {"type": "keyword"},
                    "file_type": {"type": "keyword"},
                    "char_count": {"type": "integer"},
                }
            },
            "created_at": {"type": "date"},
        }
    },
}

# ── RetrievalResult Mapping ──────────────────────────────────────────

RETRIEVAL_RESULT_MAPPING = {
    "settings": {
        "analysis": {
            "analyzer": {
                "chinese_analyzer": {
                    "type": "custom",
                    "tokenizer": "ik_max_word",
                    "filter": ["lowercase"],
                }
            }
        }
    },
    "mappings": {
        "properties": {
            "result_id": {"type": "keyword"},
            "task_id": {"type": "keyword"},
            "user_id": {"type": "keyword"},
            "retrieval_round": {"type": "integer"},
            "title": {
                "type": "text",
                "analyzer": "chinese_analyzer",
                "search_analyzer": "ik_smart",
                "fields": {"raw": {"type": "keyword", "ignore_above": 512}},
            },
            "abstract": {
                "type": "text",
                "analyzer": "chinese_analyzer",
                "search_analyzer": "ik_smart",
            },
            "excerpt_text": {
                "type": "text",
                "analyzer": "chinese_analyzer",
                "search_analyzer": "ik_smart",
            },
            "source_url": {"type": "keyword"},
            "doi": {"type": "keyword"},
            "source_type": {"type": "keyword"},
            "authors": {"type": "keyword"},
            "publication_date": {"type": "date"},
            "credibility": {"type": "keyword"},  # high | medium | low | unknown
            "retrieved_at": {"type": "date"},
        }
    },
}


async def create_indices() -> None:
    """
    Create Elasticsearch indices if they do not already exist.

    Safe to call on every startup — checks existence before creation.
    """
    es = get_es_client()

    for index_name, mapping in [
        (DOCUMENT_CHUNK_INDEX, DOCUMENT_CHUNK_MAPPING),
        (RETRIEVAL_RESULT_INDEX, RETRIEVAL_RESULT_MAPPING),
    ]:
        if await es.indices.exists(index=index_name):
            logger.info("es_index_exists", index=index_name)
            continue

        await es.indices.create(index=index_name, body=mapping)
        logger.info("es_index_created", index=index_name)


async def delete_indices() -> None:
    """Drop Elasticsearch indices. Use with caution — mainly for testing."""
    es = get_es_client()
    for index_name in [DOCUMENT_CHUNK_INDEX, RETRIEVAL_RESULT_INDEX]:
        if await es.indices.exists(index=index_name):
            await es.indices.delete(index=index_name)
            logger.info("es_index_deleted", index=index_name)
