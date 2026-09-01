"""Fehlerklassen.

Grundregel der Pipeline: ein Fehler in einer Quelle darf niemals den
Gesamtlauf stoppen. Deshalb sind Quellfehler eigene, gut abgrenzbare Typen,
die der Orchestrator einzeln abfangen und protokollieren kann.
"""

from __future__ import annotations


class TenderAIError(Exception):
    """Basisklasse aller Fehler dieses Projekts."""


class ConfigError(TenderAIError):
    """Fehlerhafte oder unvollstaendige Konfiguration."""


class SourceError(TenderAIError):
    """Fehler beim Abruf oder Parsen einer Ausschreibungsquelle."""

    def __init__(self, source: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"[{source}] {message}")
        self.source = source
        self.message = message
        self.retryable = retryable


class HttpError(TenderAIError):
    """HTTP-Abruf endgueltig fehlgeschlagen (nach allen Wiederholungen)."""

    def __init__(self, url: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"{message} ({url})")
        self.url = url
        self.status_code = status_code


class RobotsDisallowedError(TenderAIError):
    """Abruf durch robots.txt untersagt - wird nicht umgangen."""

    def __init__(self, url: str) -> None:
        super().__init__(
            f"robots.txt der Zielseite untersagt den automatisierten Abruf: {url}"
        )
        self.url = url


class AccessRestrictedError(TenderAIError):
    """Zugriff erfordert Login/Captcha/Bezahlung - bewusst nicht umgangen."""

    def __init__(self, url: str, hint: str = "") -> None:
        super().__init__(
            f"Zugriff auf {url} ist geschuetzt (Login/Captcha/Paywall) und wird "
            f"nicht automatisiert umgangen. {hint}".strip()
        )
        self.url = url
