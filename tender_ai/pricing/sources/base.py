"""Basis der Preisquellen (Stufe 4).

Eine Preisquelle beantwortet eine Frage: "Was kostet so etwas, und wo steht
das?" Sie liefert deshalb nie nur Zahlen, sondern ``PriceQuote``-Objekte mit
Herkunft, Zeitpunkt und Bezugsgroesse.

Wie bei den Ausschreibungsquellen gilt: eine ausgefallene Quelle stoppt die
Recherche nicht - sie wird als gestoert vermerkt, die uebrigen laufen weiter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar

from ...config import PriceSourceConfig, Settings
from ...core.http import HttpClient
from ...core.logging import get_logger
from ...models.item import TenderItem
from ...models.price import PriceQuote


@dataclass(slots=True)
class ProductQuery:
    """Wonach gesucht wird - abgeleitet aus einer Position des LV."""

    text: str
    manufacturer: str | None = None
    model_number: str | None = None
    article_number: str | None = None
    unit: str | None = None
    quantity: float | None = None
    max_results: int = 20

    @classmethod
    def from_item(cls, item: TenderItem, *, max_results: int = 20) -> ProductQuery:
        return cls(
            text=item.title,
            manufacturer=item.manufacturer,
            model_number=item.model_number,
            article_number=item.article_number,
            unit=item.unit,
            quantity=item.quantity,
            max_results=max_results,
        )


@dataclass(slots=True)
class PriceSourceStatus:
    """Ergebnis eines Health-Checks - Basis fuer ``tender-ai doctor``."""

    name: str
    type: str
    ok: bool
    message: str
    sample_count: int = 0
    checked_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "ok": self.ok,
            "message": self.message,
            "sample_count": self.sample_count,
            "checked_at": self.checked_at.isoformat(),
        }


class PriceSource(ABC):
    """Basisklasse aller Preisquellen."""

    type_name: ClassVar[str] = "base"

    def __init__(
        self,
        name: str,
        config: PriceSourceConfig,
        http: HttpClient,
        settings: Settings,
    ) -> None:
        self.name = name
        self.config = config
        self.http = http
        self.settings = settings
        self.log = get_logger(f"price_source.{name}")

    @property
    def priority(self) -> int:
        return self.config.priority

    @abstractmethod
    async def search(self, query: ProductQuery) -> list[PriceQuote]:
        """Angebote zu einer Produktanfrage liefern."""

    @abstractmethod
    async def health_check(self) -> PriceSourceStatus:
        """Erreichbarkeit und Lesbarkeit der Quelle pruefen."""

    async def aclose(self) -> None:  # noqa: B027 - bewusst optionaler Haken
        """Ressourcen freigeben.

        Absichtlich kein Pflichtbestandteil: eine Preisliste aus einer Datei hat
        nichts freizugeben, eine spaetere API-Quelle schon.
        """
        return None
