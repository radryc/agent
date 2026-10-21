from __future__ import annotations

import uuid
import logging
from datetime import datetime

from sqlalchemy import Column, String, Text, DateTime, Integer, select, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    title = Column(String, default="New Chat")
    active_persona = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MessageRow(Base):
    __tablename__ = "messages"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    persona = Column(String, nullable=True)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class TerminalStateRow(Base):
    __tablename__ = "terminal_states"

    session_id = Column(String, primary_key=True)
    shell = Column(String, nullable=False, default="/bin/bash")
    cwd = Column(String, nullable=False, default="")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TerminalHistoryRow(Base):
    __tablename__ = "terminal_history"

    id = Column(String, primary_key=True)
    session_id = Column(String, index=True, nullable=False)
    command = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


_engine = None
_session_factory = None


def _migrate_schema_sync(conn) -> None:
    """Add persona columns to pre-existing SQLite databases (create_all won't)."""
    migrations = (
        ("sessions", "active_persona", "ALTER TABLE sessions ADD COLUMN active_persona VARCHAR"),
        ("messages", "persona", "ALTER TABLE messages ADD COLUMN persona VARCHAR"),
    )
    for table, column, ddl in migrations:
        try:
            rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
            existing = {row[1] for row in rows}
            if column not in existing:
                conn.exec_driver_sql(ddl)
                logger.info("Migrated database: added %s.%s", table, column)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Schema migration for %s.%s failed: %s", table, column, exc)


async def init_db() -> None:
    global _engine, _session_factory
    _engine = create_async_engine(settings.database_url, echo=False)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_schema_sync)
    logger.info("Database initialized at %s", settings.database_url)


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()


def _get_session() -> AsyncSession:
    return _session_factory()


async def create_session(title: str = "New Chat", persona: str | None = None) -> dict:
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    async with _get_session() as db:
        db.add(
            SessionRow(
                id=session_id,
                title=title,
                active_persona=persona,
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    return {
        "id": session_id,
        "title": title,
        "active_persona": persona,
        "created_at": now,
        "updated_at": now,
    }


async def list_sessions() -> list[dict]:
    async with _get_session() as db:
        result = await db.execute(
            select(SessionRow).order_by(SessionRow.updated_at.desc())
        )
        rows = result.scalars().all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "active_persona": r.active_persona,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            }
            for r in rows
        ]


async def get_session(session_id: str) -> dict | None:
    async with _get_session() as db:
        row = await db.get(SessionRow, session_id)
        if not row:
            return None
        msg_result = await db.execute(
            select(MessageRow)
            .where(MessageRow.session_id == session_id)
            .order_by(MessageRow.position)
        )
        messages = [
            {
                "id": m.id,
                "session_id": m.session_id,
                "role": m.role,
                "content": m.content,
                "persona": m.persona,
                "created_at": m.created_at,
            }
            for m in msg_result.scalars().all()
        ]
        return {
            "id": row.id,
            "title": row.title,
            "active_persona": row.active_persona,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "messages": messages,
        }


async def delete_session(session_id: str) -> bool:
    async with _get_session() as db:
        row = await db.get(SessionRow, session_id)
        if not row:
            return False
        await db.execute(
            delete(MessageRow).where(MessageRow.session_id == session_id)
        )
        await db.execute(
            delete(TerminalHistoryRow).where(TerminalHistoryRow.session_id == session_id)
        )
        await db.execute(
            delete(TerminalStateRow).where(TerminalStateRow.session_id == session_id)
        )
        await db.delete(row)
        await db.commit()
        return True


async def update_session_title(session_id: str, title: str) -> dict | None:
    async with _get_session() as db:
        row = await db.get(SessionRow, session_id)
        if not row:
            return None
        row.title = title
        row.updated_at = datetime.utcnow()
        await db.commit()
        return {
            "id": row.id,
            "title": row.title,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


async def set_active_persona(session_id: str, persona: str | None) -> None:
    async with _get_session() as db:
        row = await db.get(SessionRow, session_id)
        if not row:
            return
        row.active_persona = persona
        await db.commit()


async def add_message(
    session_id: str,
    role: str,
    content: str,
    persona: str | None = None,
) -> dict:
    msg_id = str(uuid.uuid4())
    now = datetime.utcnow()
    async with _get_session() as db:
        count_result = await db.execute(
            select(MessageRow).where(MessageRow.session_id == session_id)
        )
        position = len(count_result.scalars().all())
        db.add(
            MessageRow(
                id=msg_id,
                session_id=session_id,
                role=role,
                content=content,
                persona=persona,
                position=position,
                created_at=now,
            )
        )
        session_row = await db.get(SessionRow, session_id)
        if session_row:
            session_row.updated_at = now
        await db.commit()
    return {
        "id": msg_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "persona": persona,
        "created_at": now,
    }


async def get_messages(session_id: str) -> list[dict]:
    async with _get_session() as db:
        result = await db.execute(
            select(MessageRow)
            .where(MessageRow.session_id == session_id)
            .order_by(MessageRow.position)
        )
        return [
            {"role": m.role, "content": m.content}
            for m in result.scalars().all()
        ]


async def get_terminal_state(session_id: str) -> dict | None:
    async with _get_session() as db:
        row = await db.get(TerminalStateRow, session_id)
        if not row:
            return None
        return {
            "session_id": row.session_id,
            "shell": row.shell,
            "cwd": row.cwd,
            "updated_at": row.updated_at,
        }


async def upsert_terminal_state(session_id: str, cwd: str, shell: str = "/bin/bash") -> dict:
    now = datetime.utcnow()
    async with _get_session() as db:
        row = await db.get(TerminalStateRow, session_id)
        if not row:
            row = TerminalStateRow(
                session_id=session_id,
                shell=shell,
                cwd=cwd,
                updated_at=now,
            )
            db.add(row)
        else:
            row.shell = shell
            row.cwd = cwd
            row.updated_at = now
        await db.commit()
    return {
        "session_id": session_id,
        "shell": shell,
        "cwd": cwd,
        "updated_at": now,
    }


async def add_terminal_history(session_id: str, command: str) -> dict | None:
    cleaned = command.strip()
    if not cleaned:
        return None

    history_id = str(uuid.uuid4())
    now = datetime.utcnow()
    async with _get_session() as db:
        count_result = await db.execute(
            select(TerminalHistoryRow).where(TerminalHistoryRow.session_id == session_id)
        )
        position = len(count_result.scalars().all())
        row = TerminalHistoryRow(
            id=history_id,
            session_id=session_id,
            command=cleaned,
            position=position,
            created_at=now,
        )
        db.add(row)
        await db.commit()

    return {
        "id": history_id,
        "session_id": session_id,
        "command": cleaned,
        "position": position,
        "created_at": now,
    }


async def list_terminal_history(session_id: str, limit: int = 200) -> list[dict]:
    safe_limit = max(1, min(limit, 1000))
    async with _get_session() as db:
        result = await db.execute(
            select(TerminalHistoryRow)
            .where(TerminalHistoryRow.session_id == session_id)
            .order_by(TerminalHistoryRow.position.desc())
        )
        rows = result.scalars().all()[:safe_limit]

    ordered = list(reversed(rows))
    return [
        {
            "id": r.id,
            "session_id": r.session_id,
            "command": r.command,
            "position": r.position,
            "created_at": r.created_at,
        }
        for r in ordered
    ]
