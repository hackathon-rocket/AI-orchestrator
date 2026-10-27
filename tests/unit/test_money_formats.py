"""Money formats, complete evidence tokens and trusted BBSK amounts."""

from decimal import Decimal

import pytest

from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import Evidence, ExtractionData, Fact
from app.schemas.requests import Content, FormContext, Page
from app.validators.evidence import equivalent, literal_support
from app.validators.financial import parse_money
from app.validators.validation import validate_extraction

VALID = [
    ("1234", "1234.00"),
    ("1234,50", "1234.50"),
    ("1234.50", "1234.50"),
    ("1 234,50", "1234.50"),
    ("1 234.50", "1234.50"),
    ("1.234,50", "1234.50"),
    ("1,234.50", "1234.50"),
    ("1.234.567,89", "1234567.89"),
    ("1,234,567.89", "1234567.89"),
    ("1 234 567,89", "1234567.89"),
    ("1\u00a0234,50", "1234.50"),
    ("1\u202f234,50", "1234.50"),
    ("+1.234,50", "1234.50"),
    ("-1,234.50", "-1234.50"),
    ("0,1", "0.10"),
    ("1.234,5", "1234.50"),
    ("1 234", "1234.00"),
    ("1.000.000.000.000,00", "1000000000000.00"),
    ("-1,000,000,000,000.00", "-1000000000000.00"),
]
INVALID = [
    "1.234",
    "1,234",
    "12 34",
    "1.23.45",
    "1,23,45",
    "1.234,500",
    "1,234.500",
    "NaN",
    "Infinity",
    "-Infinity",
    "1e4",
    "12.34,50",
    "12,34.50",
    "1.234.56,78",
    "1,234,56.78",
    "1 234.567,89",
    "1.234 567,89",
    "1,,234.50",
    "1234,567.89",
    "1.234,",
    ",123",
    "--123",
    "1,000,000,000,000.01",
    "-1.000.000.000.000,01",
    "1.234.567",
    "1,234,567",
]


@pytest.mark.parametrize("text,expected", VALID)
def test_valid_money_and_literal_token(text, expected):
    result = parse_money(text)
    assert result == Decimal(expected)
    assert result.as_tuple().exponent == -2
    assert literal_support("total_gross", expected, f"Suma spolu: {text} EUR")
    assert equivalent("total_gross", expected, text)


@pytest.mark.parametrize("text", INVALID)
def test_invalid_money(text):
    with pytest.raises(ValueError):
        parse_money(text)


@pytest.mark.parametrize("suffix", ["EUR", "€", "CZK", "USD", "GBP", "CHF", "PLN", "HUF", "eur"])
@pytest.mark.parametrize("number", ["1.234,50", "1,234.50"])
def test_supported_currency_suffixes(number, suffix):
    assert parse_money(f"{number} {suffix}") == Decimal("1234.50")
    assert literal_support("total_gross", "1234.50", f"Suma spolu: {number}{suffix}")


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("Infinity"), Decimal("1.001"), Decimal("1000000000000.01")]
)
def test_decimal_restrictions(value):
    with pytest.raises(ValueError):
        parse_money(value)


def test_decimal_precision():
    assert parse_money(Decimal("0.10")) + parse_money(Decimal("0.20")) == Decimal("0.30")


@pytest.mark.parametrize(
    "text,value",
    [
        ("1,234.50", "234.50"),
        ("1.234,50", "234.50"),
        ("1 234,50", "234.50"),
        ("1\u202f234,50", "234.50"),
        ("1.234,500", "1234.50"),
        ("12 34", "34"),
        ("1.23.45", "45"),
        ("1,23,45", "45"),
        ("1,234.500", "234.50"),
        ("1234.50X", "1234.50"),
        ("AB1234.50", "1234.50"),
        ("--1234.50", "1234.50"),
        ("1234.50/99", "1234.50"),
        ("1 23O,00", "1"),
    ],
)
def test_no_partial_numeric_evidence(text, value):
    assert not literal_support("total_gross", value, f"Suma spolu: {text}")


def fact(value, quote):
    return Fact(
        value=value, status="verified", confidence=0.9, evidence=Evidence(page=1, quote=quote)
    )


def validate_and_map(settings, amount, candidate="1234.50", label="Suma spolu", extra=""):
    heading = "FAKTÚRA č. F-2026-1"
    quote = f"{label}: {amount} EUR"
    text = f"{heading}\nMena dokladu: EUR\n{quote}{extra}"
    raw = ExtractionData(document_type="invoice", classification=fact("invoice", heading))
    raw.fields.total_gross = fact(candidate, quote)
    raw.fields.currency = fact("EUR", "Mena dokladu: EUR")
    if "Základ DPH:" in extra:
        raw.fields.total_net = fact("1000", "Základ DPH: 1.000,00 EUR")
        raw.fields.total_vat = fact("234.50", "DPH spolu: 234,50 EUR")
    data, _ = validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)
    config, form, _ = ConfigLoader(settings.form_config_dir, FormSchemaParser()).resolve(
        FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    )
    _, tables, _ = map_extraction(data, config, form)
    return data, tables[0]


@pytest.mark.parametrize("amount", ["1.234,50", "1,234.50"])
def test_real_bbsk_money_mapping(settings, amount):
    data, table = validate_and_map(settings, amount)
    assert data.document_type == "invoice" and data.classification.status == "verified"
    assert data.fields.total_gross.value == Decimal("1234.50")
    assert data.fields.total_gross.status == "verified" and data.fields.total_gross._safe_to_suggest
    assert table.tag == "rozpocet.zoznamuctovnychdokladov"
    assert table.rows[0]["typ"].value == "FA"
    assert table.rows[0]["suma_doklad"].value == 1234.50


@pytest.mark.parametrize(
    "amount,candidate,label,extra",
    [
        ("1.234,50", "999", "Suma spolu", ""),
        ("1.234,50", "1234.50", "Zmluvná suma", ""),
        ("1.234,500", "1234.50", "Suma spolu", ""),
        ("1,234", "1234", "Suma spolu", ""),
        ("1.234,50", "1234.50", "Suma spolu", "\nSuma spolu: 9,999.00 EUR"),
        ("1.235,50", "1235.50", "Suma spolu", "\nZáklad DPH: 1.000,00 EUR\nDPH spolu: 234,50 EUR"),
    ],
)
def test_unsafe_money_not_mapped(settings, amount, candidate, label, extra):
    data, table = validate_and_map(settings, amount, candidate, label, extra)
    assert data.fields.total_gross.status != "verified"
    assert not data.fields.total_gross._safe_to_suggest
    assert table.rows[0]["suma_doklad"].value is None
    if "9,999" in extra:
        assert "conflicting_values" in data.fields.total_gross.validation_reasons
    if "Základ DPH" in extra:
        assert "arithmetic_conflict" in data.fields.total_gross.validation_reasons


def test_consistent_vat_and_equivalent_total_formats(settings):
    data, table = validate_and_map(
        settings,
        "1.234,50",
        extra="\nSuma spolu: 1,234.50 EUR\nZáklad DPH: 1.000,00 EUR\nDPH spolu: 234,50 EUR",
    )
    assert data.fields.total_gross.status == "verified"
    assert table.rows[0]["suma_doklad"].value == 1234.50


@pytest.mark.parametrize("delimiter", ["  ", "    ", "\t", " | "])
@pytest.mark.parametrize(
    "first,second,value",
    [
        ("1234.50", "2000.00", "1234.50"),
        ("1.234,50", "2.000,00", "1234.50"),
        ("-1,234.50 EUR", "2,000.00 EUR", "-1234.50"),
        ("1 234,50", "2 000,00", "1234.50"),
        ("1\u00a0234,50", "2\u202f000,00", "1234.50"),
    ],
)
def test_structurally_separated_money_tokens(delimiter, first, second, value):
    quote = f"Suma spolu: {first}{delimiter}{second}"
    assert literal_support("total_gross", value, quote)
    assert literal_support("total_gross", "2000", quote)


@pytest.mark.parametrize("delimiter", ["  ", "\t", " | "])
@pytest.mark.parametrize("first,second", [("1234.50", "2000.00"), ("1.234,50", "2.000,00")])
def test_first_amount_has_role_and_reaches_bbsk(settings, delimiter, first, second):
    data, table = validate_and_map(settings, first + delimiter + second)
    assert data.fields.total_gross.value == Decimal("1234.50")
    assert data.fields.total_gross.status == "verified"
    assert data.fields.total_gross._safe_to_suggest
    assert table.rows[0]["suma_doklad"].value == 1234.5
    # Literal presence of the second column is not semantic support for a total.
    other, other_table = validate_and_map(settings, first + delimiter + second, candidate="2000")
    assert other.fields.total_gross.status != "verified"
    assert not other.fields.total_gross._safe_to_suggest
    assert other_table.rows[0]["suma_doklad"].value is None


@pytest.mark.parametrize(
    "amount,value",
    [
        ("1,234.50", "234.50"),
        ("1.234,50", "234.50"),
        ("1 234,50", "234.50"),
        ("12 34", "34"),
        ("1.234,500", "1234.50"),
        ("1,234.500", "1234.50"),
        ("1234.50XYZ", "1234.50"),
        ("1234.50 2000.00", "1234.50"),
    ],
)
def test_delimiters_do_not_split_malformed_or_ambiguous_tokens(amount, value):
    assert not literal_support("total_gross", value, f"Suma spolu: {amount}  9000.00")


def test_two_labelled_totals_remain_reviewable(settings):
    data, table = validate_and_map(settings, "1.234,50  Suma spolu: 2.000,00")
    assert data.fields.total_gross.status == "needs_review"
    assert "conflicting_values" in data.fields.total_gross.validation_reasons
    assert not data.fields.total_gross._safe_to_suggest
    assert table.rows[0]["suma_doklad"].value is None
