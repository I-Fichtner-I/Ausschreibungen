"""Alembic-Anbindung: Schema anlegen und aktuell halten.

Warum ueberhaupt Migrationen: die Stufen 2-6 erweitern das Schema
(Anforderungen, TenderItems, PriceOffers, ...). Ohne Migrationspfad wuerde
jede Erweiterung bestehende Datenbanken unbrauchbar machen.

``ensure_current_schema`` ist der Einstiegspunkt fuer CLI und Tests:
- leere Datenbank            -> Migrationen bis ``head`` ausfuehren
- bestehende Stufe-1-Datenbank ohne ``alembic_version`` -> auf die initiale
  Revision stempeln (die Tabellen sind identisch) und dann weitermigrieren
- aktuelle Datenbank         -> nichts tun
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from ..core.logging import get_logger
from .session import get_engine

log = get_logger(__name__)

ALEMBIC_DIR = Path(__file__).parent / "alembic"
#: Revision, die dem per ``create_all`` erzeugten Stufe-1-Schema entspricht.
INITIAL_REVISION = "0001_initial"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def current_revision(database_url: str) -> str | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision(database_url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()


def ensure_current_schema(database_url: str) -> str | None:
    """Schema auf den aktuellen Stand bringen; gibt die erreichte Revision zurueck."""
    engine = get_engine(database_url)
    config = alembic_config(database_url)

    revision = current_revision(database_url)
    if revision is None and inspect(engine).has_table("tenders"):
        # Bestandsdatenbank aus der Zeit vor den Migrationen: stempeln statt
        # die Tabellen ein zweites Mal anzulegen.
        log.info("alembic_stamp_existing_database", revision=INITIAL_REVISION)
        command.stamp(config, INITIAL_REVISION)
        revision = INITIAL_REVISION

    if revision != head_revision(database_url):
        command.upgrade(config, "head")
    return current_revision(database_url)
