"""Classification must ground titles and distinguish narrow contract references."""

import pytest

from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import Evidence, ExtractionData, Fact
from app.schemas.requests import Content, FormContext, Page
from app.validators.validation import validate_extraction


def proposal(value, quote, page=1):
    return Fact(
        value=value, status="verified", confidence=0.9, evidence=Evidence(page=page, quote=quote)
    )


def analyze(settings, text, quote, kind="invoice", page=1):
    raw = ExtractionData(document_type=kind, classification=proposal(kind, quote, page))
    raw.fields.total_gross = proposal("123.00", "Suma spolu: 123,00 EUR")
    raw.fields.currency = proposal("EUR", "Mena dokladu: EUR")
    return validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)[0]


def mapped(settings, data):
    config, form, _ = ConfigLoader(settings.form_config_dir, FormSchemaParser()).resolve(
        FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    )
    return map_extraction(data, config, form)


@pytest.mark.parametrize(
    "heading,quote,reference",
    [
        ("FAKTÚRA č. F-1", "FAKTÚRA č. F-1", ""),
        ("Daňový doklad - faktúra č. 2026/017", "faktúra č. 2026/017", ""),
        ("Daňový doklad – faktúra\nč. 2026/017", "faktúra", ""),
        ("FAKTÚRA č. F-1", "FAKTÚRA č. F-1", "\nZmluva č. Z-2026/9"),
    ],
)
def test_supported_invoice_reaches_real_bbsk_mapping(settings, heading, quote, reference):
    text = heading + "\nMena dokladu: EUR\nSuma spolu: 123,00 EUR" + reference
    data = analyze(settings, text, quote)
    assert data.document_type == "invoice"
    assert data.classification.status == "verified"
    assert data.classification.evidence.quote == quote
    _, tables, _ = mapped(settings, data)
    assert tables[0].tag == "rozpocet.zoznamuctovnychdokladov"
    assert tables[0].rows[0]["suma_doklad"].value == 123.0
    assert tables[0].rows[0]["typ"].value == "FA"


@pytest.mark.parametrize(
    "text,quote,page",
    [
        ("This document references invoice F-1.", "invoice F-1", 1),
        ("FAKTÚRA č. F-1", "FAKTÚRA č. F-999", 1),
        ("FAKTÚRA č. F-1", "F-1", 1),
        ("FAKTÚRA č. F-1", "č. F-1", 1),
        ("FAKTÚRA č. F-1", "FAKTÚRA č. F-1", 2),
        ("Poznámka: Daňový doklad - faktúra č. F-1", "faktúra č. F-1", 1),
        ("Invoice is mentioned here.\nINVOICE F-1", "Invoice", 1),
        (
            "FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR\nZMLUVA č. Z-1\nPredmet zmluvy: Nájom",
            "FAKTÚRA č. F-1",
            1,
        ),
        (
            "FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR\nZmluva č. Z-1\nPredmet zmluvy: Nájom",
            "FAKTÚRA č. F-1",
            1,
        ),
        ("FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR\n\nZmluva č. Z-1", "FAKTÚRA č. F-1", 1),
        ("FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR\nZMLUVA č. Z-1", "FAKTÚRA č. F-1", 1),
        ("ZMLUVA č. Z-1\nPredmet zmluvy: Nájom\nFAKTÚRA č. F-1", "FAKTÚRA č. F-1", 1),
    ],
)
def test_unsupported_or_conflicting_documents_stay_blocked(settings, text, quote, page):
    data = analyze(settings, text, quote, page=page)
    assert data.document_type == "unknown"
    assert data.classification.status != "verified"
    assert not data.classification._safe_to_suggest
    fields, tables, _ = mapped(settings, data)
    assert fields == tables == []


@pytest.mark.parametrize(
    "kind,text",
    [
        ("contract", "ZMLUVA č. Z-2026/9\nPredmet zmluvy: Prenájom priestorov"),
        ("contract", "Zmluva č. Z-2026/9"),
        ("receipt", "POKLADNIČNÝ DOKLAD č. P-1"),
        ("bank_statement", "VÝPIS Z ÚČTU"),
    ],
)
def test_other_primary_types_remain_supported(settings, kind, text):
    data = analyze(settings, text, text.splitlines()[0], kind)
    assert data.document_type == kind
    assert data.classification.status == "verified"


def test_contract_reference_cannot_be_used_as_primary_classification(settings):
    text = "FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR\nZmluva č. Z-2026/9"
    data = analyze(settings, text, "Zmluva č. Z-2026/9", "contract")
    assert data.document_type == "unknown"
    assert not data.classification._safe_to_suggest


def test_conflicting_heading_on_another_page_is_not_a_reference(settings):
    raw = ExtractionData(
        document_type="invoice", classification=proposal("invoice", "FAKTÚRA č. F-1")
    )
    content = Content(
        pages=[
            Page(page=1, text="FAKTÚRA č. F-1\nSuma spolu: 123,00 EUR"),
            Page(page=2, text="Zmluva č. Z-1"),
        ]
    )
    data, _ = validate_extraction(raw, content, settings)
    assert data.document_type == "unknown"
    assert data.classification.validation_reasons == ["classification_conflict"]
