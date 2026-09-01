"""Engine- und Session-Verwaltung (SQLite lokal, PostgreSQL produktiv)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

_engines: dict[str, Engine] = {}


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
    else:
        engine = create_engine(database_url, echo=echo, pool_pre_ping=True)
    _engines[database_url] = engine
    return engine


def create_all(database_url: str) -> Engine:
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    engine = create_all(database_url)
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
