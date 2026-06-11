"""
Database connection managers for PostgreSQL, MongoDB, and Elasticsearch.

Provides async connection lifecycle management (connect / disconnect / health check)
for all three storage backends used by the platform.
"""

from backend.core.config import settings
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# PostgreSQL (SQLAlchemy async)
# ═══════════════════════════════════════════════════════════════════════

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

_postgres_engine = None
_postgres_session_factory: async_sessionmaker[AsyncSession] | None = None


class Base(DeclarativeBase):
    """Base class for all PostgreSQL ORM models."""
    pass


async def postgres_connect() -> None:
    """Initialize the PostgreSQL async engine and session factory."""
    global _postgres_engine, _postgres_session_factory

    _postgres_engine = create_async_engine(
        settings.postgres_url,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_pre_ping=True,
    )
    _postgres_session_factory = async_sessionmaker(
        _postgres_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    logger.info("postgresql_connected", url=settings.postgres_url.split("@")[-1])


async def postgres_disconnect() -> None:
    """Close the PostgreSQL connection pool."""
    global _postgres_engine, _postgres_session_factory
    if _postgres_engine:
        await _postgres_engine.dispose()
        _postgres_engine = None
        _postgres_session_factory = None
        logger.info("postgresql_disconnected")


def get_postgres_session() -> AsyncSession:
    """Get a new async session from the factory. Must be used as an async context manager."""
    if _postgres_session_factory is None:
        raise RuntimeError("PostgreSQL not initialized. Call postgres_connect() first.")
    return _postgres_session_factory()


async def postgres_health() -> bool:
    """Check if PostgreSQL is reachable."""
    try:
        session = get_postgres_session()
        await session.execute(Base.metadata.tables.values().__iter__().__next__().select().limit(1))
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
# MongoDB (Motor async)
# ═══════════════════════════════════════════════════════════════════════

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_mongo_client: AsyncIOMotorClient | None = None
_mongo_db: AsyncIOMotorDatabase | None = None


async def mongo_connect() -> None:
    """Initialize the MongoDB async client and select the application database."""
    global _mongo_client, _mongo_db

    _mongo_client = AsyncIOMotorClient(settings.mongo_url)
    _mongo_db = _mongo_client[settings.mongo_db]
    # Verify connectivity
    await _mongo_client.admin.command("ping")
    logger.info("mongodb_connected", db=settings.mongo_db)


async def mongo_disconnect() -> None:
    """Close the MongoDB connection."""
    global _mongo_client, _mongo_db
    if _mongo_client:
        _mongo_client.close()
        _mongo_client = None
        _mongo_db = None
        logger.info("mongodb_disconnected")


def get_mongo_db() -> AsyncIOMotorDatabase:
    """Get the MongoDB database instance."""
    if _mongo_db is None:
        raise RuntimeError("MongoDB not initialized. Call mongo_connect() first.")
    return _mongo_db


async def mongo_health() -> bool:
    """Check if MongoDB is reachable."""
    try:
        if _mongo_client:
            await _mongo_client.admin.command("ping")
            return True
        return False
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
# Elasticsearch (elasticsearch-py async)
# ═══════════════════════════════════════════════════════════════════════

from elasticsearch import AsyncElasticsearch

_es_client: AsyncElasticsearch | None = None


async def es_connect() -> None:
    """Initialize the Elasticsearch async client."""
    global _es_client

    _es_client = AsyncElasticsearch(
        hosts=[settings.es_url],
        # Disable sniffing for single-node deployments
        sniff_on_start=False,
        sniff_on_connection_fail=False,
    )
    # Verify connectivity
    await _es_client.info()
    logger.info("elasticsearch_connected", url=settings.es_url)


async def es_disconnect() -> None:
    """Close the Elasticsearch connection."""
    global _es_client
    if _es_client:
        await _es_client.close()
        _es_client = None
        logger.info("elasticsearch_disconnected")


def get_es_client() -> AsyncElasticsearch:
    """Get the Elasticsearch async client."""
    if _es_client is None:
        raise RuntimeError("Elasticsearch not initialized. Call es_connect() first.")
    return _es_client


async def es_health() -> bool:
    """Check if Elasticsearch is reachable."""
    try:
        if _es_client:
            info = await _es_client.info()
            return info is not None
        return False
    except Exception:
        return False
