"""Gemeinsame Schnittstelle und Registry der Dokumentextraktoren."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

from ..core.logging import get_logger
from ..models.document import ExtractedDocument, ExtractionStatus

log = get_logger(__name__)

#: Obergrenze fuer den extrahierten Text je Dokument. Vergabeunterlagen koennen
#: hunderte Seiten haben; alles vollstaendig im Speicher und in der Datenbank zu
#: halten bringt fuer die Analyse keinen Mehrwert.
DEFAULT_MAX_CHARACTERS = 2_000_000


class DocumentExtractor(ABC):
    """Basisklasse aller Extraktoren."""

    name: ClassVar[str] = "base"
    #: Media-Types, die dieser Extraktor bedient.
    media_types: ClassVar[tuple[str, ...]] = ()
    #: Dateiendungen (klein, mit Punkt) als Rueckfallebene.
    suffixes: ClassVar[tuple[str, ...]] = ()

    def __init__(self, max_characters: int = DEFAULT_MAX_CHARACTERS) -> None:
        self.max_characters = max_characters

    @classmethod
    def handles(cls, media_type: str | None, suffix: str) -> bool:
        if media_type and media_type.split(";")[0].strip().lower() in cls.media_types:
            return True
        return suffix.lower() in cls.suffixes

    @abstractmethod
    def _extract(self, path: Path, document: ExtractedDocument) -> None:
        """Seiten und Tabellen in ``document`` eintragen."""

    def extract(self, path: Path, media_type: str | None = None) -> ExtractedDocument:
        """Datei auslesen; Fehler werden als Status vermerkt, nicht geworfen."""
        document = ExtractedDocument(
            source_path=str(path),
            file_name=path.name,
            media_type=media_type,
            extractor=self.name,
        )
        try:
            data = path.read_bytes()
        except OSError as exc:
            document.status = ExtractionStatus.FAILED
            document.error = f"Datei nicht lesbar: {exc}"
            return document

        document.size_bytes = len(data)
        document.checksum_sha256 = hashlib.sha256(data).hexdigest()

        try:
            self._extract(path, document)
        except Exception as exc:  # noqa: BLE001 - ein defektes Dokument stoppt nie den Lauf
            log.warning("extraction_failed", path=str(path), extractor=self.name, error=str(exc))
            document.status = ExtractionStatus.FAILED
            document.error = f"{type(exc).__name__}: {exc}"
            return document

        self._apply_limit(document)
        if document.status is ExtractionStatus.OK and not document.text.strip():
            # Gelesen, aber kein Text: typischerweise ein gescanntes PDF. Das ist
            # kein Fehler, muss aber sichtbar sein - sonst sieht die Analyse ein
            # leeres Dokument und haelt es fuer inhaltslos.
            document.status = ExtractionStatus.EMPTY
        return document

    def _apply_limit(self, document: ExtractedDocument) -> None:
        if self.max_characters <= 0:
            return
        remaining = self.max_characters
        for index, page in enumerate(document.pages):
            if remaining <= 0:
                document.pages = document.pages[:index]
                document.truncated = True
                break
            if len(page.text) > remaining:
                page.text = page.text[:remaining]
                document.truncated = True
            remaining -= len(page.text)
        if document.truncated and document.status is ExtractionStatus.OK:
            document.status = ExtractionStatus.PARTIAL


EXTRACTORS: list[type[DocumentExtractor]] = []


def register_extractor(cls: type[DocumentExtractor]) -> type[DocumentExtractor]:
    EXTRACTORS.append(cls)
    return cls


def extractor_for(
    path: Path | str, media_type: str | None = None, **kwargs: object
) -> DocumentExtractor | None:
    """Passenden Extraktor waehlen - erst nach Media-Type, dann nach Endung."""
    suffix = Path(path).suffix.lower()
    for cls in EXTRACTORS:
        if cls.handles(media_type, suffix):
            return cls(**kwargs)  # type: ignore[arg-type]
    return None


def extract_document(
    path: Path | str, media_type: str | None = None, **kwargs: object
) -> ExtractedDocument:
    """Datei mit dem passenden Extraktor auslesen.

    Gibt es keinen Extraktor, wird das als ``UNSUPPORTED`` vermerkt statt einen
    Fehler zu werfen: ein unbekannter Anhang darf die Analyse der uebrigen
    Unterlagen nicht verhindern.
    """
    path = Path(path)
    extractor = extractor_for(path, media_type, **kwargs)
    if extractor is None:
        return ExtractedDocument(
            source_path=str(path),
            file_name=path.name,
            media_type=media_type,
            status=ExtractionStatus.UNSUPPORTED,
            error=f"Kein Extraktor fuer '{media_type or path.suffix or 'unbekannt'}'",
        )
    return extractor.extract(path, media_type)
