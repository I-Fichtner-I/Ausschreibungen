"""Engine- und Session-Verwaltung (SQLite lokal, PostgreSQL produktiv).

Das Schema wird ueber Alembic-Migrationen verwaltet (``tender_ai.database.migrations``).
``create_all`` bleibt fuer Tests und den Erstaufbau erhalten, wird aber nicht
mehr bei jedem Session-Start aufgerufen.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engines: dict[str, Engine] = {}

#: PRAGMAs je SQLite-Verbindung. WAL erlaubt gleichzeitiges Lesen waehrend eines
#: Schreibvorgangs (cron-Lauf + interaktive CLI), busy_timeout wartet statt
#: sofort mit "database is locked" abzubrechen, foreign_keys aktiviert die
#: ON-DELETE-Regeln, die das ORM sonst nur anwendungsseitig durchsetzt.
SQLITE_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("busy_timeout", "5000"),
    ("foreign_keys", "ON"),
    ("synchronous", "NORMAL"),
)


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for pragma, value in SQLITE_PRAGMAS:
            cursor.execute(f"PRAGMA {pragma}={value}")
    finally:
        cursor.close()


def get_engine(database_url: str, echo: bool = False) -> Engine:
    engine = _engines.get(database_url)
    if engine is not None:
        return engine
    if database_url.startswith("sqlite"):
        # Verzeichnis fuer die SQLite-Datei anlegen
        path_part = database_url.split("///", 1)[-1]
        if path_part and path_part != ":memory:":
            Path(path_part).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(database_url, echo=echo, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _apply_sqlite_pragmas)
    else:
        engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    _engines[database_url] = engine
    return engine


def create_all(database_url: str) -> Engine:
    """Schema direkt aus den Modellen anlegen (Tests, Notfall).

    Der regulaere Weg ist ``tender_ai.database.migrations.upgrade_to_head``.
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(database_url: str, *, ensure_schema: bool = True) -> Iterator[Session]:
    engine = get_engine(database_url)
    if ensure_schema:
        from .migrations import ensure_current_schema

        ensure_current_schema(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
