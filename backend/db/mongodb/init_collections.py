"""
MongoDB collection initialization.

Called on backend startup to ensure all collections exist.
Idempotent — creates collections only if they don't already exist.
Indexes from init.js are created by the Docker first-launch script;
this module only guarantees collection presence for the backend to
start writing data.
"""

from backend.core.database import get_mongo_db
from backend.utils.logging import get_logger

logger = get_logger(__name__)

COLLECTIONS = [
    "research_plans",
    "retrieval_results",
    "knowledge_summaries",
    "knowledge_gaps",
]


async def ensure_collections() -> None:
    """Create MongoDB collections if they don't exist (idempotent)."""
    db = get_mongo_db()
    existing = set(await db.list_collection_names())
    for coll_name in COLLECTIONS:
        if coll_name not in existing:
            await db.create_collection(coll_name)
            logger.info("mongo_collection_created", collection=coll_name)
        else:
            logger.info("mongo_collection_exists", collection=coll_name)
