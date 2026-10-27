"""Shared Laravel OCR examples exercise the actual HTTP/validation/mapping path."""

import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.llm.mock_provider import MockProvider, fixture_extract
from app.main import create_app
from app.schemas.requests import AnalyzeRequest
from app.schemas.responses import AnalyzeResponse
from tests.conftest import TOKEN

ROOT = Path(__file__).parents[1] / "fixtures/laravel_ocr/v1"


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


VALID = read("cases.json")["cases"]
INVALID = read("invalid_cases.json")["cases"]


class ObservedMockProvider(MockProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.inputs = []

    async def attempt(self, content):
        self.inputs.append(content.model_copy(deep=True))
        return await super().attempt(content)


@pytest.fixture
def observed_api(settings):
    provider = ObservedMockProvider(settings)
    with TestClient(
        create_app(settings, provider), headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        yield client, provider


@pytest.mark.parametrize("case", VALID, ids=[case["name"] for case in VALID])
def test_shared_normalized_requests_reach_provider_unchanged(observed_api, case):
    client, provider = observed_api
    payload = read(case["request"])
    original = copy.deepcopy(payload)
    expected = case["expected"]
    request = AnalyzeRequest.model_validate(payload)
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200, response.text
    assert payload == original
    body = response.json()
    AnalyzeResponse.model_validate(body)
    assert len(provider.inputs) == 1
    sent = provider.inputs[0]
    assert sent.language == expected["language"]
    assert sent.model_dump() == request.content.model_dump()
    assert [(page.page, page.text) for page in sent.pages] == [
        (page.get("page"), page["text"]) for page in payload["content"]["pages"]
    ]
    assert [sheet.model_dump(exclude_defaults=True) for sheet in sent.sheets] == [
        sheet.model_dump(exclude_defaults=True) for sheet in request.content.sheets
    ]
    assert body["document_type"] == expected["document_type"]
    assert body["mapping_status"] == expected["mapping_status"]
    assert body["status"] == "completed" and body["requires_review"] is True
    gross = body["extracted_data"]["fields"]["total_gross"]
    assert gross["value"] == expected["total_gross"]
    if expected["document_type"] == "invoice":
        assert gross["status"] == "verified"
        assert gross["evidence"]["page"] == expected["amount_page"]
        assert gross["evidence"]["locator"] == expected["amount_locator"]
        row = body["tables"][0]["rows"][0]
        assert row["typ"]["value"] == "FA"
        assert row["suma_doklad"]["value"] == float(expected["total_gross"])
        assert row["suma_doklad"]["page"] == expected["amount_page"]
        assert row["suma_doklad"]["source_locator"] == expected["amount_locator"]
        if expected["amount_locator"] is None:
            assert gross["evidence"]["quote"] == "Suma spolu: 1 230,00 EUR"
        if case["name"] == "numbered_pages":
            assert body["extracted_data"]["classification"]["evidence"]["page"] == 3
            assert row["datum"]["page"] == 3
            assert [
                item["gross"]["evidence"]["page"] for item in body["extracted_data"]["line_items"]
            ] == [11, 11]
        if case["name"] in {"combined_text", "sheet_only"}:
            assert all(cell["page"] is None for cell in row.values())
        if case["name"] == "combined_text":
            assert any("rozdelenie na strany" in warning for warning in body["warnings"])
    else:
        assert body["fields"] == body["tables"] == []
        assert all(
            fact["status"] == "not_found" for fact in body["extracted_data"]["fields"].values()
        )


def assert_safe_rejection(response, provider, status, code):
    assert response.status_code == status
    data = response.json()
    assert set(data) == {"error"}
    assert set(data["error"]) == {"code", "message", "request_id"}
    assert data["error"]["code"] == code
    assert str(UUID(response.headers["x-request-id"])) == data["error"]["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert not provider.inputs  # Invalid OCR never reaches extraction.
    for secret in (TOKEN, "FA-2026-001", "Ukážkový", "synthetic-private-ocr"):
        assert secret not in response.text


@pytest.mark.parametrize("case", INVALID, ids=[case["name"] for case in INVALID])
def test_shared_invalid_requests_fail_before_provider(observed_api, case):
    client, provider = observed_api
    response = client.post("/v1/analyze", json=case["request"])
    assert_safe_rejection(
        response, provider, case["expected_http_status"], case["expected_error_code"]
    )


def test_known_laravel_text_storage_matches_unpaginated_request():
    exported = read("stored_extraction.synthetic.json")
    request = read("combined_text.request.json")
    record = exported["record"]
    assert set(record) == {"engine", "language", "filename", "source", "text", "raw"}
    assert record["raw"] is None  # Explicit redaction, not an invented provider raw schema.
    assert request["content"]["pages"] == [{"page": None, "text": record["text"]}]
    assert request["content"]["language"] == record["language"]


@pytest.mark.parametrize(
    "language,expected",
    [(" SLK + ENG ", "sk+en"), ("slk+eng+slk", "sk+en"), ("ces+eng", "cs+en"), ("sk+en", "sk+en")],
)
def test_language_hint_normalization_is_visible_to_provider(observed_api, language, expected):
    client, provider = observed_api
    payload = read("combined_text.request.json")
    payload["content"]["language"] = language
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert provider.inputs[0].language == expected
    assert provider.inputs[0].pages[0].text == payload["content"]["pages"][0]["text"]


def test_absent_page_id_stays_unpaginated(observed_api):
    client, provider = observed_api
    payload = read("combined_text.request.json")
    payload["content"]["pages"][0].pop("page")
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert provider.inputs[0].pages[0].page is None
    assert response.json()["tables"][0]["rows"][0]["c_dokladu"]["page"] is None


def test_source_order_is_not_renumbered_or_sorted(observed_api):
    client, provider = observed_api
    payload = read("numbered_pages.request.json")
    payload["content"]["pages"].reverse()
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert [page.page for page in provider.inputs[0].pages] == [11, 7, 3]
    row = response.json()["tables"][0]["rows"][0]
    assert row["c_dokladu"]["page"] == 3 and row["suma_doklad"]["page"] == 7


def test_wrong_page_cannot_borrow_evidence(settings):
    class WrongPageProvider(MockProvider):
        async def attempt(self, content):
            raw = fixture_extract(content)
            raw.fields.total_gross.evidence.page = 3  # Actual amount is on page 7.
            return raw

    with TestClient(
        create_app(settings, WrongPageProvider(settings)),
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        response = client.post("/v1/analyze", json=read("numbered_pages.request.json"))
    assert response.status_code == 200
    body = response.json()
    assert body["extracted_data"]["fields"]["total_gross"]["status"] == "not_found"
    assert body["tables"][0]["rows"][0]["suma_doklad"]["value"] is None


def bounded_text(size):
    heading = "FAKTÚRA č. LENGTH-1\n"
    tail = "\nMena: EUR\nSuma spolu: 123,00 EUR"
    length = size - len(heading) - len(tail)
    # Realistic OCR lines also respect the existing 4000-character Evidence ceiling.
    padding = (("x" * 999 + "\n") * (length // 1000 + 1))[:length]
    return heading + padding + tail


def test_exact_page_ceiling_preserves_final_evidence(observed_api, settings):
    client, provider = observed_api
    settings.max_content_chars = 200000
    payload = read("combined_text.request.json")
    text = bounded_text(200000)
    payload["content"]["pages"][0]["text"] = text
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert provider.inputs[0].pages[0].text == text
    gross = response.json()["extracted_data"]["fields"]["total_gross"]
    assert gross["value"] == "123.00" and gross["status"] == "verified"
    assert gross["evidence"]["quote"] == text.splitlines()[-1]


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("page_length", 422, "invalid_request"),
        ("page_count", 422, "invalid_request"),
        ("sheet_count", 422, "invalid_request"),
        ("total_characters", 413, "content_too_large"),
        ("configured_pages", 413, "content_too_large"),
        ("configured_sheets", 413, "content_too_large"),
        ("body_bytes", 413, "request_too_large"),
    ],
)
def test_limits_reject_instead_of_truncating(observed_api, kind, status, code):
    client, provider = observed_api
    payload = read("combined_text.request.json")
    if kind == "page_length":
        payload["content"]["pages"][0]["text"] = bounded_text(200001)
    elif kind in {"page_count", "configured_pages"}:
        count = 101 if kind == "page_count" else 51
        payload["content"]["pages"] = [
            {"page": n + 1, "text": "synthetic-private-ocr"} for n in range(count)
        ]
    elif kind in {"sheet_count", "configured_sheets"}:
        count = 21 if kind == "sheet_count" else 11
        payload["content"]["pages"] = []
        payload["content"]["sheets"] = [
            {
                "name": f"S{n}",
                "rows": [
                    {"row": 1, "cells": [{"coordinate": "A1", "value": "synthetic-private-ocr"}]}
                ],
            }
            for n in range(count)
        ]
    elif kind == "total_characters":
        payload["content"]["pages"] = [
            {"page": 1, "text": "x" * 80000},
            {"page": 2, "text": "x" * 80000},
        ]
    if kind == "body_bytes":
        response = client.post(
            "/v1/analyze", content=b"x" * 2000001, headers={"Content-Type": "application/json"}
        )
    else:
        response = client.post("/v1/analyze", json=payload)
    assert_safe_rejection(response, provider, status, code)
