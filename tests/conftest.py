from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tender_ai.config import Settings, load_settings

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_tender_file(tmp_path: Path) -> Path:
    payload = {
        "tenders": [
            {
                "source_id": "t-1",
                "national_id": "VG-2026-1",
                "title": "Lieferung von 500 Monitoren",
                "contracting_authority": "Musterstadt",
                "country": "DEU",
                "cpv_codes": ["30231300"],
                "publication_date": "2026-08-20",
                "submission_deadline": "2036-09-15T12:00:00+02:00",
                "estimated_value": 100000.0,
                "currency": "EUR",
                "status": "OPEN",
            },
            {
                "source_id": "t-2",
                "title": "Wartung von Aufzuegen",
                "contracting_authority": "Stadtwerke",
                "country": "DEU",
                "cpv_codes": ["50750000"],
                "publication_date": "2026-08-21",
                "submission_deadline": "2036-10-01T09:00:00+02:00",
                "status": "OPEN",
            },
        ]
    }
    path = tmp_path / "tenders.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_tender_file: Path) -> Settings:
    config = {
        "http": {
            "requests_per_second": 0,  # Tests sollen nicht warten
            "cache_enabled": False,
            "respect_robots": False,
            "max_retries": 2,
            "backoff_base": 0.001,
        },
        "search": {
            "countries": ["DEU"],
            "published_within_days": 4000,
            "min_days_until_deadline": 0,
            "max_results_per_source": 50,
        },
        "sources": {
            "ted": {
                "enabled": True,
                "type": "ted",
                "priority": 10,
                "base_url": "https://api.test.invalid",
                "search_path": "/v3/notices/search",
                "page_size": 10,
            },
            "feed": {
                "enabled": True,
                "type": "rss",
                "priority": 20,
                "feeds": [
                    {
                        "name": "Testfeed",
                        "url": "https://feed.test.invalid/rss.xml",
                        "country": "DEU",
                    }
                ],
            },
            "fixture": {
                "enabled": True,
                "type": "fixture",
                "priority": 90,
                "path": str(sample_tender_file),
            },
        },
        "logging": {"level": "WARNING", "format": "json"},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    monkeypatch.setenv("TENDER_AI_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("TENDER_AI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TENDER_AI_DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.delenv("TENDER_AI_TED_API_KEY", raising=False)

    settings = load_settings(config_path)
    settings.ensure_directories()
    return settings
