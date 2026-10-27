"""Mapping outcome is based on validated type and actual emitted suggestions."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.llm.mock_provider import MockProvider, fixture_extract
from app.main import create_app
from app.schemas.responses import AnalyzeResponse
from tests.conftest import TOKEN


def mapping_form(source="total_gross", table=False, required_currency=None):
    kind = "CURRENCY" if source == "total_gross" else "SIMPLE_STRING"
    leaf = {"type": "TABLE_ITEM_CURRENCY" if table else kind, "tag": "hodnota", "name": "Hodnota"}
    children = (
        [{"type": "TABLE_LIST", "tag": "riadky", "config": {"add_rows": 1}, "children": [leaf]}]
        if table
        else [leaf]
    )
    rule = {"source": source, "destination": "doklad.riadky.hodnota" if table else "doklad.hodnota"}
    if required_currency:
        rule["required_currency"] = required_currency
    return {
        "id": "status_test",
        "mapping_id": "status_test_v1",
        "schema": {
            "type": "root",
            "children": [{"type": "SECTION", "tag": "doklad", "children": children}],
        },
        "mapping": {
            "id": "status_test_v1",
            "form_id": "status_test",
            "document_types": ["invoice"],
            "rules": [rule],
        },
    }


def analyze(client, payload, expected):
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    AnalyzeResponse.model_validate(result)
    assert result["mapping_status"] == expected
    assert result["status"] == "completed"
    assert result["requires_review"] is True
    return result


@pytest.mark.parametrize("include_unmapped", [True, False])
def test_bbsk_mapping_status(client, load_fixture, include_unmapped):
    payload = load_fixture("clean_invoice")
    payload["options"]["include_unmapped"] = include_unmapped
    data = analyze(client, payload, "mapped")
    assert data["fields"] == []
    row = data["tables"][0]["rows"][0]
    assert (row["typ"]["value"], row["c_dokladu"]["value"], row["suma_doklad"]["value"]) == (
        "FA",
        "FA-2026-001",
        1230.0,
    )
    assert (data["extracted_data"] is not None) == include_unmapped


@pytest.mark.parametrize("kind", ["clean_invoice", "unknown"])
@pytest.mark.parametrize("explicit_null", [True, False])
def test_no_form_precedes_type(client, load_fixture, kind, explicit_null):
    payload = load_fixture(kind)
    if explicit_null:
        payload["form"] = None
    else:
        payload.pop("form", None)
    result = analyze(client, payload, "no_form")
    assert result["fields"] == result["tables"] == []


@pytest.mark.parametrize("kind", ["receipt", "contract", "unknown"])
def test_validated_type_not_supported(client, load_fixture, kind):
    payload = load_fixture(kind)
    payload["form"] = {"id": "bbsk_11824", "mapping_id": "bbsk_11824_default"}
    result = analyze(client, payload, "unsupported_document_type")
    assert result["document_type"] == kind
    assert result["fields"] == result["tables"] == []


def test_conflicting_classification_uses_validated_unknown_type(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] += "\n\nZMLUVA č. Z-1\nPredmet zmluvy: Nájom"
    result = analyze(client, payload, "unsupported_document_type")
    assert result["document_type"] == "unknown"
    assert result["tables"] == []


@pytest.mark.parametrize("table", [True, False])
@pytest.mark.parametrize("amount", ["0,00", "-12,30", "1.234,50"])
def test_actual_scalar_and_table_values_including_zero(client, load_fixture, table, amount):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form(table=table)
    payload["content"]["pages"][0]["text"] = f"FAKTÚRA č. F-1\nMena: EUR\nSuma spolu: {amount} EUR"
    result = analyze(client, payload, "mapped")
    cell = result["tables"][0]["rows"][0]["hodnota"] if table else result["fields"][0]
    assert cell["value"] is not None and cell["status"] == "verified"
    if amount == "0,00":
        assert cell["value"] == 0


@pytest.mark.parametrize("table", [True, False])
@pytest.mark.parametrize("reason", ["missing", "wrong_currency", "conflict", "invalid_format"])
def test_applicable_mapping_without_safe_values(client, load_fixture, table, reason):
    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form(table=table, required_currency="EUR")
    body = {
        "missing": "Mena: EUR",
        "wrong_currency": "Mena: USD\nSuma spolu: 12,30 USD",
        "conflict": "Mena: EUR\nSuma spolu: 12,30 EUR\nSuma spolu: 99,00 EUR",
        "invalid_format": "Mena: EUR\nSuma spolu: 12 34 EUR",
    }[reason]
    payload["content"]["pages"][0]["text"] = "FAKTÚRA č. F-1\n" + body
    result = analyze(client, payload, "no_values")
    assert result["document_type"] == "invoice"
    if table:
        assert len(result["tables"]) == 1 and result["tables"][0]["rows"] == []
        assert result["tables"][0]["columns"]  # A table/column definition alone is not a value.
    else:
        assert result["fields"] and all(f["value"] is None for f in result["fields"])


def test_protected_targets_produce_no_values(client, settings, load_fixture):
    payload = load_fixture("clean_invoice")
    schema = json.loads(
        (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
    )
    schema["children"][0]["children"][0]["config"]["is_readonly"] = 1
    payload["form"]["schema"] = schema
    result = analyze(client, payload, "no_values")
    assert result["fields"] == result["tables"] == []


def test_safe_review_suggestion_counts_as_mapped(settings, load_fixture):
    class UncertainProvider(MockProvider):
        async def extract(self, content):
            raw = fixture_extract(content)
            raw.fields.expense_description.status = "needs_review"
            return raw

    payload = load_fixture("clean_invoice")
    payload["form"] = mapping_form(source="expense_description")
    with TestClient(
        create_app(settings, UncertainProvider(settings)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        result = analyze(client, payload, "mapped")
    assert result["fields"][0]["status"] == "needs_review"
    assert result["fields"][0]["value"] == "Tlač informačných materiálov"


def test_classification_cell_alone_is_an_actual_suggestion(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] = "FAKTÚRA č. F-1"
    result = analyze(client, payload, "mapped")
    row = result["tables"][0]["rows"][0]
    assert row["typ"]["value"] == "FA" and row["suma_doklad"]["value"] is None


def test_error_remains_an_error_not_a_mapping_status(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["form"]["mapping_id"] = "not_registered"
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 422
    assert set(response.json()) == {"error"}


def test_mapping_status_required_and_closed_enum(client, load_fixture):
    data = analyze(client, load_fixture("clean_invoice"), "mapped")
    for invalid in (None, "completed", "partial", "MAPPED"):
        with pytest.raises(ValidationError):
            AnalyzeResponse.model_validate({**data, "mapping_status": invalid})
    data.pop("mapping_status")
    with pytest.raises(ValidationError):
        AnalyzeResponse.model_validate(data)
