"""Alembic-Anbindung: Schema anlegen und aktuell halten.

Warum ueberhaupt Migrationen: die Stufen 2-6 erweitern das Schema
(Anforderungen, TenderItems, PriceOffers, ...). Ohne Migrationspfad wuerde
jede Erweiterung bestehende Datenbanken unbrauchbar machen.

``ensure_current_schema`` ist der Einstiegspunkt fuer CLI und Tests:
- leere Datenbank -> Migrationen bis ``head`` ausfuehren
- bestehende Datenbank ohne ``alembic_version`` -> stempeln und weitermigrieren.
  Auf welche Revision gestempelt wird, entscheidet ein Vergleich des
  vorhandenen Schemas mit den Modellen: entspricht es ihnen bereits (etwa per
  ``create_all`` erzeugt), wird auf ``head`` gestempelt; weicht es ab (echte
  Stufe-1-Datenbank), auf die initiale Revision und dann migriert.
- aktuelle Datenbank -> nichts tun
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from ..core.logging import get_logger
from .models import Base
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


def schema_matches_models(database_url: str) -> bool:
    """Entspricht das vorhandene Schema bereits den ORM-Modellen?"""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        return not compare_metadata(context, Base.metadata)


def ensure_current_schema(database_url: str) -> str | None:
    """Schema auf den aktuellen Stand bringen; gibt die erreichte Revision zurueck."""
    engine = get_engine(database_url)
    config = alembic_config(database_url)

    revision = current_revision(database_url)
    if revision is None and inspect(engine).has_table("tenders"):
        # Bestandsdatenbank ohne Versionstabelle: stempeln statt die Tabellen
        # ein zweites Mal anzulegen. Auf welche Revision, entscheidet der
        # Vergleich mit den Modellen - sonst wuerde eine bereits aktuelle
        # Datenbank spaeter an "duplicate column" scheitern.
        target = head_revision(database_url) if schema_matches_models(database_url) else None
        target = target or INITIAL_REVISION
        log.info("alembic_stamp_existing_database", revision=target)
        command.stamp(config, target)
        revision = target

    if revision != head_revision(database_url):
        command.upgrade(config, "head")
    return current_revision(database_url)
