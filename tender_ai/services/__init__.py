"""Anwendungsdienste: die Ablaeufe hinter den Oberflaechen.

Die CLI - und spaeter das Dashboard (Stufe 6) - rufen ausschliesslich diese
Funktionen auf. Sie kapseln HTTP-Client-Aufbau, Quellenauswahl, Session-
Handling und Fehlerfaelle; die Oberflaechen kuemmern sich nur noch um Ein- und
Ausgabe. Hier wird bewusst nichts formatiert (kein Rich, keine Konsole).
"""

from .documents import DocumentReport, DocumentResult, fetch_documents
from .health import check_sources
from .search import run_search

__all__ = [
    "DocumentReport",
    "DocumentResult",
    "check_sources",
    "fetch_documents",
    "run_search",
]
