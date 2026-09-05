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
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
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
    #: Obergrenze fuer einen einzelnen Dateidownload (Vergabeunterlagen).
    max_download_bytes: int = 50_000_000
    #: Obergrenze fuer eine Feed-Antwort - schuetzt den XML-Parser vor
    #: uebergrossen oder aufgeblaehten Feeds.
    max_feed_bytes: int = 5_000_000


class SearchConfig(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    cpv_codes: list[str] = Field(default_factory=list)
    countries: list[str] = Field(default_factory=lambda: ["DEU"])
    published_within_days: int = 14
    min_days_until_deadline: int = 0
    max_results_per_source: int = 100


class SourceConfig(BaseModel):
    """Basis jeder Quellkonfiguration.

    Konkrete Adapter bringen eigene Unterklassen mit ihren Feldern mit und
    registrieren sie ueber ``register_source_config``. Dadurch faellt ein
    Tippfehler in config.yaml (``page_sze`` statt ``page_size``) beim Start auf,
    statt still zum Default zu fuehren.
    """

    # validate_assignment: auch spaeter gesetzte Werte werden geprueft und in
    # die typisierten Modelle konvertiert (z. B. ein Feed-Dict zu FeedConfig).
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = True
    type: str
    priority: int = 50
    requests_per_second: float | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default) if hasattr(self, key) else default


class UnknownSourceConfig(SourceConfig):
    """Fallback fuer Quelltypen, die dieses Programm nicht kennt.

    Bewusst tolerant (``extra="allow"``): eine unbekannte Quelle in config.yaml
    soll beim Start gemeldet und uebersprungen werden - sie darf nicht die
    gesamte Konfiguration ungueltig machen.
    """

    model_config = ConfigDict(extra="allow")


class TedSourceConfig(SourceConfig):
    """Konfiguration des TED-Adapters (siehe ``tender_ai.sources.ted``)."""

    base_url: str = "https://api.ted.europa.eu"
    search_path: str = "/v3/notices/search"
    page_size: int = 50
    scope: str = "ALL"
    auth_header: str = "Authorization"
    auth_scheme: str = ""
    fields: list[str] | None = None
    query_fields: dict[str, str] | None = None
    #: Praefix des eForms-``notice-type`` -> Status; leer = eingebaute Zuordnung.
    status_map: dict[str, str] | None = None
    #: Komplette Expert-Query selbst vorgeben (ueberschreibt den Query-Builder).
    raw_query: str | None = None


class FeedConfig(BaseModel):
    """Ein einzelner RSS-/Atom-Feed."""

    model_config = ConfigDict(extra="forbid")

    url: str
    name: str | None = None
    country: str | None = None
    #: Falls der Feed genau eine Vergabestelle abbildet.
    authority: str | None = None


class RssSourceConfig(SourceConfig):
    feeds: list[FeedConfig] = Field(default_factory=list)


class FixtureSourceConfig(SourceConfig):
    path: str = "data/fixtures/sample_tenders.json"


#: type -> Konfigurationsklasse. Adapter registrieren hier ihre Klasse.
class FieldSelector(BaseModel):
    """Wo in einer Trefferzeile ein Feld steht.

    Absichtlich datengetrieben: Portale aendern ihr Markup, ohne Bescheid zu
    sagen. Ein geaenderter Selektor ist damit eine Zeile in config.yaml und
    kein Codeaenderung-plus-Release.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    #: CSS-Selektor relativ zur Zeile; leer = die Zeile selbst.
    selector: str | None = None
    #: "text" oder ein Attributname ("href", "title", "datetime", ...).
    attribute: str = "text"
    #: Optionaler Ausdruck; die erste Gruppe (sonst der Treffer) gewinnt.
    regex: str | None = None
    #: Welcher Treffer, falls der Selektor mehrere Elemente findet.
    index: int = 0


class HtmlListSourceConfig(SourceConfig):
    """Vergabeportal ohne API: Trefferliste als HTML auslesen.

    Der Adapter ist bewusst generisch - die meisten deutschen Landesportale
    laufen auf derselben Handvoll Produkte (AI Vergabemanager/VMP, Deutsche
    eVergabe, cosinex). Ein neues Portal ist damit ein Konfigurationsblock.

    Der Abruf laeuft ueber den normalen HTTP-Client und damit ueber robots.txt,
    Rate-Limit und Cache. Es werden ausschliesslich oeffentlich erreichbare
    Uebersichtsseiten gelesen - keine Anmeldung, kein Captcha, keine
    Zugriffsbeschraenkung wird umgangen.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    type: str = "html_list"
    #: Anzeigename des Portals (erscheint in Herkunft und Protokoll).
    label: str | None = None
    #: Basis fuer relative Links.
    base_url: str
    #: Einstiegsseite mit der Trefferliste.
    list_url: str
    country: str | None = None
    region: str | None = None
    #: Vergabestelle, falls die Liste sie nicht ausweist.
    authority: str | None = None
    #: Zeitzone der Fristangaben; Portale schreiben Ortszeit ohne Offset.
    timezone: str = "Europe/Berlin"

    #: CSS-Selektor der Trefferzeilen (z. B. "table.results tbody tr").
    row_selector: str
    #: Feldname -> Fundstelle. Bekannte Feldnamen siehe ``html_list.FIELDS``.
    fields: dict[str, FieldSelector] = Field(default_factory=dict)
    #: Zeilen ohne diese Felder gelten als Kopf- oder Layoutzeile.
    required_fields: list[str] = Field(default_factory=lambda: ["title"])

    #: Blaetterung ueber einen Query-Parameter (z. B. "page").
    page_param: str | None = None
    first_page: int = 1
    max_pages: int = 1

    #: Detailseite je Treffer nachladen - kostet einen Abruf pro Treffer.
    follow_detail: bool = False
    #: Obergrenze, damit ein Lauf das Portal nicht ueberrennt.
    max_detail_requests: int = 25
    detail_fields: dict[str, FieldSelector] = Field(default_factory=dict)
    #: Query-Parameter der Detail-URL, der die Bekanntmachung eindeutig macht.
    id_param: str | None = None

    @field_validator("base_url", "list_url")
    @classmethod
    def _require_http_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("muss eine vollstaendige http(s)-URL sein")
        return value.rstrip("/") if value.endswith("/") else value


SOURCE_CONFIG_TYPES: dict[str, type[SourceConfig]] = {
    "ted": TedSourceConfig,
    "rss": RssSourceConfig,
    "fixture": FixtureSourceConfig,
    "html_list": HtmlListSourceConfig,
}


def register_source_config(type_name: str, config_cls: type[SourceConfig]) -> None:
    """Konfigurationsklasse eines Adapters bekannt machen."""
    SOURCE_CONFIG_TYPES[type_name] = config_cls


def parse_source_config(name: str, value: Any) -> SourceConfig:
    """Einen ``sources``-Eintrag mit der zu seinem ``type`` passenden Klasse lesen."""
    if isinstance(value, SourceConfig):
        return value
    if not isinstance(value, dict):
        raise ValueError(f"sources.{name} muss ein Mapping sein, nicht {type(value).__name__}")
    config_cls = SOURCE_CONFIG_TYPES.get(str(value.get("type", "")), UnknownSourceConfig)
    return config_cls.model_validate(value)


class PriceSourceConfig(BaseModel):
    """Basis jeder Preisquellenkonfiguration."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    enabled: bool = True
    type: str
    priority: int = 50
    requests_per_second: float | None = None

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default) if hasattr(self, key) else default


class UnknownPriceSourceConfig(PriceSourceConfig):
    """Unbekannter Typ macht die Konfiguration nicht ungueltig."""

    model_config = ConfigDict(extra="allow")


#: Logische Spalten einer Preisliste -> uebliche Ueberschriften. Dient nur als
#: Vorbelegung; ``columns`` in config.yaml ueberschreibt sie feldweise.
DEFAULT_CATALOG_COLUMNS: dict[str, str] = {
    "product_name": "Bezeichnung",
    "manufacturer": "Hersteller",
    "model_number": "Typ",
    "article_number": "Artikelnummer",
    "amount": "Preis",
    "currency": "Waehrung",
    "basis": "Preisbasis",
    "vat_rate": "MwSt",
    "unit": "Einheit",
    "tiers": "Staffelpreise",
    "shipping_cost": "Versandkosten",
    "min_order_quantity": "Mindestmenge",
    "packaging_unit": "Verpackungseinheit",
    "availability": "Verfuegbarkeit",
    "lead_time_days": "Lieferzeit",
    "supplier": "Lieferant",
    "url": "Link",
    "price_date": "Preisstand",
}


class CatalogPriceSourceConfig(PriceSourceConfig):
    """Lieferantenpreisliste als CSV, XLSX oder JSON."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    type: str = "catalog"
    path: str
    #: Lieferantenname, falls die Liste ihn nicht je Zeile ausweist.
    supplier: str | None = None
    encoding: str = "utf-8"
    #: Arbeitsblatt bei XLSX; leer = erstes Blatt.
    sheet: str | None = None
    columns: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_CATALOG_COLUMNS))
    default_currency: str = "EUR"
    #: Ausdrueckliche Ansage, ob die Liste netto oder brutto ist. Ohne sie
    #: bleibt die Bezugsgroesse UNKNOWN - das Tool raet sie nicht.
    default_basis: str | None = None
    default_vat_rate: float | None = None
    shipping_included: bool | None = None
    #: Ab welcher Namensaehnlichkeit eine Zeile als Kandidat gilt. Die
    #: eigentliche Bewertung macht danach das begruendete Matching.
    minimum_similarity: float = 0.34

    @field_validator("default_basis")
    @classmethod
    def _check_basis(cls, value: str | None) -> str | None:
        """NET/GROSS, auch in deutscher Schreibweise angegeben."""
        if value is None:
            return None
        known = {"NET": "NET", "NETTO": "NET", "GROSS": "GROSS", "BRUTTO": "GROSS"}
        resolved = known.get(value.strip().upper())
        if resolved is None:
            raise ValueError(
                f"default_basis muss NET/netto oder GROSS/brutto sein, nicht {value!r}"
            )
        return resolved


PRICE_SOURCE_CONFIG_TYPES: dict[str, type[PriceSourceConfig]] = {
    "catalog": CatalogPriceSourceConfig,
}


def register_price_source_config(type_name: str, config_cls: type[PriceSourceConfig]) -> None:
    PRICE_SOURCE_CONFIG_TYPES[type_name] = config_cls


def parse_price_source_config(name: str, value: Any) -> PriceSourceConfig:
    """Einen ``price_sources``-Eintrag mit der zu seinem ``type`` passenden Klasse lesen."""
    if isinstance(value, PriceSourceConfig):
        return value
    if not isinstance(value, dict):
        raise ValueError(
            f"price_sources.{name} muss ein Mapping sein, nicht {type(value).__name__}"
        )
    config_cls = PRICE_SOURCE_CONFIG_TYPES.get(str(value.get("type", "")), UnknownPriceSourceConfig)
    return config_cls.model_validate(value)


class PricingConfig(BaseModel):
    """Verhalten der Preisrecherche (Stufe 4)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    #: Angebote je Position, die behalten werden.
    max_offers_per_item: int = 10
    #: Positionen je Ausschreibung, fuer die recherchiert wird.
    max_items: int = 200
    #: Ab welcher Preisstreuung gewarnt wird (Anteil des Medians).
    spread_warning_ratio: float = 0.5
    #: Nur Angebote in diesen Waehrungen verwenden; leer = alle. Ein Umrechnen
    #: unterbleibt bewusst - ein erfundener Kurs waere ein erfundener Preis.
    currencies: list[str] = Field(default_factory=lambda: ["EUR"])


class CalculationConfig(BaseModel):
    """Wie aus Einkaufspreisen ein Angebotspreis wird (Stufe 5)."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    #: Aufschlag auf die Selbstkosten, in Prozent. Bestimmt den Angebotspreis.
    markup_percent: float = 25.0
    #: Gemeinkostenzuschlag auf den Einkaufswert, in Prozent.
    overhead_percent: float = 8.0
    #: Pauschale je Position (Bestellung, Wareneingang, Handling).
    handling_cost_per_position: float = 0.0
    #: Pauschale je Auftrag (Angebotserstellung, Projektaufwand).
    fixed_cost_per_tender: float = 0.0
    #: Versandkosten aus den Angeboten mitrechnen.
    include_shipping: bool = True
    #: Ohne diese Abdeckung wird nicht bewertet: die Marge eines Bruchteils
    #: der Positionen ist nicht die Marge des Auftrags.
    minimum_coverage_percent: int = 80

    @field_validator("markup_percent", "overhead_percent")
    @classmethod
    def _not_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("darf nicht negativ sein")
        return value

    @field_validator("minimum_coverage_percent")
    @classmethod
    def _percent_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("muss zwischen 0 und 100 liegen")
        return value


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
    #: Preisquellen der Stufe 4 (Lieferantenlisten, spaeter APIs).
    price_sources: dict[str, PriceSourceConfig] = Field(default_factory=dict)
    pricing: PricingConfig = Field(default_factory=PricingConfig)
    calculation: CalculationConfig = Field(default_factory=CalculationConfig)
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

    @field_validator("sources", mode="before")
    @classmethod
    def _parse_sources(cls, value: Any) -> Any:
        """Jede Quelle mit der Klasse ihres ``type`` validieren."""
        if not isinstance(value, dict):
            return value
        return {name: parse_source_config(name, entry) for name, entry in value.items()}

    @field_validator("price_sources", mode="before")
    @classmethod
    def _parse_price_sources(cls, value: Any) -> Any:
        """Jede Preisquelle mit der Klasse ihres ``type`` validieren."""
        if not isinstance(value, dict):
            return value
        return {name: parse_price_source_config(name, entry) for name, entry in value.items()}

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
