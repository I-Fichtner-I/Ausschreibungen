"""Konfiguration: config.yaml + .env + Umgebungsvariablen.

Prioritaet (hoechste zuerst):
1. explizite Argumente im Code
2. Umgebungsvariablen (Praefix ``TENDER_AI_``, verschachtelt mit ``__``)
3. .env-Datei
4. config.yaml
5. Defaults im Code

Secrets stehen ausschliesslich in .env bzw. in der Umgebung, niemals in
config.yaml oder im Quellcode. Sie werden als ``SecretStr`` gehalten, damit
sie in ``repr``, Logs und Tracebacks maskiert bleiben.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_CONFIG_FILE = Path("config.yaml")


class HttpConfig(BaseModel):
    timeout: float = 30.0
    connect_timeout: float = 10.0
    max_retries: int = 4
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    user_agent: str = "tender-ai/0.1 (+mailto:kontakt@example.org)"
    respect_robots: bool = True
    requests_per_second: float = 1.0
    cache_enabled: bool = True
    cache_ttl_seconds: int = 900


class SearchConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    cpv_codes: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=lambda: ["DEU"])
    published_within_days: int = 14
    min_days_until_deadline: int = 0
    max_results_per_source: int = 100


class SourceConfig(BaseModel):
    """Konfiguration einer einzelnen Quelle.

    ``extra="allow"``: adapterspezifische Schluessel (z. B. ``feeds`` beim
    RSS-Adapter) landen direkt im Objekt, ohne dass die Basisklasse jedes
    Detail kennen muss.
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    type: str
    priority: int = 50
    requests_per_second: float | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default) if hasattr(self, key) else default


class DedupConfig(BaseModel):
    enabled: bool = True
    title_similarity_threshold: float = 0.90
    match_window_days: int = 21
    #: Obergrenze fuer Kandidaten derselben Vergabestelle mit abweichendem
    #: Titelanfang. Schuetzt Laeufe vor Behoerden mit sehr vielen Ausschreibungen.
    max_authority_candidates: int = 200


class CriteriaConfig(BaseModel):
    minimum_margin_percent: float = 15.0
    minimum_profit_eur: float = 500.0
    minimum_roi_percent: float = 20.0
    maximum_risk_score: int = 40
    minimum_match_confidence: int = 85
    minimum_days_until_deadline: int = 3
    preferred_currencies: list[str] = Field(default_factory=lambda: ["EUR"])
    excluded_categories: list[str] = Field(default_factory=list)


class ScoringThresholds(BaseModel):
    very_interesting: int = 90
    interesting: int = 75
    review: int = 60
    rather_uninteresting: int = 40


class ScoringConfig(BaseModel):
    thresholds: ScoringThresholds = Field(default_factory=ScoringThresholds)


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "console"


class _YamlSettingsSource(PydanticBaseSettingsSource):
    """Laedt config.yaml als niedrigst priorisierte Settings-Quelle."""

    def __init__(self, settings_cls: type[BaseSettings], path: Path) -> None:
        super().__init__(settings_cls)
        self._data: dict[str, Any] = {}
        if path.is_file():
            loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} enthaelt kein YAML-Mapping")
            self._data = loaded

    def get_field_value(
        self, field: Any, field_name: str
    ) -> tuple[Any, str, bool]:  # pragma: no cover
        value = self._data.get(field_name)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TENDER_AI_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    config_file: Path = DEFAULT_CONFIG_FILE
    data_dir: Path = Path("data")
    database_url: str = "sqlite:///./data/tender_ai.db"

    http: HttpConfig = Field(default_factory=HttpConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    criteria: CriteriaConfig = Field(default_factory=CriteriaConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    # --- Secrets (nur aus Umgebung/.env, nie aus config.yaml) ---
    #: API-Schluessel je Quelle, Schluessel = Quellname aus config.yaml.
    #: Umgebung: TENDER_AI_SOURCE_API_KEYS__<name>=... (oder als JSON-Objekt in
    #: TENDER_AI_SOURCE_API_KEYS).
    source_api_keys: dict[str, SecretStr] = Field(default_factory=dict)
    #: Abwaertskompatibel: TENDER_AI_TED_API_KEY.
    ted_api_key: SecretStr | None = None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        config_path = Path(os.environ.get("TENDER_AI_CONFIG_FILE", str(DEFAULT_CONFIG_FILE)))
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSettingsSource(settings_cls, config_path),
            file_secret_settings,
        )

    # --- abgeleitete Pfade ---
    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    def ensure_directories(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.documents_dir, self.exports_dir):
            path.mkdir(parents=True, exist_ok=True)

    def enabled_sources(self) -> dict[str, SourceConfig]:
        return {name: cfg for name, cfg in self.sources.items() if cfg.enabled}

    def secret_for_source(self, source_name: str) -> SecretStr | None:
        """API-Key einer Quelle - ausschliesslich aus Umgebung/.env.

        Reihenfolge: ``source_api_keys[<name>]`` (Gross-/Kleinschreibung egal),
        danach fuer ``ted`` das Legacy-Feld ``ted_api_key``.
        """
        wanted = source_name.lower()
        for name, key in self.source_api_keys.items():
            if name.lower() == wanted and key.get_secret_value():
                return key
        if wanted == "ted" and self.ted_api_key and self.ted_api_key.get_secret_value():
            return self.ted_api_key
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_settings(config_file: Path | str | None = None) -> Settings:
    """Settings neu laden - nuetzlich in Tests und beim CLI-Start."""
    if config_file is not None:
        os.environ["TENDER_AI_CONFIG_FILE"] = str(config_file)
    get_settings.cache_clear()
    return get_settings()
