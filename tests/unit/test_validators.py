from decimal import Decimal

import pytest

from app.schemas.extraction import CellLocator, Evidence, Fact
from app.schemas.requests import Content, Page
from app.validators.dates import parse_date
from app.validators.evidence import quote_support
from app.validators.financial import amounts_balance, parse_money
from app.validators.identifiers import normalize_dic, normalize_iban, normalize_ico
from app.validators.validation import validate_fact


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1 234,50 €", "1234.50"),
        ("-12,30", "-12.30"),
        ("1234.56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("0,10", "0.10"),
        ("1\u00a0234,50 EUR", "1234.50"),
    ],
)
def test_money(text, expected):
    assert parse_money(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["12 34,50", "1.234", "1 23O,00", "NaN", "1,239", "1e4"])
def test_ambiguous_money_rejected(text):
    with pytest.raises(ValueError):
        parse_money(text)


@pytest.mark.parametrize("text", ["31. 12. 2025", "31.12.2025", "2025-12-31"])
def test_dates(text):
    assert parse_date(text) == "2025-12-31"


@pytest.mark.parametrize("text", ["31.02.2025", "12/01/2025", "01.02.26", "2025-02-29"])
def test_bad_dates(text):
    with pytest.raises(ValueError):
        parse_date(text)


def test_identifiers():
    assert normalize_ico("12 345 679") == "12345679"
    assert normalize_dic("202 1234567") == "2021234567"
    assert normalize_iban("SK31 1200 0000 1987 4263 7541") == "SK3112000000198742637541"
    for bad in ["12345678", "00000000", "1234567"]:
        with pytest.raises(ValueError):
            normalize_ico(bad)
    with pytest.raises(ValueError):
        normalize_iban("SK3212000000198742637541")


def test_arithmetic():
    assert amounts_balance(Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.00"))
    assert not amounts_balance(Decimal("10"), Decimal("2"), Decimal("13"), Decimal("0.02"))


def test_evidence_correct_page_and_diacritics():
    content = Content(
        pages=[Page(page=3, text="Faktúra č.   FA-001"), Page(page=8, text="Iný obsah")]
    )
    assert quote_support(Evidence(page=3, quote="Faktura c. FA-001"), content).matched
    assert not quote_support(Evidence(page=8, quote="Faktúra č. FA-001"), content).matched
    assert not quote_support(Evidence(page=1, quote="Faktúra č. FA-001"), content).matched


@pytest.mark.parametrize("field", ["payment_date", "delivery_date", "issue_date"])
def test_due_date_not_evidence_for_other_dates(field):
    quote = "Dátum splatnosti: 15.10.2026"
    content = Content(pages=[Page(page=1, text=quote)])
    fact = Fact(
        value="2026-10-15", evidence=Evidence(page=1, quote=quote), status="verified", confidence=1
    )
    warnings = []
    assert validate_fact(field, fact, content, warnings).value is None
    assert warnings


def test_arbitrary_value_not_supported_by_real_quote():
    content = Content(pages=[Page(page=1, text="Dodávateľ: Firma A")])
    fact = Fact(
        value="Firma B", evidence=Evidence(page=1, quote="Dodávateľ: Firma A"), confidence=1
    )
    assert validate_fact("supplier_name", fact, content, []).value is None


def test_supplier_customer_role_mismatch_not_verified():
    content = Content(pages=[Page(page=1, text="Odberateľ: Firma A")])
    fact = Fact(
        value="Firma A",
        evidence=Evidence(page=1, quote="Odberateľ: Firma A"),
        status="verified",
        confidence=0.95,
    )
    assert validate_fact("supplier_name", fact, content, []).status == "needs_review"


def test_calculated_and_unproven_values_are_not_direct_evidence():
    content = Content(pages=[Page(page=1, text="Suma spolu: 20,00")])
    fact = Fact(value="20", source_kind="calculated", confidence=0.9)
    assert validate_fact("total_gross", fact, content, []).value is None
    assert Fact(value=None, status="verified", confidence=1).model_dump()["confidence"] == 0


def test_quote_cannot_truncate_number():
    content = Content(pages=[Page(page=1, text="Suma spolu: 123,50 EUR")])
    assert not quote_support(Evidence(page=1, quote="Suma spolu: 12"), content).matched
    assert not quote_support(Evidence(page=1, quote="Suma spolu: 123"), content).matched
    with pytest.raises(ValueError):
        parse_money("123EUR456")


def test_document_word_inside_prose_is_not_classification():
    content = Content(pages=[Page(page=1, text="Toto nie je FAKTÚRA")])
    fact = Fact(
        value="invoice",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="FAKTÚRA"),
    )
    assert validate_fact("classification", fact, content, []).value is None


def test_cell_reference_and_context(load_fixture):
    content = Content.model_validate(load_fixture("structured_excel")["content"])
    evidence = Evidence(
        quote="120.5", locator=CellLocator(sheet="Doklad", cell="B2", context_cells=["A2"])
    )
    fact = Fact(value="120.50", evidence=evidence, status="verified", confidence=0.8)
    assert validate_fact("total_gross", fact, content, []).status == "verified"
    evidence.locator.sheet = "Other"
    assert not quote_support(evidence, content).matched
