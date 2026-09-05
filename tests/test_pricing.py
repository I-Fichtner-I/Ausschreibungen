"""Stufe 4: Produkt-Matching, Preisstatistik und Katalogquelle."""

from __future__ import annotations

from pathlib import Path

import pytest

from tender_ai.config import CatalogPriceSourceConfig, Settings
from tender_ai.core.errors import ConfigError
from tender_ai.core.http import HttpClient
from tender_ai.models.item import TenderItem
from tender_ai.models.price import Availability, PriceBasis, PriceQuote, PriceTier
from tender_ai.pricing.matching import BRAND_MISMATCH_CAP, match_quote, match_quotes, title_overlap
from tender_ai.pricing.sources.base import ProductQuery
from tender_ai.pricing.sources.catalog import CatalogPriceSource
from tender_ai.pricing.statistics import price_statistics

CSV_HEADER = (
    "Artikelnummer;Bezeichnung;Hersteller;Typ;Preis;Waehrung;Preisbasis;MwSt;"
    "Einheit;Staffelpreise;Versandkosten;Verfuegbarkeit;Lieferzeit;Lieferant\n"
)
CSV_ROWS = (
    "MX27-001;Monitor 27 Zoll IPS entspiegelt;Muster GmbH;MX-27;189,00;EUR;netto;19;"
    "STK;50:179,00|100:172,50;0,00;Lager;3;Muster Distribution\n"
    "MON27-B;Monitor 27 Zoll IPS;Andere AG;ZZ-1;119,00;EUR;netto;19;STK;;0,00;Lager;7;"
    "Zweitlieferant\n"
    "STU-9;Buerostuhl drehbar mit Armlehne;Sitzwerk;BS-9;129,00;EUR;netto;19;STK;;5,90;"
    "Auf Bestellung;14;Muster Distribution\n"
)


def _quote(**overrides) -> PriceQuote:
    payload: dict = {
        "supplier": "Muster Distribution",
        "product_name": "Monitor 27 Zoll IPS",
        "manufacturer": "Muster GmbH",
        "model_number": "MX-27",
        "amount": 189.0,
        "currency": "EUR",
        "basis": PriceBasis.NET,
        "unit": "STK",
    }
    payload.update(overrides)
    return PriceQuote(**payload)


def _item(**overrides) -> TenderItem:
    payload: dict = {
        "title": "Monitor 27 Zoll IPS",
        "manufacturer": "Muster GmbH",
        "model_number": "MX-27",
        "unit": "STK",
    }
    payload.update(overrides)
    return TenderItem(**payload)


# --------------------------------------------------------------------------
# Netto/Brutto - der teuerste Kalkulationsfehler
# --------------------------------------------------------------------------


def test_net_price_needs_no_conversion():
    assert _quote(basis=PriceBasis.NET, amount=100.0).net_amount() == (100.0, None)


def test_gross_price_without_vat_rate_yields_no_net_amount():
    """19 Prozent zu unterstellen waere bei ermaessigten Saetzen schlicht falsch."""
    net, reason = _quote(basis=PriceBasis.GROSS, amount=119.0).net_amount()
    assert net is None
    assert "Steuersatz" in reason


def test_gross_price_with_vat_rate_converts():
    net, reason = _quote(basis=PriceBasis.GROSS, amount=119.0, vat_rate=0.19).net_amount()
    assert net == pytest.approx(100.0)
    assert reason is None


def test_unknown_basis_yields_no_net_amount():
    net, reason = _quote(basis=PriceBasis.UNKNOWN).net_amount()
    assert net is None
    assert "Netto/Brutto" in reason


def test_tier_price_applies_from_its_quantity():
    quote = _quote(
        amount=189.0,
        tiers=[PriceTier(min_quantity=50, amount=179.0), PriceTier(min_quantity=100, amount=172.5)],
    )
    assert quote.amount_for(10) == 189.0
    assert quote.amount_for(50) == 179.0
    assert quote.amount_for(120) == 172.5
    # Ohne Menge gilt der Grundpreis - eine Staffel waere eine Annahme.
    assert quote.amount_for(None) == 189.0


# --------------------------------------------------------------------------
# Zuordnung
# --------------------------------------------------------------------------


def test_identical_article_number_scores_highest():
    match = match_quote(
        _item(article_number="MX27-001"), _quote(article_number="mx27 001", model_number=None)
    )
    assert match.match_confidence >= 90
    assert any("Artikelnummer" in reason for reason in match.reasons)


def test_manufacturer_and_model_score_high():
    match = match_quote(_item(), _quote())
    assert match.match_confidence >= 85
    assert any("Hersteller und Typ" in reason for reason in match.reasons)


def test_name_similarity_alone_stays_below_the_usual_threshold():
    """Ein aehnlicher Name ist kein Produkt - und darf keine Marge tragen."""
    item = _item(manufacturer=None, model_number=None)
    match = match_quote(item, _quote(manufacturer=None, model_number=None))
    assert 0 < match.match_confidence < 85
    assert not match.is_usable(85)


def test_unrelated_product_scores_zero_and_is_dropped():
    match = match_quote(
        _item(),
        _quote(product_name="Buerostuhl drehbar", manufacturer="Sitzwerk", model_number="BS-9"),
    )
    assert match.match_confidence == 0
    assert match_quotes(_item(), [match.quote]) == []


def test_brand_locked_position_caps_a_foreign_make():
    """Die wichtigste Sperre: das billigere Fremdfabrikat darf nicht gewinnen."""
    item = _item(brand_locked=True)
    cheaper = _quote(manufacturer="Andere AG", model_number="ZZ-1", amount=119.0)
    match = match_quote(item, cheaper)
    assert match.match_confidence <= BRAND_MISMATCH_CAP
    assert any("Fabrikatsvorgabe" in concern for concern in match.concerns)
    assert not match.is_usable(85)


def test_unit_mismatch_is_capped_and_explained():
    match = match_quote(_item(unit="STK"), _quote(unit="M"))
    assert match.match_confidence <= 55
    assert any("Einheit weicht ab" in concern for concern in match.concerns)


def test_uncalculable_price_is_flagged_as_a_concern():
    match = match_quote(_item(), _quote(basis=PriceBasis.GROSS, vat_rate=None))
    assert match.match_confidence >= 85  # die Zuordnung stimmt
    assert any("nicht kalkulationsfaehig" in concern for concern in match.concerns)


def test_matches_are_sorted_by_confidence_then_price():
    item = _item()
    expensive = _quote(amount=199.0)
    cheap = _quote(amount=149.0)
    weak = _quote(manufacturer=None, model_number=None, amount=99.0)
    matches = match_quotes(item, [expensive, weak, cheap])
    assert matches[0].quote.amount == 149.0  # gleiche Guete, guenstiger zuerst
    assert matches[-1].quote.amount == 99.0  # schwaechere Zuordnung zuletzt


def test_title_overlap_ignores_filler_words_and_german_plurals():
    """Ohne Flexionsabgleich haetten "Monitoren" und "Monitor" nichts gemeinsam."""
    assert title_overlap("Lieferung von Monitoren", "Monitor 27 Zoll") >= 0.5
    assert title_overlap("Kabel Kategorie 7", "Kabeln Kategorie 7") == 1.0
    assert title_overlap("Monitor 27 Zoll", "Buerostuhl drehbar") == 0.0
    assert title_overlap(None, "Monitor") == 0.0


def test_stemming_stays_conservative():
    """Lieber eine Zuordnung verpassen als eine falsche erfinden."""
    assert title_overlap("Messer", "Messe") == 0.0
    assert title_overlap("Rechner", "Rechnung") == 0.0
    # Umlaut-Plural bleibt unerkannt - dafuer braeuchte es einen echten
    # Stemmer; Hersteller und Typ tragen diese Faelle.
    assert title_overlap("Buerostuehle", "Buerostuhl") == 0.0


# --------------------------------------------------------------------------
# Preisbild
# --------------------------------------------------------------------------


def test_statistics_use_only_usable_offers():
    item = _item()
    matches = match_quotes(
        item,
        [
            _quote(amount=189.0),
            _quote(amount=179.0, supplier="Zweiter"),
            # zu schwache Zuordnung - zaehlt nicht mit
            _quote(amount=99.0, manufacturer=None, model_number=None, supplier="Dritter"),
        ],
    )
    statistics, _warnings = price_statistics(matches, minimum_confidence=85)
    assert statistics.offer_count == 3
    assert statistics.usable_count == 2
    assert statistics.minimum == 179.0
    assert statistics.maximum == 189.0
    assert statistics.currency == "EUR"


def test_single_usable_offer_is_marked_as_such():
    statistics, _warnings = price_statistics(
        match_quotes(_item(), [_quote()]), minimum_confidence=85
    )
    assert statistics.is_single_source


def test_uncalculable_offers_are_reported_not_counted():
    matches = match_quotes(_item(), [_quote(basis=PriceBasis.UNKNOWN)])
    statistics, warnings = price_statistics(matches, minimum_confidence=85)
    assert statistics.usable_count == 0
    assert any("Netto/Brutto" in warning for warning in warnings)


def test_foreign_currency_is_excluded_not_converted():
    """Ein erfundener Kurs waere ein erfundener Preis."""
    matches = match_quotes(
        _item(), [_quote(amount=189.0), _quote(amount=200.0, currency="CHF", supplier="Schweiz")]
    )
    statistics, warnings = price_statistics(matches, minimum_confidence=85, currencies=["EUR"])
    assert statistics.currency == "EUR"
    assert statistics.usable_count == 1
    assert any("nicht umgerechnet" in warning for warning in warnings)


def test_statistics_of_nothing_stay_empty():
    statistics, warnings = price_statistics([], minimum_confidence=85)
    assert statistics.offer_count == 0
    assert warnings == []


def test_spread_ratio_flags_a_suspicious_mix():
    matches = match_quotes(_item(), [_quote(amount=100.0), _quote(amount=400.0, supplier="Teuer")])
    statistics, _warnings = price_statistics(matches, minimum_confidence=85)
    assert statistics.spread_ratio is not None
    assert statistics.spread_ratio > 1.0


# --------------------------------------------------------------------------
# Katalogquelle
# --------------------------------------------------------------------------


@pytest.fixture
def price_list(tmp_path: Path) -> Path:
    path = tmp_path / "preise.csv"
    path.write_text(CSV_HEADER + CSV_ROWS, encoding="utf-8")
    return path


def _source(settings: Settings, path: Path, **overrides) -> CatalogPriceSource:
    payload: dict = {"type": "catalog", "path": str(path)}
    payload.update(overrides)
    return CatalogPriceSource(
        name="liste",
        config=CatalogPriceSourceConfig.model_validate(payload),
        http=HttpClient(settings.http),
        settings=settings,
    )


async def test_catalog_reads_a_csv_price_list(settings: Settings, price_list: Path):
    source = _source(settings, price_list)
    quotes = await source.search(ProductQuery(text="Monitor 27 Zoll IPS"))

    assert len(quotes) == 2
    first = quotes[0]
    assert first.manufacturer == "Muster GmbH"
    assert first.model_number == "MX-27"
    assert first.amount == 189.0
    assert first.basis is PriceBasis.NET
    assert first.vat_rate == pytest.approx(0.19)
    assert first.availability is Availability.IN_STOCK
    assert first.lead_time_days == 3
    assert [tier.amount for tier in first.tiers] == [179.0, 172.5]
    assert first.provenance and first.provenance.document == "preise.csv"


async def test_catalog_finds_by_article_number(settings: Settings, price_list: Path):
    source = _source(settings, price_list)
    quotes = await source.search(
        ProductQuery(text="voellig anderer Text", article_number="MX27-001")
    )
    assert quotes and quotes[0].article_number == "MX27-001"


async def test_missing_basis_stays_unknown(settings: Settings, tmp_path: Path):
    """Ohne Angabe in Liste und Konfiguration wird nichts unterstellt."""
    path = tmp_path / "ohne_basis.csv"
    path.write_text("Bezeichnung;Preis\nMonitor 27 Zoll;189,00\n", encoding="utf-8")
    source = _source(settings, path)
    quote = (await source.search(ProductQuery(text="Monitor 27 Zoll")))[0]
    assert quote.basis is PriceBasis.UNKNOWN
    assert quote.net_amount()[0] is None
    assert any("Netto/Brutto" in warning for warning in quote.warnings)


async def test_default_basis_from_configuration_is_applied(settings: Settings, tmp_path: Path):
    """Eine Ansage des Nutzers ist keine Annahme des Tools."""
    path = tmp_path / "ohne_basis.csv"
    path.write_text("Bezeichnung;Preis\nMonitor 27 Zoll;189,00\n", encoding="utf-8")
    source = _source(settings, path, default_basis="netto")
    quote = (await source.search(ProductQuery(text="Monitor 27 Zoll")))[0]
    assert quote.basis is PriceBasis.NET
    assert quote.net_amount()[0] == 189.0


async def test_catalog_reads_json(settings: Settings, tmp_path: Path):
    path = tmp_path / "preise.json"
    path.write_text(
        '{"products": [{"Bezeichnung": "Monitor 27 Zoll IPS", "Preis": 189.0, '
        '"Preisbasis": "netto", "Hersteller": "Muster GmbH"}]}',
        encoding="utf-8",
    )
    quotes = await _source(settings, path).search(ProductQuery(text="Monitor 27 Zoll IPS"))
    assert quotes[0].amount == 189.0
    assert quotes[0].basis is PriceBasis.NET


async def test_missing_price_list_is_reported(settings: Settings, tmp_path: Path):
    source = _source(settings, tmp_path / "gibt-es-nicht.csv")
    with pytest.raises(ConfigError):
        await source.search(ProductQuery(text="Monitor"))

    status = await source.health_check()
    assert status.ok is False
    assert "nicht gefunden" in status.message


async def test_health_check_counts_calculable_rows(settings: Settings, price_list: Path):
    status = await _source(settings, price_list).health_check()
    assert status.ok is True
    assert "3 Zeile(n)" in status.message
    assert "3 kalkulationsfaehig" in status.message


async def test_health_check_hints_at_the_missing_basis(settings: Settings, tmp_path: Path):
    path = tmp_path / "ohne_basis.csv"
    path.write_text("Bezeichnung;Preis\nMonitor 27 Zoll;189,00\n", encoding="utf-8")
    status = await _source(settings, path).health_check()
    assert "default_basis" in status.message


async def test_catalog_reads_xlsx(settings: Settings, tmp_path: Path):
    """Lieferantenlisten kommen oft als Excel-Mappe."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Preise"
    sheet.append(["Bezeichnung", "Hersteller", "Typ", "Preis", "Preisbasis", "Einheit"])
    sheet.append(["Monitor 27 Zoll IPS", "Muster GmbH", "MX-27", 189.0, "netto", "STK"])
    sheet.append([None, None, None, None, None, None])  # Leerzeile wird uebersprungen
    path = tmp_path / "preise.xlsx"
    workbook.save(path)

    quotes = await _source(settings, path, sheet="Preise").search(
        ProductQuery(text="Monitor 27 Zoll IPS")
    )

    assert len(quotes) == 1
    assert quotes[0].amount == 189.0
    assert quotes[0].basis is PriceBasis.NET
    assert quotes[0].manufacturer == "Muster GmbH"


async def test_column_mapping_tolerates_header_spelling(settings: Settings, tmp_path: Path):
    """ "BEZEICHNUNG " und "Bezeichnung" sind dieselbe Spalte."""
    path = tmp_path / "preise.csv"
    path.write_text("BEZEICHNUNG ;PREIS\nMonitor 27 Zoll;189,00\n", encoding="utf-8")
    quotes = await _source(settings, path, default_basis="netto").search(
        ProductQuery(text="Monitor 27 Zoll")
    )
    assert quotes and quotes[0].amount == 189.0


async def test_rows_without_a_name_are_skipped(settings: Settings, tmp_path: Path):
    path = tmp_path / "preise.csv"
    path.write_text("Bezeichnung;Preis\n;99,00\nMonitor 27 Zoll;189,00\n", encoding="utf-8")
    source = _source(settings, path, default_basis="netto")
    status = await source.health_check()
    assert "2 Zeile(n), 1 mit Bezeichnung" in status.message
