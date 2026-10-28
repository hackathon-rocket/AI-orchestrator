"""Mapped exact money originates from Decimal; legacy JSON numbers stay compatible."""

import json
import math
from decimal import Decimal

import pytest

from app.mapping.form_parser import FormField
from app.mapping.mapper import ui_field
from app.schemas.extraction import Fact
from app.schemas.responses import ExtractedField
from app.validators.financial import parse_money
from tests.integration.test_mapping_status import mapping_form
from tests.unit.test_money_formats import validate_and_map

AMOUNTS = [
    "0.10",
    "0.20",
    "0.30",
    "1234.56",
    "999999999999.99",
    "0.00",
    "-125.50",
    "-0.00",
    "1000000000000.00",
]


def cell_from(data, table):
    return data["tables"][0]["rows"][0]["hodnota"] if table else data["fields"][0]


@pytest.mark.parametrize("amount", AMOUNTS)
@pytest.mark.parametrize("table", [False, True])
def test_exact_currency_survives_fastapi_wire(client, load_fixture, amount, table):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form(table=table, required_currency="EUR")
    payload["content"]["pages"][0]["text"] = f"FAKTÚRA č. F-1\nMena: EUR\nSuma spolu: {amount} EUR"
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    cell = cell_from(body, table)
    assert type(cell["value"]) is float
    assert cell["value"] == float(
        Decimal(amount)
    )  # The legacy conversion is intentionally retained.
    assert cell["decimal_value"] == amount  # Exact expectation is never derived from that float.
    if amount == "-0.00":
        assert math.copysign(1, cell["value"]) == -1
    assert cell["status"] == "verified"
    assert cell["evidence"] == f"Suma spolu: {amount} EUR" and cell["page"] == 3
    assert body["mapping_status"] == "mapped" and body["requires_review"] is True
    fact = body["extracted_data"]["fields"]["total_gross"]
    assert fact["value"] == amount and "decimal_value" not in fact
    # Check the actual JSON token types, not only a constructed Pydantic model.
    decimal_json = json.loads(response.content, parse_float=Decimal)
    assert isinstance(cell_from(decimal_json, table)["value"], Decimal)
    assert isinstance(cell_from(decimal_json, table)["decimal_value"], str)


@pytest.mark.parametrize("include_unmapped", [True, False])
def test_bbsk_invoice_exact_amount_and_non_currency_cells(client, load_fixture, include_unmapped):
    payload = load_fixture("clean_invoice")
    payload["options"]["include_unmapped"] = include_unmapped
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["document_type"] == "invoice" and body["mapping_status"] == "mapped"
    row = body["tables"][0]["rows"][0]
    amount = row["suma_doklad"]
    assert amount["value"] == 1230.0 and amount["decimal_value"] == "1230.00"
    assert amount["status"] == "verified" and amount["page"] == 3
    assert amount["evidence"] == "Suma spolu: 1 230,00 EUR" and amount["source_locator"] is None
    assert row["c_dokladu"]["value"] == "FA-2026-001" and row["typ"]["value"] == "FA"
    for cell in row.values():
        if cell["type"] != "currency" or cell["value"] is None:
            assert cell["decimal_value"] is None
    assert (body["extracted_data"] is None) == (not include_unmapped)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("string", "FA-1"),
        ("date", "2026-10-01"),
        ("ico", "12345679"),
        ("dic", "2021234567"),
        ("iban", "SK3112000000198742637541"),
        ("number", Decimal("0.10")),
    ],
)
def test_non_currency_ui_types_never_gain_decimal_value(kind, value):
    target = FormField(
        path="field",
        tag="field",
        label="Field",
        frontend_type=kind,
        table=None,
        editable=True,
        required=False,
    )
    result = ui_field(target, Fact(value=value, status="verified"))
    assert result.decimal_value is None
    assert json.loads(result.model_dump_json())["decimal_value"] is None


def test_missing_currency_is_not_zero():
    target = FormField(
        path="field",
        tag="field",
        label="Field",
        frontend_type="currency",
        table=None,
        editable=True,
        required=False,
    )
    field = ui_field(target, Fact())
    assert field.value is None and field.decimal_value is None and field.status == "not_found"


@pytest.mark.parametrize(
    "amount,candidate,label,extra",
    [
        ("123.45", "999", "Suma spolu", ""),
        ("123.45", "123.45", "Zmluvná suma", ""),
        ("0.00", "0.00", "Zmluvná suma", ""),
        ("123.45", "123.45", "Suma spolu", "\nSuma spolu: 999,00 EUR"),
        ("1.234,500", "1234.50", "Suma spolu", ""),
        ("1000000000000.01", "1000000000000.01", "Suma spolu", ""),
        ("NaN", "NaN", "Suma spolu", ""),
        ("Infinity", "Infinity", "Suma spolu", ""),
        ("1235.50", "1235.50", "Suma spolu", "\nZáklad DPH: 1.000,00 EUR\nDPH spolu: 234,50 EUR"),
    ],
)
def test_rejected_candidate_cannot_leak_through_exact_field(
    settings, amount, candidate, label, extra
):
    _, table = validate_and_map(settings, amount, candidate, label, extra)
    cell = json.loads(table.model_dump_json())["rows"][0]["suma_doklad"]
    assert cell["value"] is None and cell["decimal_value"] is None
    assert cell["status"] == "not_found"


def test_currency_mismatch_stays_null_on_wire(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] = payload["content"]["pages"][0]["text"].replace(
        "EUR", "USD"
    )
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["extracted_data"]["fields"]["total_gross"]["value"] == "1230.00"
    assert body["tables"][0]["rows"][0]["suma_doklad"]["value"] is None
    assert body["tables"][0]["rows"][0]["suma_doklad"]["decimal_value"] is None


@pytest.mark.parametrize("kind", ["receipt", "unknown", "no_form"])
def test_unmapped_documents_have_no_invented_exact_values(client, load_fixture, kind):
    payload = load_fixture("clean_invoice" if kind == "no_form" else kind)
    if kind == "no_form":
        payload["form"] = None
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["fields"] == body["tables"] == []
    assert body["mapping_status"] == (
        "no_form" if kind == "no_form" else "unsupported_document_type"
    )


def test_exact_http_values_support_decimal_arithmetic(client, load_fixture):
    values = []
    for amount in ["0.10", "0.20", "0.30"]:
        payload = load_fixture("clean_invoice")
        payload["form"] = mapping_form(required_currency="EUR")
        payload["content"]["pages"][0]["text"] = (
            f"FAKTÚRA č. F-1\nMena: EUR\nSuma spolu: {amount} EUR"
        )
        response = client.post("/v1/analyze", json=payload)
        assert response.status_code == 200
        values.append(Decimal(response.json()["fields"][0]["decimal_value"]))
    assert values[0] + values[1] == values[2] == Decimal("0.30")


def test_openapi_exact_field_addition_only(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    properties = schemas["ExtractedField"]["properties"]
    assert {item["type"] for item in properties["decimal_value"]["anyOf"]} == {"string", "null"}
    assert {item["type"] for item in properties["value"]["anyOf"]} == {"string", "number", "null"}
    assert schemas["ExtractedTable"]["properties"]["rows"]["items"]["additionalProperties"][
        "$ref"
    ].endswith("/ExtractedField")
    assert "decimal_value" not in schemas["Fact"]["properties"]
    assert ExtractedField.model_fields["decimal_value"].default is None


def test_exact_decimal_serializes_without_exponent_or_rounding():
    target = FormField(
        path="field",
        tag="field",
        label="Field",
        frontend_type="currency",
        table=None,
        editable=True,
        required=False,
    )
    normalized = parse_money(Decimal("1E+3"))
    field = ui_field(target, Fact(value=normalized, status="verified"))
    assert field.decimal_value == "1000.00"
    assert json.loads(field.model_dump_json())["decimal_value"] == "1000.00"
    assert json.loads(Fact(value=Decimal("0.10")).model_dump_json())["value"] == "0.10"


@pytest.mark.parametrize("amount", ["1.234,56", "1,234.56"])
def test_grouped_formats_keep_exact_mapped_value(client, load_fixture, amount):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] = f"FAKTÚRA č. F-1\nMena: EUR\nSuma spolu: {amount} EUR"
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    cell = response.json()["tables"][0]["rows"][0]["suma_doklad"]
    assert cell["value"] == 1234.56 and cell["decimal_value"] == "1234.56"


@pytest.mark.parametrize("source", ["missing", "malformed", "over_limit", "conflict"])
def test_unsafe_currency_on_http_wire_has_no_exact_value(client, load_fixture, source):
    payload = load_fixture("clean_invoice")
    body = {
        "missing": "Mena: EUR",
        "malformed": "Mena: EUR\nSuma spolu: 1.234,500 EUR",
        "over_limit": "Mena: EUR\nSuma spolu: 1000000000000.01 EUR",
        "conflict": "Mena: EUR\nSuma spolu: 123,45 EUR\nSuma spolu: 999,00 EUR",
    }[source]
    payload["content"]["pages"][0]["text"] = "FAKTÚRA č. F-1\n" + body
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    cell = response.json()["tables"][0]["rows"][0]["suma_doklad"]
    assert cell["value"] is None and cell["decimal_value"] is None
    assert cell["status"] == "not_found"
