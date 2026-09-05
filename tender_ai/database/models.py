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
    risk_analysis: Mapped[RiskAnalysisRecord | None] = relationship(
        back_populates="tender", cascade="all, delete-orphan", uselist=False
    )
    items: Mapped[list[TenderItemRecord]] = relationship(
        back_populates="tender", cascade="all, delete-orphan"
    )
    item_extraction: Mapped[ItemExtractionRecord | None] = relationship(
        back_populates="tender", cascade="all, delete-orphan", uselist=False
    )
    price_research: Mapped[PriceResearchRecord | None] = relationship(
        back_populates="tender", cascade="all, delete-orphan", uselist=False
    )
    calculation: Mapped[CalculationRecord | None] = relationship(
        back_populates="tender", cascade="all, delete-orphan", uselist=False
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


class RiskAnalysisRecord(Base):
    """Risikobewertung einer Ausschreibung (Stufe 2B).

    Je Ausschreibung wird die jeweils aktuelle Bewertung gehalten; die Faktoren
    liegen als JSON bei, damit die Begruendung erhalten bleibt und nicht aus
    dem Score zurueckgerechnet werden muss.
    """

    __tablename__ = "risk_analyses"

    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    level: Mapped[str] = mapped_column(String(16), default="LOW", index=True)
    factors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    findings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    documents_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    documents_unreadable: Mapped[int] = mapped_column(Integer, default=0)
    characters_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    #: Inhalts-Hash der Ausschreibung zum Bewertungszeitpunkt. Nur wenn er sich
    #: aendert, ist eine Neubewertung noetig - ``updated_at`` taugt dafuer
    #: nicht, weil die Analyse selbst den Datensatz schreibt.
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="risk_analysis")


class ItemExtractionRecord(Base):
    """Lauf der Artikelerkennung je Ausschreibung (Stufe 3).

    Haelt die Kennzahlen des Laufs getrennt von den Positionen: der taegliche
    Stapel muss wissen, ob sich seit der letzten Erkennung ueberhaupt etwas
    geaendert hat, ohne dafuer alle Positionen zu laden.
    """

    __tablename__ = "item_extractions"

    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    item_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    priceable_count: Mapped[int] = mapped_column(Integer, default=0)
    average_confidence: Mapped[int] = mapped_column(Integer, default=0)
    documents_scanned: Mapped[int] = mapped_column(Integer, default=0)
    tables_scanned: Mapped[int] = mapped_column(Integer, default=0)
    tables_used: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Inhalts-Hash der Ausschreibung zum Erkennungszeitpunkt - nur bei
    #: Aenderung ist ein erneuter Lauf noetig (wie bei ``risk_analyses``).
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="item_extraction")


class TenderItemRecord(Base):
    """Eine erkannte Position des Leistungsverzeichnisses (Stufe 3).

    Die Fundstelle (Dokument, Seite, Originalzeile) steht in derselben Zeile:
    ohne sie waere eine Menge nur eine Zahl ohne Beleg.
    """

    __tablename__ = "tender_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    #: Reihenfolge im Leistungsverzeichnis - fuer stabile Ausgaben.
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[str | None] = mapped_column(String(64), default=None)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    quantity_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    unit: Mapped[str | None] = mapped_column(String(16), default=None, index=True)
    unit_original: Mapped[str | None] = mapped_column(String(64), default=None)

    manufacturer: Mapped[str | None] = mapped_column(String(255), default=None)
    model_number: Mapped[str | None] = mapped_column(String(255), default=None)
    article_number: Mapped[str | None] = mapped_column(String(128), default=None)
    specifications: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    brand_locked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    #: Guete der Produktzuordnung - bleibt leer bis Stufe 4.
    match_confidence: Mapped[int | None] = mapped_column(Integer, default=None)
    source_kind: Mapped[str] = mapped_column(String(16), default="TABLE")

    document: Mapped[str | None] = mapped_column(Text, default=None)
    page: Mapped[int | None] = mapped_column(Integer, default=None)
    section: Mapped[str | None] = mapped_column(Text, default=None)
    original_text: Mapped[str | None] = mapped_column(Text, default=None)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="items")
    quotes: Mapped[list[PriceQuoteRecord]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class PriceResearchRecord(Base):
    """Lauf der Preisrecherche je Ausschreibung (Stufe 4)."""

    __tablename__ = "price_research"

    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Positionen mit mindestens einem kalkulationsfaehigen Angebot.
    usable_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    coverage_percent: Mapped[int] = mapped_column(Integer, default=0, index=True)
    sources_used: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources_failed: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: Inhalts-Hash der Ausschreibung zum Rechercheszeitpunkt (Stapellauf).
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    researched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="price_research")


class PriceQuoteRecord(Base):
    """Ein Angebot zu einer Position - mit Herkunft und Zuordnungsguete.

    Preise altern: ohne ``retrieved_at`` waere spaeter nicht zu sagen, ob eine
    Kalkulation auf einem Preis von gestern oder vom letzten Jahr beruht.
    """

    __tablename__ = "price_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tender_items.id", ondelete="CASCADE"), index=True
    )
    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), index=True
    )
    #: Reihenfolge der Bewertung - bestes Angebot zuerst.
    rank: Mapped[int] = mapped_column(Integer, default=0)

    source: Mapped[str] = mapped_column(String(64), index=True)
    supplier: Mapped[str] = mapped_column(String(255), index=True)
    product_name: Mapped[str] = mapped_column(Text)
    manufacturer: Mapped[str | None] = mapped_column(String(255), default=None)
    model_number: Mapped[str | None] = mapped_column(String(255), default=None)
    article_number: Mapped[str | None] = mapped_column(String(128), default=None)

    amount: Mapped[float | None] = mapped_column(Float, default=None)
    currency: Mapped[str | None] = mapped_column(String(8), default=None)
    basis: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    vat_rate: Mapped[float | None] = mapped_column(Float, default=None)
    #: Abgeleiteter Nettobetrag; ``None``, wenn er sich nicht ableiten laesst.
    net_amount: Mapped[float | None] = mapped_column(Float, default=None)
    unit: Mapped[str | None] = mapped_column(String(16), default=None)
    tiers: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)

    shipping_cost: Mapped[float | None] = mapped_column(Float, default=None)
    shipping_included: Mapped[bool | None] = mapped_column(Boolean, default=None)
    min_order_quantity: Mapped[float | None] = mapped_column(Float, default=None)
    availability: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    lead_time_days: Mapped[int | None] = mapped_column(Integer, default=None)

    match_confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    concerns: Mapped[list[str]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)

    url: Mapped[str | None] = mapped_column(Text, default=None)
    document: Mapped[str | None] = mapped_column(Text, default=None)
    original_text: Mapped[str | None] = mapped_column(Text, default=None)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    item: Mapped[TenderItemRecord] = relationship(back_populates="quotes")


class CalculationRecord(Base):
    """Kalkulation und Entscheidungsvorlage einer Ausschreibung (Stufe 5).

    Ausdruecklich kein Angebot: ``user_decision`` auf ``TenderRecord`` bleibt
    die einzige Stelle, an der ein Mensch etwas freigibt.
    """

    __tablename__ = "calculations"

    tender_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("tenders.id", ondelete="CASCADE"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(32), default="NOT_ASSESSABLE", index=True)
    score: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    coverage_percent: Mapped[int] = mapped_column(Integer, default=0, index=True)
    currency: Mapped[str | None] = mapped_column(String(8), default=None)

    position_count: Mapped[int] = mapped_column(Integer, default=0)
    calculated_count: Mapped[int] = mapped_column(Integer, default=0)
    #: Erwartungsfall - die Zahlen, auf die jemand schaut.
    cost_total: Mapped[float | None] = mapped_column(Float, default=None)
    sale_total: Mapped[float | None] = mapped_column(Float, default=None)
    margin_absolute: Mapped[float | None] = mapped_column(Float, default=None)
    margin_percent: Mapped[float | None] = mapped_column(Float, default=None, index=True)
    roi_percent: Mapped[float | None] = mapped_column(Float, default=None)

    scenarios: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    positions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_notes: Mapped[list[str]] = mapped_column(JSON, default=list)

    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    tender: Mapped[TenderRecord] = relationship(back_populates="calculation")


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
