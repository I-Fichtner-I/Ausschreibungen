"""Textextraktion aus Vergabeunterlagen (Stufe 2).

Jeder Dateityp hat einen eigenen Extraktor mit gemeinsamer Schnittstelle; die
Auswahl erfolgt ueber Media-Type und Dateiendung. Neue Formate werden ergaenzt,
indem eine Klasse von ``DocumentExtractor`` abgeleitet und registriert wird -
die aufrufenden Stellen aendern sich nicht.
"""

from .base import DocumentExtractor, extract_document, extractor_for, register_extractor
from .docx import DocxExtractor
from .html import HtmlExtractor
from .pdf import PdfExtractor
from .text import PlainTextExtractor
from .xlsx import XlsxExtractor

__all__ = [
    "DocumentExtractor",
    "DocxExtractor",
    "HtmlExtractor",
    "PdfExtractor",
    "PlainTextExtractor",
    "XlsxExtractor",
    "extract_document",
    "extractor_for",
    "register_extractor",
]
