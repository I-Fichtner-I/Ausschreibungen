"""T-10: Skalierung und Recall der Dublettenerkennung.

Belegt die Akzeptanzkriterien aus der Roadmap:
- Stufe 3 bleibt auch bei vielen fremdquelligen Kandidaten schnell
- Dubletten werden auch jenseits des frueheren Kandidaten-Limits gefunden
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tender_ai.config import DedupConfig
from tender_ai.database.repository import TenderRepository
from tender_ai.database.session import session_scope
from tender_ai.models.common import blocking_key
from tender_ai.models.tender import Tender, TenderStatus

#: Wie viel langsamer ein Upsert bei zehnfacher Datenmenge hoechstens werden
#: darf. Vor T-10 wurde linear ueber bis zu 500 Kandidaten verglichen - der
#: Faktor lag dann bei ungefaehr 10. Der Blocking-Schluessel haelt ihn nahe 1;
#: 4 laesst Messrauschen zu und schlaegt trotzdem an, sobald wieder linear
#: gesucht wird.
MAX_SCALING_FACTOR = 4.0
#: Grobe Notbremse gegen eine voellige Entgleisung. Bewusst weit weg vom
#: Messwert (~2 ms): eine harte Millisekundengrenze haengt an Maschine und
#: Instrumentierung - unter Coverage flackerte eine 5-ms-Grenze - und wuerde
#: CI aus Gruenden rot faerben, die mit dem Code nichts zu tun haben.
MAX_MS_PER_UPSERT = 50.0


def make(index: int, source: str, *, title: str | None = None) -> Tender:
    return Tender(
        id=f"{source}:{index}",
        source=source,
        source_id=str(index),
        title=title or f"Lieferung von Geraeten Nr {index} fuer Verwaltung {index % 37}",
        contracting_authority=f"Stadt {index % 50}",
        country="DEU",
        publication_date=date(2026, 8, 1) + timedelta(days=index % 20),
        submission_deadline=datetime(2036, 9, 1, tzinfo=UTC) + timedelta(days=index % 30),
        status=TenderStatus.OPEN,
    )


@pytest.fixture
def repository(tmp_path: Path):
    with session_scope(f"sqlite:///{tmp_path / 'scaling.db'}") as session:
        yield TenderRepository(session, DedupConfig())


def test_blocking_key_groups_similar_tenders():
    """Dieselbe Ausschreibung, unterschiedliche Schreibweise der Menge."""
    left = blocking_key("Lieferung von 2.000 Monitoren", "Musterstadt - Zentrale Vergabestelle")
    right = blocking_key(
        "Lieferung von 2000 Monitoren fuer Schulen", "Musterstadt - Zentrale Vergabestelle"
    )
    assert left == right

    other = blocking_key("Reinigungsdienstleistungen", "Musterstadt - Zentrale Vergabestelle")
    assert other != left
    assert blocking_key(None, None) == "|"


def test_thousands_separators_do_not_split_groups():
    """Regressionsschutz: "2.000" und "2000" muessen gleich normalisiert werden."""
    from tender_ai.models.common import normalize_text

    assert normalize_text("Lieferung von 2.000 Monitoren") == "lieferung von 2000 monitoren"
    assert normalize_text("Lieferung von 2 000 Monitoren") == "lieferung von 2000 monitoren"
    assert normalize_text("1.234.567 Stueck") == "1234567 stueck"
    # Dezimalstellen bleiben unangetastet (keine Dreiergruppe)
    assert normalize_text("1,5 Mio") == "1 5 mio"


def _ms_per_upsert(repository: TenderRepository, corpus: int, *, samples: int = 150) -> float:
    """Kosten eines Upserts gegen einen Bestand von ``corpus`` Datensaetzen."""
    for index in range(corpus):
        repository.upsert(make(index, "a"))
    repository.session.commit()

    # Aufwaermen: der erste Durchlauf bezahlt das Uebersetzen der Statements
    # und wuerde sonst als Messwert durchgehen.
    for index in range(20):
        repository.upsert(make(index, "warmup"))
    repository.session.commit()

    started = time.perf_counter()
    for index in range(samples):
        repository.upsert(make(index, "b"))
    repository.session.commit()
    return (time.perf_counter() - started) * 1000 / samples


@pytest.mark.slow
def test_upsert_does_not_degrade_with_the_number_of_candidates(tmp_path: Path):
    """Frueher: 56 ms je Upsert bei 750 fremdquelligen Kandidaten (Cap 500).

    Gemessen wird das Verhaeltnis zwischen kleinem und zehnfach groesserem
    Bestand, nicht eine absolute Millisekundenzahl: nur das Verhaeltnis sagt
    etwas ueber den Algorithmus aus. Eine absolute Grenze misst die Maschine
    mit - unter Coverage ist derselbe Code doppelt so langsam, ohne dass sich
    am Verhalten etwas geaendert haette.
    """
    with session_scope(f"sqlite:///{tmp_path / 'small.db'}") as session:
        small = _ms_per_upsert(TenderRepository(session, DedupConfig()), 100)
    with session_scope(f"sqlite:///{tmp_path / 'large.db'}") as session:
        large = _ms_per_upsert(TenderRepository(session, DedupConfig()), 1000)

    factor = large / small if small > 0 else float("inf")
    assert factor < MAX_SCALING_FACTOR, (
        f"{small:.2f} ms bei 100 Kandidaten, {large:.2f} ms bei 1000 "
        f"(Faktor {factor:.1f}) - die Suche skaliert wieder mit der Datenmenge"
    )
    assert large < MAX_MS_PER_UPSERT, f"{large:.1f} ms je Upsert"


def test_duplicate_beyond_former_candidate_cap_is_found(repository: TenderRepository):
    """600 Datensaetze im Fenster, dann eine Dublette des aeltesten.

    Mit dem frueheren ``LIMIT 500`` nach ``last_seen_at`` waere der aelteste
    Datensatz nicht mehr unter den Kandidaten gewesen.
    """
    oldest = Tender(
        id="a:0",
        source="a",
        source_id="0",
        title="Lieferung von 2.000 Monitoren fuer Verwaltungsarbeitsplaetze",
        contracting_authority="Musterstadt - Zentrale Vergabestelle",
        publication_date=date(2026, 8, 10),
        submission_deadline=datetime(2036, 9, 15, tzinfo=UTC),
    )
    repository.upsert(oldest)
    for index in range(1, 601):
        repository.upsert(make(index, "a"))
    repository.session.commit()

    result = repository.upsert(
        Tender(
            id="b:0",
            source="b",
            source_id="0",
            title="Lieferung von 2.000 Monitoren fuer Verwaltungsarbeitsplaetze",
            contracting_authority="Musterstadt - Zentrale Vergabestelle",
            publication_date=date(2026, 8, 12),
        )
    )
    assert result.action == "duplicate"
    assert result.duplicate_of == "a:0"


def test_authority_path_catches_different_title_start(repository: TenderRepository):
    """Gleiche Vergabestelle, abweichender Titelanfang - zweiter Kandidatenpfad.

    Der Blocking-Schluessel unterscheidet sich (anderer Titelbeginn), die Frist
    ebenfalls (anderer Fingerprint) - gefunden wird die Dublette nur ueber den
    Kandidatenpfad "gleiche Vergabestelle".
    """
    first = Tender(
        id="a:1",
        source="a",
        source_id="1",
        title="Die Beschaffung von Buerostuehlen fuer das Rathaus Beispielstadt",
        contracting_authority="Landkreis Beispiel",
        publication_date=date(2026, 8, 10),
        submission_deadline=datetime(2036, 9, 1, tzinfo=UTC),
    )
    second = Tender(
        id="b:1",
        source="b",
        source_id="1",
        title="Beschaffung von Buerostuehlen fuer das Rathaus Beispielstadt",
        contracting_authority="Landkreis Beispiel",
        publication_date=date(2026, 8, 11),
        submission_deadline=datetime(2036, 9, 2, tzinfo=UTC),
    )
    assert blocking_key(first.title, first.contracting_authority) != blocking_key(
        second.title, second.contracting_authority
    )
    assert first.fingerprint() != second.fingerprint()

    repository.upsert(first)
    result = repository.upsert(second)
    assert result.action == "duplicate"
    assert result.duplicate_reason == "title_similarity"


def test_unrelated_tenders_of_same_authority_stay_separate(repository: TenderRepository):
    repository.upsert(
        Tender(
            id="a:2",
            source="a",
            source_id="2",
            title="Lieferung von Monitoren",
            contracting_authority="Landkreis Beispiel",
            publication_date=date(2026, 8, 10),
        )
    )
    result = repository.upsert(
        Tender(
            id="b:2",
            source="b",
            source_id="2",
            title="Reinigung von Schulgebaeuden",
            contracting_authority="Landkreis Beispiel",
            publication_date=date(2026, 8, 11),
        )
    )
    assert result.action == "new"


def test_publication_window_still_applies(repository: TenderRepository):
    """Unterschiedliche Fristen (kein Fingerprint-Treffer), Fenster ueberschritten."""
    repository.upsert(
        Tender(
            id="a:3",
            source="a",
            source_id="3",
            title="Lieferung von Monitoren",
            contracting_authority="Stadt A",
            publication_date=date(2026, 1, 1),
            submission_deadline=datetime(2036, 2, 1, tzinfo=UTC),
        )
    )
    result = repository.upsert(
        Tender(
            id="b:3",
            source="b",
            source_id="3",
            title="Lieferung von Monitoren",
            contracting_authority="Stadt A",
            publication_date=date(2026, 12, 1),  # ausserhalb des Fensters
            submission_deadline=datetime(2037, 1, 1, tzinfo=UTC),
        )
    )
    assert result.action == "new"
