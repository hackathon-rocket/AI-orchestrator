import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def analyze(client, fixture, load_fixture):
    response = client.post("/v1/analyze", json=load_fixture(fixture))
    assert response.status_code == 200, response.text
    return response.json()


def test_invoice_end_to_end(client, load_fixture):
    result = analyze(client, "clean_invoice", load_fixture)
    fields = result["extracted_data"]["fields"]
    assert result["document_type"] == "invoice"
    assert fields["invoice_number"]["value"] == "FA-2026-001"
    assert fields["supplier_ico"]["value"] == "12345679"
    assert fields["supplier_ico"]["status"] == "verified"
    assert fields["issue_date"]["value"] == "2026-10-01"
    assert fields["due_date"]["value"] == "2026-10-15"
    assert fields["payment_date"]["value"] is None
    assert len(result["extracted_data"]["line_items"]) == 2
    table = result["tables"][0]
    assert table["tag"] == "rozpocet.zoznamuctovnychdokladov"
    assert len(table["rows"]) == 1
    row = table["rows"][0]
    assert row["typ"]["value"] == "FA"
    assert row["suma_doklad"]["value"] == 1230.0
    assert row["datum"]["value"] == "2026-09-30"
    assert row["datum_1"]["value"] is None
    assert row["suma_dotacia"]["value"] is None
    assert row["druh"]["value"] is None
    assert row["p_c"]["value"] is None
    assert "uctovny_doklad" not in row and "tovar" not in row
    assert row["c_dokladu"]["page"] == 3
    assert row["c_dokladu"]["evidence"] == "FAKTÚRA č. FA-2026-001"
    assert result["requires_review"] is True and "document" not in result
    assert any("úhrady" in w for w in result["warnings"])
    for cell in row.values():
        assert {"tag", "label", "type", "value", "confidence", "evidence", "page"} <= set(cell)
        assert cell["status"] in {"verified", "needs_review", "not_found"}
        assert 0 <= cell["confidence"] <= 0.85
    assert {c["tag"] for c in table["columns"]} == set(row)


def test_missing_due_date(client, load_fixture):
    data = analyze(client, "missing_due_date", load_fixture)["extracted_data"]["fields"]["due_date"]
    assert data["value"] is None and data["status"] == "not_found"
    assert data["confidence"] == 0 and data["evidence"] is None


@pytest.mark.parametrize(
    "fixture,field",
    [
        ("bad_ocr", "total_gross"),
        ("invalid_ico", "supplier_ico"),
        ("inconsistent_totals", "total_gross"),
    ],
)
def test_bad_data_not_verified(client, load_fixture, fixture, field):
    result = analyze(client, fixture, load_fixture)
    assert result["extracted_data"]["fields"][field]["status"] != "verified"
    assert result["warnings"]
    if field == "total_gross":
        assert result["tables"][0]["rows"][0]["suma_doklad"]["value"] is None


@pytest.mark.parametrize(
    "fixture,kind,field,value",
    [
        ("receipt", "receipt", "receipt_number", "B-001"),
        ("contract", "contract", "contract_number", "Z-001"),
        ("bank_statement", "bank_statement", "closing_balance", "200.00"),
    ],
)
def test_document_types(client, load_fixture, fixture, kind, field, value):
    result = analyze(client, fixture, load_fixture)
    assert result["document_type"] == kind
    assert result["extracted_data"]["fields"][field]["value"] == value
    assert result["tables"] == []
    if kind == "bank_statement":
        assert result["extracted_data"]["transactions"][0]["amount"]["value"] == "-100.00"


def test_unknown_document(client, load_fixture):
    result = analyze(client, "unknown", load_fixture)
    assert result["document_type"] == "unknown"
    assert all(f["value"] is None for f in result["extracted_data"]["fields"].values())
    assert result["tables"] == []


def test_injection_cannot_change_policy_or_disclose_secret(client, load_fixture):
    result = analyze(client, "prompt_injection", load_fixture)
    serialized = json.dumps(result)
    assert "synthetic-test-token-only" not in serialized
    assert "999999" not in serialized
    assert result["requires_review"] is True
    assert result["tables"][0]["rows"][0]["suma_dotacia"]["value"] is None
    assert any("pokynom" in w for w in result["warnings"])


def test_conflicting_values_not_mapped(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] += "\nSuma spolu: 900,00 EUR"
    result = client.post("/v1/analyze", json=payload).json()
    assert result["extracted_data"]["fields"]["total_gross"]["status"] == "needs_review"
    assert result["tables"][0]["rows"][0]["suma_doklad"]["value"] is None


def test_alternate_mapping_no_extractor_change(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["form"] = {"id": "alternate_demo", "mapping_id": "alternate_demo"}
    result = client.post("/v1/analyze", json=payload).json()
    assert result["tables"] == []
    fields = {f["tag"]: f["value"] for f in result["fields"]}
    assert fields == {"doklad.referencia": "FA-2026-001", "doklad.celkom": 1230.0}


def test_structured_excel(client, load_fixture):
    result = analyze(client, "structured_excel", load_fixture)
    fact = result["extracted_data"]["fields"]["total_gross"]
    assert fact["value"] == "120.50" and fact["status"] == "needs_review"
    assert "currency_missing" in fact["validation_reasons"]
    assert fact["evidence"]["locator"]["cell"] == "B2"
    assert fact["evidence"]["page"] is None


def test_include_unmapped_false(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["options"]["include_unmapped"] = False
    result = client.post("/v1/analyze", json=payload).json()
    assert result["extracted_data"] is None
    assert result["warnings"] and result["tables"]


def test_boot_endpoints(client):
    for path in ["/health", "/ready", "/version", "/docs", "/openapi.json"]:
        assert client.get(path).status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/v1/analyze"]["post"]["security"] == [{"HTTPBearer": []}]


def test_missing_configuration_fails_closed():
    with TestClient(create_app(Settings(_env_file=None, agent_api_key=""))) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        assert client.post("/v1/analyze", json={}).status_code == 503


@pytest.mark.parametrize(
    "header,code",
    [(None, "auth_missing"), ("Bearer wrong", "auth_invalid"), ("Basic abc", "auth_invalid")],
)
def test_auth(settings, header, code):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/analyze", json={}, headers={"Authorization": header} if header else {}
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"document_id": "x", "content": {"pages": []}},
        {"document_id": "x", "content": {"pages": [{"page": 1, "text": " "}]}},
    ],
)
def test_invalid_request(client, payload):
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_limits_before_provider(settings, load_fixture):
    settings.max_content_chars = 100
    settings.max_request_bytes = 10000
    with TestClient(
        create_app(settings), headers={"Authorization": "Bearer synthetic-test-token-only"}
    ) as client:
        assert client.post("/v1/analyze", json=load_fixture("clean_invoice")).status_code == 413
        assert (
            client.post(
                "/v1/analyze", content="x" * 10001, headers={"Content-Type": "application/json"}
            ).status_code
            == 413
        )


def test_duplicate_pages_and_invalid_sheet_coordinates(client):
    for content in [
        {"pages": [{"page": 1, "text": "x"}, {"page": 1, "text": "y"}]},
        {
            "sheets": [
                {"name": "S", "rows": [{"row": 1, "cells": [{"coordinate": "B2", "value": "x"}]}]}
            ]
        },
    ]:
        assert (
            client.post("/v1/analyze", json={"document_id": "x", "content": content}).status_code
            == 422
        )


def test_unpaginated_ocr(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["page"] = None
    result = client.post("/v1/analyze", json=payload).json()
    assert result["tables"][0]["rows"][0]["c_dokladu"]["page"] is None
    assert any("rozdelenie na strany" in w for w in result["warnings"])


def test_schema_precedence_and_invalid_mapping(client, load_fixture, settings):
    payload = load_fixture("clean_invoice")
    schema = json.loads(
        (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
    )
    schema["children"][0]["children"][0]["config"]["is_readonly"] = 1
    payload["form"]["schema"] = schema
    result = client.post("/v1/analyze", json=payload).json()
    assert result["meta"]["schema_source"] == "request"
    assert result["tables"] == []
    payload["form"]["schema"] = {"type": "root", "children": []}
    assert client.post("/v1/analyze", json=payload).status_code == 422


def test_errors_do_not_echo_input(client):
    response = client.post("/v1/analyze", json={"secret": "do-not-echo-this"})
    assert "do-not-echo-this" not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert response.headers["cache-control"] == "no-store"


def test_foreign_currency_not_silently_mapped_to_eur(client, load_fixture):
    payload = load_fixture("clean_invoice")
    payload["content"]["pages"][0]["text"] = payload["content"]["pages"][0]["text"].replace(
        "EUR", "USD"
    )
    result = client.post("/v1/analyze", json=payload).json()
    assert result["extracted_data"]["fields"]["total_gross"]["value"] == "1230.00"
    assert result["tables"][0]["rows"][0]["suma_doklad"]["value"] is None
    assert any("mena" in w for w in result["warnings"])


def test_deep_json_has_safe_error(client):
    response = client.post(
        "/v1/analyze",
        content='{"x":' * 2000 + "1" + "}" * 2000,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code in {400, 422}
    assert "error" in response.json()
