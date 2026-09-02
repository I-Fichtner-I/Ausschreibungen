"""Anwendungsdienste: die Ablaeufe hinter den Oberflaechen.

Die CLI - und spaeter das Dashboard (Stufe 6) - rufen ausschliesslich diese
Funktionen auf. Sie kapseln HTTP-Client-Aufbau, Quellenauswahl, Session-
Handling und Fehlerfaelle; die Oberflaechen kuemmern sich nur noch um Ein- und
Ausgabe. Hier wird bewusst nichts formatiert (kein Rich, keine Konsole).
"""

from .analysis import BatchAnalysisReport, analyze_open_tenders, analyze_tender
from .documents import DocumentReport, DocumentResult, documents_from_db, fetch_documents
from .health import check_sources
from .items import BatchItemReport, extract_items_for_open_tenders, extract_tender_items
from .search import run_search

__all__ = [
    "BatchAnalysisReport",
    "BatchItemReport",
    "DocumentReport",
    "DocumentResult",
    "analyze_open_tenders",
    "analyze_tender",
    "check_sources",
    "documents_from_db",
    "extract_items_for_open_tenders",
    "extract_tender_items",
    "fetch_documents",
    "run_search",
]
