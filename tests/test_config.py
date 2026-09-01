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
