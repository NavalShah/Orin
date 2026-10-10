"""SQLite data model (spec 13.1). Synchronous SQLAlchemy; JSON columns are text.

Every table that holds user data has a ``user_id`` and every query filters on it
(spec 10.2). Use the ``owned`` helper so that rule is hard to forget.
"""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Type, TypeVar

from sqlalchemy import Float, Integer, String, Text, create_engine, event, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def new_id() -> str:
    return uuid.uuid4().hex


def loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    name_key: Mapped[str] = mapped_column(String, unique=True, index=True)  # casefolded, for get-or-create
    created_at: Mapped[str] = mapped_column(String)
    prefs_json: Mapped[str] = mapped_column(Text, default="{}")


class SessionRow(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)  # SHA-256; the token itself is never stored
    user_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[str] = mapped_column(String)


class ThreadRow(Base):
    __tablename__ = "threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(String)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class MessageRow(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    spec_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cards_json: Mapped[str] = mapped_column(Text, default="[]")
    track_ids: Mapped[str] = mapped_column(Text, default="[]")
    meta_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(String, index=True)


class AssetRow(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String)
    filename: Mapped[str] = mapped_column(String)  # display name only, never a path
    media_type: Mapped[str] = mapped_column(String)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String, index=True)  # also the storage key
    duration_s: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    meta_json: Mapped[str] = mapped_column(Text, default="{}")  # keys starting with "_" stay server-side
    standin: Mapped[int] = mapped_column(Integer, default=0)
    ref: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)  # lookup key, e.g. narration:<text hash>
    created_at: Mapped[str] = mapped_column(String, index=True)


class ConversionRow(Base):
    __tablename__ = "conversions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    notes_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    input_format: Mapped[str] = mapped_column(String)
    detected_format: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # the format the analysis ran with
    output_format: Mapped[str] = mapped_column(String)
    ir_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, deferred=True)  # large; loaded on demand
    loss_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outputs_json: Mapped[str] = mapped_column(Text, default="[]")
    result_json: Mapped[str] = mapped_column(Text, default="{}")  # IR summary, score, detection, missing, suggestions
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    problems_json: Mapped[str] = mapped_column(Text, default="[]")
    error_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fidelity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    note_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String, index=True)


class ProjectRow(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    project_json: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, index=True)


class JobRow(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # ErrorBody JSON
    result_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JobResult JSON
    created_at: Mapped[str] = mapped_column(String, index=True)
    updated_at: Mapped[str] = mapped_column(String)


class ExplanationRow(Base):
    __tablename__ = "explanations_cache"
    key: Mapped[str] = mapped_column(String, primary_key=True)  # server lookup key, see services/explain.py
    ai_key: Mapped[str] = mapped_column(String)  # the AI layer's own key: explain_key(s) + level + language
    level: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    try_this_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String)
    audio_asset_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)


Row = TypeVar("Row", bound=Base)


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(self.engine, "connect")
        def _pragmas(conn, _record) -> None:  # WAL: readers do not block the job workers' writes
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

        Base.metadata.create_all(self.engine)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A short unit of work: commits on success, rolls back on error. Rows stay
        readable after it closes (expire_on_commit is off)."""
        s = self._factory()
        try:
            yield s
            s.commit()
        except BaseException:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()


def owned(s: Session, model: Type[Row], user_id: str, row_id: str) -> Optional[Row]:
    """The row with this id IF it belongs to this user, else None (spec 10.2)."""
    return s.execute(
        select(model).where(model.id == row_id, model.user_id == user_id)  # type: ignore[attr-defined]
    ).scalar_one_or_none()
