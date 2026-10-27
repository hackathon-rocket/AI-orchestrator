from pathlib import Path

import pytest

from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import Evidence, ExtractionData, Fact
from app.schemas.requests import Content, FormContext, Page
from app.validators.validation import validate_extraction, validate_fact


def proposal(value, quote, page=7, status="verified"):
    return Fact(
        value=value, evidence=Evidence(page=page, quote=quote), status=status, confidence=0.8
    )


def document(text):
    return Content(pages=[Page(page=7, text=text)])


@pytest.mark.parametrize(
    "heading",
    [
        "Daňový doklad - faktúra č. 2026/017",
        "INVOICE 2026/017",
        "Daňový doklad – FAKTÚRA\nč. 2026/017",
    ],
)
def test_real_heading_variants_are_supported(heading):
    content = document("Dodávateľ: Lipa\n" + heading + "\nSuma spolu: 123,00 EUR")
    fact = validate_fact("classification", proposal("invoice", heading), content, [])
    assert fact.status == "verified"


def test_multiline_parties_and_dates():
    text = (Path(__file__).parents[1] / "fixtures/realistic_invoice.txt").read_text(
        encoding="utf-8"
    )
    content = document(text)
    for field, value, quote in [
        ("supplier_ico", "12345679", "IČO: 12 345 679"),
        ("customer_ico", "87654326", "IČO: 87 654 326"),
        ("supplier_name", "Lipa tlač, s.r.o.", "Dodávateľ:\nLipa tlač, s.r.o."),
        ("issue_date", "2026-10-02", "Dátum vystavenia:\n02. 10. 2026"),
        ("total_gross", "123.00", "Celkom na úhradu:\n123,00"),
    ]:
        fact = validate_fact(field, proposal(value, quote), content, [])
        assert fact.status == "verified", field
        assert fact.evidence.quote == quote


def test_ambiguous_number_is_reviewable_but_invented_number_is_not():
    content = document("Referenčné údaje: 12 345 679")
    grounded = validate_fact(
        "supplier_ico", proposal("12345679", "Referenčné údaje: 12 345 679"), content, []
    )
    assert grounded.value == "12345679"
    assert grounded.status == "needs_review"
    unsupported = validate_fact(
        "supplier_ico", proposal("87654326", "Referenčné údaje: 12 345 679"), content, []
    )
    assert unsupported.value is None


def test_conflicting_document_headings_not_verified(settings):
    text = "FAKTÚRA č. F-1\nSuma spolu: 100 EUR\nZMLUVA č. Z-1\nPredmet zmluvy: Nájom"
    raw = ExtractionData(
        document_type="invoice", classification=proposal("invoice", "FAKTÚRA č. F-1")
    )
    raw.fields.total_gross = proposal("100", "Suma spolu: 100 EUR")
    result, warnings = validate_extraction(raw, document(text), settings)
    assert result.document_type == "unknown"
    assert result.fields.total_gross.value is not None
    assert warnings


def test_grounded_review_candidate_remains_visible(settings):
    content = document("FAKTÚRA č. F-1\nPredmet fakturácie: Servis zariadenia")
    raw = ExtractionData(
        document_type="invoice", classification=proposal("invoice", "FAKTÚRA č. F-1")
    )
    raw.fields.expense_description = proposal(
        "Servis zariadenia", "Predmet fakturácie: Servis zariadenia", status="needs_review"
    )
    data, _ = validate_extraction(raw, content, settings)
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    config, form, _ = loader.resolve(FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default"))
    _, tables, warnings = map_extraction(data, config, form)
    cell = tables[0].rows[0]["nazov_vydavku"]
    assert cell.value == "Servis zariadenia"
    assert cell.status == "needs_review"
    assert warnings


@pytest.mark.parametrize("currency_text", ["Mena dokladu: EUR", "Všetky sumy sú uvedené v EUR"])
def test_document_currency_context_resolves(settings, currency_text):
    text = "FAKTÚRA č. F-1\n" + currency_text + "\nSuma spolu: 120,00"
    raw = ExtractionData(
        document_type="invoice", classification=proposal("invoice", "FAKTÚRA č. F-1")
    )
    raw.fields.total_gross = proposal("120.00", "Suma spolu: 120,00")
    data, _ = validate_extraction(raw, document(text), settings)
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    config, form, _ = loader.resolve(FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default"))
    _, tables, _ = map_extraction(data, config, form)
    assert tables[0].rows[0]["suma_doklad"].value == 120.0


def test_missing_currency_is_not_verified_amount(settings):
    raw = ExtractionData()
    raw.fields.total_gross = proposal("120.00", "Suma spolu: 120,00")
    data, _ = validate_extraction(raw, document("Suma spolu: 120,00"), settings)
    assert data.fields.total_gross.value is not None
    assert data.fields.total_gross.status == "needs_review"


@pytest.mark.parametrize(
    "heading",
    [
        "Daňový doklad–faktúra č. 2026/017",
        "Daňový\ndoklad - faktúra č. 2026/017",
        "  Daňový   doklad -\nFAKTÚRA č. 2026/017",
    ],
)
def test_heading_ocr_wrapping_and_dash_spacing(heading):
    fact = validate_fact("classification", proposal("invoice", heading), document(heading), [])
    assert fact.status == "verified"


def test_body_quote_cannot_borrow_nearby_heading():
    text = "Invoice is mentioned here.\nINVOICE 2026/017"
    fact = validate_fact("classification", proposal("invoice", "Invoice"), document(text), [])
    assert fact.value is None


def test_non_eur_currency_is_preserved_without_conversion(settings):
    raw = ExtractionData()
    raw.fields.total_gross = proposal("120.00", "Suma spolu: 120,00 GBP")
    data, _ = validate_extraction(raw, document("Suma spolu: 120,00 GBP"), settings)
    assert data.fields.total_gross.status == "verified"
    assert data.fields.total_gross.currency.value == "GBP"


@pytest.mark.parametrize("conflict", [False, True])
def test_long_currency_source_keeps_bounded_evidence_and_conflicts(settings, conflict):
    currency_text = "Mena dokladu: EUR " + "poznámka " * 600
    if conflict:
        currency_text += " USD"
    text = currency_text + "\nSuma spolu: 120,00"
    raw = ExtractionData()
    raw.fields.total_gross = proposal("120.00", "Suma spolu: 120,00")
    data, _ = validate_extraction(raw, document(text), settings)
    currency = data.fields.total_gross.currency
    assert currency.status == ("needs_review" if conflict else "verified")
    assert currency.value == (None if conflict else "EUR")
    assert currency.evidence
    assert all(e.quote in text and len(e.quote) <= 4000 and e.page == 7 for e in currency.evidence)
    if conflict:
        assert "currency_conflict" in currency.reasons
        assert data.fields.total_gross._safe_to_suggest is False
