"""
PostgreSQL table creation migrations.

These scripts are idempotent — they use IF NOT EXISTS and are safe
to run on every application startup.

Tables: users, conversations, messages, research_tasks, research_reports, citations, documents
"""

from sqlalchemy import text

from backend.core.database import Base, _postgres_engine
from backend.utils.logging import get_logger

logger = get_logger(__name__)

# ── Raw SQL for initial schema creation ──────────────────────────────

CREATE_TABLES_SQL = """
-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100),
    institution VARCHAR(200),
    is_active BOOLEAN DEFAULT TRUE,
    locked_until TIMESTAMP WITH TIME ZONE,
    login_attempts INTEGER DEFAULT 0,
    token_version INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Research tasks table
CREATE TABLE IF NOT EXISTS research_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    topic TEXT NOT NULL CHECK (char_length(topic) >= 10),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'paused', 'completed', 'failed')),
    current_phase VARCHAR(30),
    progress_message TEXT,
    started_at TIMESTAMP WITH TIME ZONE,
    elapsed_seconds INTEGER DEFAULT 0,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    tags JSONB DEFAULT '[]'::jsonb,
    config_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    CONSTRAINT fk_task_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON research_tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON research_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON research_tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_tags ON research_tasks USING gin(tags);

-- Research reports table
CREATE TABLE IF NOT EXISTS research_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID UNIQUE NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    title VARCHAR(500) NOT NULL,
    abstract TEXT NOT NULL,
    sections_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    gap_notes TEXT,
    export_format_log JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_report_task FOREIGN KEY (task_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

-- Citations table
CREATE TABLE IF NOT EXISTS citations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id UUID NOT NULL REFERENCES research_reports(id) ON DELETE CASCADE,
    index_number INTEGER NOT NULL,
    retrieval_result_id VARCHAR(24) NOT NULL,
    context_in_report TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_citation_report FOREIGN KEY (report_id) REFERENCES research_reports(id) ON DELETE CASCADE,
    UNIQUE(report_id, index_number)
);

CREATE INDEX IF NOT EXISTS idx_citations_report_id ON citations(report_id);

-- Documents table
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    file_type VARCHAR(20) NOT NULL CHECK (file_type IN ('pdf', 'docx', 'txt', 'md')),
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0 AND file_size_bytes <= 52428800),
    storage_path VARCHAR(1000) NOT NULL,
    processing_status VARCHAR(20) DEFAULT 'pending'
        CHECK (processing_status IN ('pending', 'processing', 'completed', 'failed')),
    processing_error TEXT,
    processed_at TIMESTAMP WITH TIME ZONE,
    es_index_name VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_doc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_documents_user_id ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(processing_status);

-- Conversations table (Phase 3b — chat UX)
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL,
    title VARCHAR(200) NOT NULL DEFAULT 'New Conversation',
    model VARCHAR(100) NOT NULL DEFAULT 'gpt-4o',
    context_window_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_conv_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_updated
    ON conversations(user_id, updated_at DESC);

-- Messages table (Phase 3b — chat UX)
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL,
    role VARCHAR(20) NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    message_type VARCHAR(30) NOT NULL DEFAULT 'text',
    parent_message_id UUID,
    metadata JSONB DEFAULT '{}'::jsonb,
    token_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id)
        REFERENCES conversations(id) ON DELETE CASCADE,
    CONSTRAINT fk_msg_parent FOREIGN KEY (parent_message_id)
        REFERENCES messages(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created
    ON messages(conversation_id, created_at);

-- Link ResearchTask to Message (Phase 3b)
ALTER TABLE research_tasks
    ADD COLUMN IF NOT EXISTS message_id UUID,
    ADD CONSTRAINT fk_task_message FOREIGN KEY (message_id)
        REFERENCES messages(id) ON DELETE SET NULL;

-- Message model tracking (per-message model selection)
ALTER TABLE messages
    ADD COLUMN IF NOT EXISTS model VARCHAR(100);

-- Token version for refresh token rotation (Phase 5 security hardening)
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS token_version INTEGER DEFAULT 0;
"""

DROP_TABLES_SQL = """
DROP TABLE IF EXISTS citations CASCADE;
DROP TABLE IF EXISTS research_reports CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS research_tasks CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS users CASCADE;
"""


async def _execute_sql(conn, sql: str) -> None:
    """
    Execute multi-statement SQL by splitting on semicolons.

    asyncpg does not support multiple commands in a single prepared statement,
    so we split and execute each non-empty statement individually.
    """
    from sqlalchemy import text

    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        await conn.execute(text(stmt + ";"))


async def run_migrations() -> None:
    """
    Execute table creation scripts.

    Each statement is executed individually so that a failure in one
    (e.g. duplicate constraint) does not skip later statements
    (e.g. new column additions). All statements are idempotent.
    """
    from sqlalchemy.exc import ProgrammingError
    from backend.core.database import _postgres_engine

    if _postgres_engine is None:
        raise RuntimeError("PostgreSQL not initialized. Call postgres_connect() first.")

    statements = [s.strip() for s in CREATE_TABLES_SQL.split(";") if s.strip()]

    async with _postgres_engine.begin() as conn:
        for stmt in statements:
            # Use a savepoint so one failure doesn't abort the whole transaction
            try:
                async with conn.begin_nested():
                    await conn.execute(text(stmt + ";"))
            except ProgrammingError:
                logger.info(
                    "postgresql_migration_idempotent_skip",
                    stmt=stmt[:80],
                )

    logger.info("postgresql_migrations_complete")


async def drop_all_tables() -> None:
    """Drop all tables. Use with caution — for testing only."""
    from backend.core.database import _postgres_engine

    if _postgres_engine is None:
        raise RuntimeError("PostgreSQL not initialized.")

    async with _postgres_engine.begin() as conn:
        await _execute_sql(conn, DROP_TABLES_SQL)

    logger.warning("postgresql_all_tables_dropped")
