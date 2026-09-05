from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tender_ai.config import load_settings


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Leeres Projektverzeichnis mit config.yaml; .env wird je Test geschrieben."""
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"sources": {"ted": {"type": "ted"}, "foo": {"type": "rss"}}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    for name in (
        "TENDER_AI_TED_API_KEY",
        "TENDER_AI_SOURCE_API_KEYS",
        "TENDER_AI_SOURCE_API_KEYS__FOO",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_source_keys_from_dotenv_for_any_source(project_dir: Path):
    (project_dir / ".env").write_text(
        "TENDER_AI_SOURCE_API_KEYS__FOO=geheim-foo\nTENDER_AI_TED_API_KEY=geheim-ted\n",
        encoding="utf-8",
    )
    settings = load_settings(project_dir / "config.yaml")
    assert settings.secret_for_source("foo").get_secret_value() == "geheim-foo"
    assert settings.secret_for_source("FOO").get_secret_value() == "geheim-foo"
    assert settings.secret_for_source("ted").get_secret_value() == "geheim-ted"
    assert settings.secret_for_source("unbekannt") is None


def test_source_keys_as_json_env(project_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TENDER_AI_SOURCE_API_KEYS", '{"ted": "json-ted"}')
    settings = load_settings(project_dir / "config.yaml")
    assert settings.secret_for_source("ted").get_secret_value() == "json-ted"


def test_secrets_are_masked_in_repr(project_dir: Path):
    (project_dir / ".env").write_text(
        "TENDER_AI_SOURCE_API_KEYS__FOO=geheim-foo\nTENDER_AI_TED_API_KEY=geheim-ted\n",
        encoding="utf-8",
    )
    settings = load_settings(project_dir / "config.yaml")
    text = repr(settings) + str(settings) + settings.model_dump_json()
    assert "geheim-foo" not in text
    assert "geheim-ted" not in text


def test_empty_key_counts_as_missing(project_dir: Path):
    (project_dir / ".env").write_text("TENDER_AI_TED_API_KEY=\n", encoding="utf-8")
    settings = load_settings(project_dir / "config.yaml")
    assert settings.secret_for_source("ted") is None


# --- T-23: typisierte Quellkonfigurationen -------------------------------------
def test_known_source_types_get_their_config_class(project_dir: Path):
    from tender_ai.config import RssSourceConfig, TedSourceConfig

    settings = load_settings(project_dir / "config.yaml")
    assert isinstance(settings.sources["ted"], TedSourceConfig)
    assert isinstance(settings.sources["foo"], RssSourceConfig)
    assert settings.sources["ted"].page_size == 50  # Default aus der Klasse


def test_typo_in_known_source_is_rejected(project_dir: Path):
    """Ein Tippfehler faellt beim Start auf, statt still zum Default zu fuehren."""
    (project_dir / "config.yaml").write_text(
        yaml.safe_dump({"sources": {"ted": {"type": "ted", "page_sze": 10}}}),
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="page_sze"):
        load_settings(project_dir / "config.yaml")


def test_unknown_source_type_stays_tolerant(project_dir: Path):
    """Eine unbekannte Quelle darf nicht die ganze Konfiguration ungueltig machen."""
    from tender_ai.config import UnknownSourceConfig

    (project_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {"sources": {"exotisch": {"type": "gibtsnicht", "irgendwas": 1, "priority": 5}}}
        ),
        encoding="utf-8",
    )
    settings = load_settings(project_dir / "config.yaml")
    source = settings.sources["exotisch"]
    assert isinstance(source, UnknownSourceConfig)
    assert source.priority == 5


def test_feed_entries_are_validated(project_dir: Path):
    (project_dir / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": {
                    "feed": {
                        "type": "rss",
                        "feeds": [{"url": "https://x.invalid/f.xml", "country": "DEU"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(project_dir / "config.yaml")
    feed = settings.sources["feed"].feeds[0]
    assert feed.url == "https://x.invalid/f.xml"
    assert feed.name is None and feed.country == "DEU"


def test_shipped_config_yaml_is_valid():
    """Die mitgelieferte config.yaml muss selbst durch die Validierung gehen.

    ``extra="forbid"`` faengt einen Tippfehler im Quellblock sonst erst beim
    ersten Lauf ab - und dann beim Nutzer statt in der CI.
    """
    from tender_ai.config import HtmlListSourceConfig, SourceConfig

    settings = load_settings(Path(__file__).resolve().parents[1] / "config.yaml")

    assert settings.sources
    for name, source in settings.sources.items():
        assert isinstance(source, SourceConfig), name

    from tender_ai.config import CatalogPriceSourceConfig, PriceSourceConfig

    for name, price_source in settings.price_sources.items():
        assert isinstance(price_source, PriceSourceConfig), name
    price_list = settings.price_sources["beispiel_liste"]
    assert isinstance(price_list, CatalogPriceSourceConfig)
    assert price_list.enabled is False  # Demoliste, nicht die des Nutzers
    assert Path(price_list.path).exists(), "Beispielpreisliste fehlt"
    assert price_list.default_basis == "NET"

    portal = settings.sources["evergabe_nrw"]
    assert isinstance(portal, HtmlListSourceConfig)
    # Bewusst aus: die Selektoren sind erst mit "doctor" am echten Portal
    # bestaetigt - eine Dauerquelle mit ungeprueften Selektoren erzeugt nur
    # leere Laeufe und Fehlerzaehler.
    assert portal.enabled is False
    assert portal.list_url.startswith("https://www.evergabe.nrw.de/")
    assert set(portal.required_fields) <= set(portal.fields)
