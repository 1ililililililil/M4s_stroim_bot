from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from .models import Base

_engine = None
_session_factory = None

def _prepare_database_url(database_url: str) -> str:
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        return database_url

    query = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunsplit((
        "postgresql+asyncpg",
        parsed.netloc,
        parsed.path,
        urlencode(query),
        parsed.fragment,
    ))

def init_db(database_url: str):
    global _engine, _session_factory
    if "sqlite" in database_url:
        Path("data").mkdir(exist_ok=True)
    else:
        database_url = _prepare_database_url(database_url)
    _engine = create_async_engine(database_url, future=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)

def get_session_factory():
    if _session_factory is None:
        raise RuntimeError("База данных не инициализирована")
    return _session_factory

async def create_tables():
    if _engine is None:
        raise RuntimeError("База данных не инициализирована")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "postgresql":
            for table, column in (
                ("users", "telegram_id"),
                ("comments", "telegram_message_id"),
                ("comments", "chat_id"),
                ("comments", "post_message_id"),
                ("comments", "reply_to_message_id"),
                ("admin_actions", "admin_id"),
            ):
                await conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE BIGINT"))

        required_columns = {
            "comments": {
                "post_text": "TEXT",
                "processing": "BOOLEAN NOT NULL DEFAULT FALSE",
                "auto_reply_sent": "BOOLEAN NOT NULL DEFAULT FALSE",
                "published_response": "TEXT",
                "processed_at": "TIMESTAMP",
                "route": "VARCHAR(32)",
                "ai_requested": "BOOLEAN NOT NULL DEFAULT FALSE",
                "ai_failed": "BOOLEAN NOT NULL DEFAULT FALSE",
                "fallback_used": "BOOLEAN NOT NULL DEFAULT FALSE",
            },
            "knowledge_base": {
                "chat_id": "BIGINT",
                "source_type": "VARCHAR(32) NOT NULL DEFAULT 'manual'",
                "source_message_id": "BIGINT",
                "title": "VARCHAR(256)",
                "question": "TEXT",
                "keywords": "TEXT",
                "category": "VARCHAR(32) NOT NULL DEFAULT 'GENERAL'",
                "priority": "INTEGER NOT NULL DEFAULT 0",
                "answer_variants": "TEXT",
            }
        }
        for table, columns in required_columns.items():
            existing = await conn.run_sync(
                lambda sync_conn: {column["name"] for column in inspect(sync_conn).get_columns(table)}
            )
            for column, definition in columns.items():
                if column not in existing:
                    await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_source "
            "ON knowledge_base (chat_id, source_message_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_unanswered_comment "
            "ON unanswered_questions (comment_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_answer_candidate_comment "
            "ON answer_candidates (comment_id)"
        ))

async def dispose_db():
    if _engine is not None:
        await _engine.dispose()
