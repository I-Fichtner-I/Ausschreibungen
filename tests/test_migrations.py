from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from tender_ai.database.migrations import (
    INITIAL_REVISION,
    current_revision,
    ensure_current_schema,
    head_revision,
)
from tender_ai.database.models import Base
from tender_ai.database.session import create_all, get_engine, session_scope


def _url(tmp_path: Path, name: str = "m.db") -> str:
    return f"sqlite:///{tmp_path / name}"


def test_migrations_create_schema_matching_models(tmp_path: Path):
    url = _url(tmp_path)
    revision = ensure_current_schema(url)
    assert revision == head_revision(url)

    engine = get_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "tenders",
        "tender_aliases",
        "tender_documents",
        "tender_changes",
        "ingest_runs",
        "source_states",
    } <= tables

    # Migration und Modelle duerfen nicht auseinanderlaufen.
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": False})
        assert compare_metadata(context, Base.metadata) == []


def test_existing_database_is_stamped_not_recreated(tmp_path: Path):
    """Bestandsdatenbank aus der Zeit vor Alembic wird gestempelt."""
    url = _url(tmp_path, "legacy.db")
    create_all(url)  # Stufe-1-Zustand: Tabellen da, kein alembic_version
    assert current_revision(url) is None

    revision = ensure_current_schema(url)
    assert revision == head_revision(url)
    assert revision is not None
    assert INITIAL_REVISION.startswith("0001")


def test_ensure_is_idempotent(tmp_path: Path):
    url = _url(tmp_path, "idem.db")
    first = ensure_current_schema(url)
    second = ensure_current_schema(url)
    assert first == second


def test_sqlite_pragmas_are_applied(tmp_path: Path):
    url = _url(tmp_path, "pragma.db")
    with session_scope(url) as session:
        assert session.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert session.execute(text("PRAGMA busy_timeout")).scalar() == 5000


def test_foreign_keys_cascade_on_delete(tmp_path: Path):
    """Mit aktivem foreign_keys raeumt die Datenbank abhaengige Zeilen selbst auf."""
    from tender_ai.database.models import TenderAliasRecord, TenderRecord

    url = _url(tmp_path, "fk.db")
    with session_scope(url) as session:
        session.add(
            TenderRecord(
                id="a:1",
                fingerprint="f",
                source="a",
                source_id="1",
                content_hash="h",
                cpv_codes=[],
                payload={},
            )
        )
        session.flush()
        session.add(TenderAliasRecord(tender_id="a:1", source="b", source_id="2"))
        session.commit()

        session.execute(text("DELETE FROM tenders WHERE id = 'a:1'"))
        session.commit()
        assert session.query(TenderAliasRecord).count() == 0
