"""Gemeinsame Bausteine der Datenmodelle.

Datenqualitaets-Regel des Projekts: fehlende Werte werden **nicht** geraten.
Intern bleiben sie ``None``; bei jeder Ausgabe (CLI, Export, Bericht) werden
sie als ``UNKNOWN`` bzw. ``NOT_AVAILABLE`` dargestellt. Geschaetzte Werte
tragen immer ein eigenes Kennzeichen (``estimated=True``).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

UNKNOWN = "UNKNOWN"
NOT_AVAILABLE = "NOT_AVAILABLE"


def utcnow() -> datetime:
    return datetime.now(UTC)


def display(value: Any, *, missing: str = UNKNOWN) -> str:
    """Wert fuer die Ausgabe rendern; ``None``/leer wird zu UNKNOWN."""
    if value is None:
        return missing
    if isinstance(value, str) and not value.strip():
        return missing
    if isinstance(value, (list, tuple, set, dict)) and not value:
        return missing
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value)
    return str(value)


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
#: Tausendertrennzeichen innerhalb einer Zahl (2.000 / 2 000 -> 2000). Ohne das
#: zerfaellt "2.000 Monitoren" zu "2 000 monitoren" und derselbe Auftrag landet
#: je nach Schreibweise der Quelle in unterschiedlichen Vergleichsgruppen.
_THOUSANDS = re.compile(r"(?<=\d)[.\u00a0 ](?=\d{3}(?!\d))")


def normalize_text(value: str | None) -> str:
    """Normalisiert Text fuer Vergleiche (Dubletten, Matching)."""
    if not value:
        return ""
    # Reihenfolge wichtig: erst deutsche Umlaute transkribieren, dann zerlegen -
    # sonst wird aus "ü" ein "u" statt "ue" und Titelvergleiche werden ungenau.
    text = value.casefold()
    text = text.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = _THOUSANDS.sub("", text)
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


class Provenance(BaseModel):
    """Herkunftsnachweis - jede wichtige Aussage bleibt ueberpruefbar."""

    source: str
    source_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    method: str = "api"  # "api" | "rss" | "html" | "document" | "manual" | "ai"
    document: str | None = None
    page: int | None = None
    section: str | None = None
    original_text: str | None = None
    confidence: int | None = None  # 0-100, verpflichtend bei KI-Ergebnissen


class EstimatedValue(BaseModel):
    """Zahl mit Schaetz-Kennzeichnung; nie als amtlicher Wert ausgeben."""

    value: float | None = None
    currency: str | None = None
    estimated: bool = False
    basis: str | None = None
    provenance: Provenance | None = None


#: Laenge des Vergabestellen-Anteils im Blocking-Schluessel.
BLOCKING_AUTHORITY_CHARS = 24
#: Anzahl Titelwoerter im Blocking-Schluessel.
BLOCKING_TITLE_WORDS = 3


def blocking_key(title: str | None, authority: str | None) -> str:
    """Grober Gruppenschluessel fuer die Dublettensuche.

    Statt jeden neuen Datensatz mit hunderten Kandidaten zu vergleichen, werden
    nur Datensaetze derselben Gruppe geprueft: normalisierte Vergabestelle plus
    die ersten Titelwoerter. Der Schluessel ist absichtlich grob - er engt die
    Kandidatenmenge ein, die Entscheidung faellt weiterhin der Aehnlichkeits-
    vergleich in ``tender_ai.pipeline.dedup``.
    """
    authority_part = normalize_text(authority)[:BLOCKING_AUTHORITY_CHARS]
    title_part = " ".join(normalize_text(title).split()[:BLOCKING_TITLE_WORDS])
    return f"{authority_part}|{title_part}"
