"""Registry der Preisquellen: Auswahl, Abschaltung, unbekannte Typen."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tender_ai.config import Settings, load_settings
from tender_ai.core.http import HttpClient
from tender_ai.pricing.sources import available_price_source_types, build_price_sources


@pytest.fixture
def http(settings: Settings) -> HttpClient:
    return HttpClient(settings.http)


def _configure(settings: Settings, price_sources: dict) -> Settings:
    config = yaml.safe_load(settings.config_file.read_text(encoding="utf-8"))
    config["price_sources"] = price_sources
    settings.config_file.write_text(yaml.safe_dump(config), encoding="utf-8")
    return load_settings(settings.config_file)


def test_catalog_type_is_registered():
    assert "catalog" in available_price_source_types()


def test_disabled_source_is_skipped_but_selectable(
    settings: Settings, http: HttpClient, tmp_path: Path
):
    """``--source`` aktiviert eine abgeschaltete Quelle fuer einen Lauf."""
    path = tmp_path / "p.csv"
    path.write_text("Bezeichnung;Preis\nX;1,00\n", encoding="utf-8")
    configured = _configure(
        settings, {"aus": {"type": "catalog", "path": str(path), "enabled": False}}
    )

    assert build_price_sources(configured, http) == []
    assert [s.name for s in build_price_sources(configured, http, only=["aus"])] == ["aus"]


def test_unknown_type_does_not_break_the_run(settings: Settings, http: HttpClient):
    """Ein unbekannter Typ wird protokolliert, nicht geworfen."""
    configured = _configure(settings, {"exotisch": {"type": "gibt-es-nicht"}})
    assert build_price_sources(configured, http) == []


def test_sources_are_ordered_by_priority(settings: Settings, http: HttpClient, tmp_path: Path):
    path = tmp_path / "p.csv"
    path.write_text("Bezeichnung;Preis\nX;1,00\n", encoding="utf-8")
    configured = _configure(
        settings,
        {
            "zweite": {"type": "catalog", "path": str(path), "priority": 20},
            "erste": {"type": "catalog", "path": str(path), "priority": 10},
        },
    )
    assert [s.name for s in build_price_sources(configured, http)] == ["erste", "zweite"]
