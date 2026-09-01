from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tender_ai.models.common import NOT_AVAILABLE, UNKNOWN, display, normalize_text
from tender_ai.models.tender import Tender, TenderDocument, TenderStatus


def make_tender(**overrides) -> Tender:
    base = dict(
        id="src:1",
        source="src",
        source_id="1",
        title="Lieferung von 2.000 Monitoren",
        contracting_authority="Musterstadt - Zentrale Vergabestelle",
        submission_deadline=datetime(2036, 9, 15, 12, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return Tender(**base)


def test_display_marks_missing_values():
    assert display(None) == UNKNOWN
    assert display("") == UNKNOWN
    assert display([]) == UNKNOWN
    assert display(None, missing=NOT_AVAILABLE) == NOT_AVAILABLE
    assert display(0) == "0"
    assert display(["a", "b"]) == "a, b"


def test_normalize_text_handles_umlauts_and_punctuation():
    assert normalize_text("Büroausstattung, Lose 1-3") == "bueroausstattung lose 1 3"
    assert normalize_text(None) == ""
    assert normalize_text("Straße") == "strasse"


def test_naive_deadline_is_treated_as_utc():
    tender = make_tender(submission_deadline=datetime(2036, 9, 15, 12))
    assert tender.submission_deadline.tzinfo is not None


def test_days_until_deadline_and_expiry():
    past = datetime.now(timezone.utc) - timedelta(days=5)
    assert make_tender(submission_deadline=past).is_expired is True
    future = datetime.now(timezone.utc) + timedelta(days=10)
    assert make_tender(submission_deadline=future).days_until_deadline in (9, 10)
    assert make_tender(submission_deadline=None).days_until_deadline is None


def test_fingerprint_prefers_national_id():
    left = make_tender(national_id="VG-2026-1", title="Titel A")
    right = make_tender(
        id="other:2", source="other", source_id="2", national_id="VG-2026-1", title="Titel B"
    )
    assert left.fingerprint() == right.fingerprint()


def test_fingerprint_falls_back_to_title_authority_deadline():
    left = make_tender()
    right = make_tender(id="other:2", source="other", source_id="2")
    assert left.fingerprint() == right.fingerprint()
    different = make_tender(title="Ganz anderer Auftrag")
    assert different.fingerprint() != left.fingerprint()


def test_content_hash_reacts_to_relevant_changes():
    tender = make_tender()
    before = tender.content_hash()
    tender.submission_deadline = datetime(2036, 10, 1, tzinfo=timezone.utc)
    assert tender.content_hash() != before

    tender2 = make_tender()
    before2 = tender2.content_hash()
    tender2.documents.append(TenderDocument(name="LV", url="https://example.invalid/lv.pdf"))
    assert tender2.content_hash() != before2


def test_cpv_codes_are_normalised():
    tender = make_tender(cpv_codes=" 30231300 ")
    assert tender.cpv_codes == ["30231300"]
    assert make_tender(cpv_codes=None).cpv_codes == []


def test_status_defaults_to_unknown():
    assert make_tender().status is TenderStatus.UNKNOWN
