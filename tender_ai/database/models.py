"""SQLAlchemy-Modelle.

Stufe 1 speichert Ausschreibungen, ihre Dubletten-Verweise, Dokumente,
Aenderungen und Laufprotokolle. Die Tabellen der spaeteren Stufen
(TenderItems, Suppliers, PriceOffers, CostCalculations,
ProfitabilityAnalysis, RiskAnalysis) werden hier ergaenzt; die
Beziehungspunkte (``tender_id``) sind bereits vorgesehen.

Das Schema laeuft unveraendert auf SQLite (lokal) und PostgreSQL (produktiv).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class TenderRecord(Base):
    __tablename__ = "tenders"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str] = mapped_column(String(255), index=True)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    national_id: Mapped[str | None] = mapped_column(String(255), index=True, default=None)

    title: Mapped[str | None] = mapped_column(Text, default=None)
    title_normalized: Mapped[str | None] = mapped_column(Text, default=None)
    contracting_authority: Mapped[str | None] = mapped_column(Text, default=None)
    authority_normalized: Mapped[str | None] = mapped_column(Text, default=None)
    #: Gruppenschluessel der Dublettensuche (Vergabestelle + Titelanfang).
    blocking_key: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    country: Mapped[str | None] = mapped_column(String(8), default=None)
    region: Mapped[str | None] = mapped_column(String(128), default=None)
    cpv_codes: Mapped[list[str]] = mapped_column(JSON, default=list)
    notice_type: Mapped[str | None] = mapped_column(String(128), default=None)
    procedure_type: Mapped[str | None] = mapped_column(String(128), default=None)
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)

    publication_date: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    submission_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True
    )
    estimated_value: Mapped[float | None] = mapped_column(Float, default=None)
    currency: Mapped[str | None] = mapped_column(String(8), default=None)

    #: Vollstaendiges Tender-Objekt als JSON - keine Information geht verloren.
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)

    #: Dublettenverwaltung: Primaerdatensatz zeigt auf sich selbst.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    primary_tender_id: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("tenders.id"), default=None, index=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    #: Nutzerentscheidung (Human-in-the-loop, ab Stufe 5)
    user_decision: Mapped[str | None] = mapped_column(String(32), default=None)

    aliases: Mapped[list[TenderAliasRecord]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    documents: Mapped[list[TenderDocumentRecord]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    changes: Mapped[list[TenderChangeRecord]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_tenders_source_source_id", "source", "source_id", unique=True),)


class TenderAliasRecord(Base):
    """Weitere Fundstellen derselben Ausschreibung (andere Portale)."""

    __tablename__ = "tender_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    match_reason: Mapped[str | None] = mapped_column(String(128), default=None)
    match_confidence: Mapped[int | None] = mapped_column(Integer, default=None)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="aliases")

    __table_args__ = (Index("ix_alias_source_source_id", "source", "source_id", unique=True),)


class TenderDocumentRecord(Base):
    __tablename__ = "tender_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str | None] = mapped_column(Text, default=None)
    url: Mapped[str | None] = mapped_column(Text, default=None)
    media_type: Mapped[str | None] = mapped_column(String(128), default=None)
    access: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    local_path: Mapped[str | None] = mapped_column(Text, default=None)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), default=None)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    tender: Mapped[TenderRecord] = relationship(back_populates="documents")
    extract: Mapped[DocumentExtractRecord | None] = relationship(
        back_populates="document", cascade="all, delete-orphan", uselist=False
    )


class DocumentExtractRecord(Base):
    """Extrahierter Inhalt einer Vergabeunterlage (Stufe 2).

    Getrennt von ``tender_documents``, weil der Text um Groessenordnungen
    groesser ist als die Metadaten - Listen und Suchen sollen ihn nicht
    mitladen muessen.
    """

    __tablename__ = "document_extracts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tender_documents.id", ondelete="CASCADE"), index=True
    )
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    extractor: Mapped[str | None] = mapped_column(String(32), default=None)
    status: Mapped[str] = mapped_column(String(32), default="OK", index=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)

    text: Mapped[str | None] = mapped_column(Text, default=None)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    table_count: Mapped[int] = mapped_column(Integer, default=0)
    character_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Erkannte Tabellen als JSON - Rohmaterial der Artikelerkennung (Stufe 3).
    tables: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    document: Mapped[TenderDocumentRecord] = relationship(back_populates="extract")


class TenderChangeRecord(Base):
    """Aenderungshistorie - Grundlage der Ueberwachung (Anforderung 16)."""

    __tablename__ = "tender_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(Text, default=None)
    new_value: Mapped[str | None] = mapped_column(Text, default=None)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    source: Mapped[str | None] = mapped_column(String(64), default=None)

    tender: Mapped[TenderRecord] = relationship(back_populates="changes")


class IngestRunRecord(Base):
    """Protokoll eines Rechercherlaufs - fuer Nachvollziehbarkeit und Scheduler."""

    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)
    query: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    found: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    http_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SourceStateRecord(Base):
    """Zustand je Quelle: letzter Erfolg, letzter Fehler, Trefferzahl."""

    __tablename__ = "source_states"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    last_result_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
