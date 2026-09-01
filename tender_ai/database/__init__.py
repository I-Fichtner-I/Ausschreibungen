"""Persistenz: SQLAlchemy-Modelle, Session-Handling, Repository."""

from .models import (
    Base,
    IngestRunRecord,
    SourceStateRecord,
    TenderAliasRecord,
    TenderChangeRecord,
    TenderDocumentRecord,
    TenderRecord,
)
from .repository import TenderRepository, UpsertResult
from .session import create_all, get_engine, session_scope

__all__ = [
    "Base",
    "IngestRunRecord",
    "SourceStateRecord",
    "TenderAliasRecord",
    "TenderChangeRecord",
    "TenderDocumentRecord",
    "TenderRecord",
    "TenderRepository",
    "UpsertResult",
    "create_all",
    "get_engine",
    "session_scope",
]
