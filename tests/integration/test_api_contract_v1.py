"""API Contract v1.0: fixed goldens plus HTTP/schema compatibility assertions.

Goldens are read-only. Only request UUIDs and measured durations are normalized.
"""

import asyncio
import copy
import io
import json
import math
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UserError

from app.llm.base import BoundedProvider
from app.llm.mock_provider import MockProvider
from app.main import create_app
from app.schemas.requests import AnalyzeRequest
from app.schemas.responses import AnalyzeResponse, ErrorResponse, PrepareXlsxResponse
from tests.conftest import TOKEN

FIXTURES = Path(__file__).parents[1] / "fixtures/contracts/v1"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
SENTINEL = "synthetic-private-ocr-and-provider-content"
CASES = [
    "invoice",
    "unsupported_receipt",
    "unknown",
    "no_form",
    "mapped_only",
    "structured_excel",
    "unpaginated",
    "no_values",
]


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_headers(response):
    request_id = response.headers["x-request-id"]
    assert str(UUID(request_id)) == request_id
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    return request_id


def assert_error(response, status, code):
    assert response.status_code == status
    request_id = assert_headers(response)
    body = response.json()
    ErrorResponse.model_validate(body)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"] == request_id
    assert body["error"]["message"]
    for private in (TOKEN, SENTINEL, "FA-2026-001", "Ukážkový", "synthetic-invalid-token"):
        assert private not in response.text
    return body


def normalized_analysis(response):
    request_id = assert_headers(response)
    body = response.json()
    AnalyzeResponse.model_validate(body)
    assert body["meta"]["request_id"] == request_id
    duration = body["meta"]["duration_ms"]
    assert type(duration) in (int, float) and math.isfinite(duration) and duration >= 0
    assert body["requires_review"] is True
    result = copy.deepcopy(body)
    result["meta"]["request_id"] = "$dynamic.request_id"
    result["meta"]["duration_ms"] = 0
    return result


@pytest.mark.parametrize("case", CASES)
def test_analyze_response_golden(client, case):
    payload = fixture(f"{case}.request.json")
    AnalyzeRequest.model_validate(payload)
    response = client.post("/v1/analyze", json=payload)
    assert response.status_code == 200
    assert normalized_analysis(response) == fixture(f"{case}.response.json")
    if case == "invoice":
        body = response.json()
        assert body["fields"] == []
        row = body["tables"][0]["rows"][0]
        assert (row["typ"]["value"], row["c_dokladu"]["value"], row["suma_doklad"]["value"]) == (
            "FA",
            "FA-2026-001",
            1230.0,
        )
        assert row["c_dokladu"]["page"] == 3
        assert row["suma_dotacia"]["value"] is None


@pytest.mark.parametrize("form_behavior", ["omitted", "null"])
def test_no_form_and_default_options(client, form_behavior):
    payload = fixture("no_form.request.json")
    payload.pop("options")
    if form_behavior == "null":
        payload["form"] = None
    response = client.post("/v1/analyze", json=payload)
    assert normalized_analysis(response) == fixture("no_form.response.json")


def test_request_schema_precedence(client, settings):
    payload = fixture("invoice.request.json")
    payload["form"]["schema"] = json.loads(
        (settings.form_config_dir / "bbsk_11824_schema.json").read_text(encoding="utf-8")
    )
    response = client.post("/v1/analyze", json=payload)
    expected = fixture("invoice.response.json")
    expected["meta"]["schema_source"] = "request"
    assert normalized_analysis(response) == expected


INVALID = [
    {"content": {"pages": [{"page": 1, "text": "x"}]}},
    {"document_id": "bad/id", "content": {"pages": [{"page": 1, "text": "x"}]}},
    {"document_id": "x", "content": {}},
    *[
        {"document_id": "x", "content": {"pages": [{"page": n, "text": "x"}]}}
        for n in (0, -1, 100001)
    ],
    {
        "document_id": "x",
        "content": {"pages": [{"page": 3, "text": "x"}, {"page": 3, "text": "y"}]},
    },
    {
        "document_id": "x",
        "content": {"pages": [{"page": None, "text": "x"}, {"page": 3, "text": "y"}]},
    },
    {
        "document_id": "x",
        "content": {
            "sheets": [
                {"name": "S", "rows": [{"row": 1, "cells": [{"coordinate": "a1", "value": "x"}]}]}
            ]
        },
    },
    {
        "document_id": "x",
        "content": {
            "sheets": [
                {"name": "S", "rows": [{"row": 1, "cells": [{"coordinate": "B2", "value": "x"}]}]}
            ]
        },
    },
    {"document_id": "x", "content": {"pages": [{"page": 1, "text": "x"}]}, "extra": SENTINEL},
    {"document_id": "x", "content": {"pages": [{"page": 1, "text": "x"}]}, "options": None},
]


@pytest.mark.parametrize("payload", INVALID)
def test_invalid_request_contract(client, payload):
    assert_error(client.post("/v1/analyze", json=payload), 422, "invalid_request")


@pytest.mark.parametrize(
    "case",
    [
        "auth_missing",
        "auth_invalid",
        "invalid_request",
        "unsupported_media_type",
        "unknown_mapping",
    ],
)
def test_error_goldens(client, case):
    kwargs = {
        "auth_missing": {"headers": {"Authorization": ""}, "json": {}},
        "auth_invalid": {
            "headers": {"Authorization": "Bearer synthetic-invalid-token"},
            "json": {},
        },
        "invalid_request": {"json": {"document_id": "synthetic-invalid", "content": {}}},
        "unsupported_media_type": {"content": SENTINEL, "headers": {"Content-Type": "text/plain"}},
        "unknown_mapping": {
            "json": {
                **fixture("invoice.request.json"),
                "form": {"id": "unregistered", "mapping_id": "unregistered"},
            }
        },
    }[case]
    response = client.post("/v1/analyze", **kwargs)
    expected = fixture("errors.json")[case]
    body = assert_error(response, expected["status"], case)
    body["error"]["request_id"] = "$dynamic.request_id"
    assert body == expected["body"]
    if expected["status"] == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("supplied", [str(uuid4()), "invalid-uuid"])
def test_request_id_success_and_error(client, supplied):
    for payload in (fixture("invoice.request.json"), {"secret": SENTINEL}):
        response = client.post("/v1/analyze", json=payload, headers={"X-Request-ID": supplied})
        request_id = assert_headers(response)
        assert (request_id == supplied) == (supplied != "invalid-uuid")
        body = response.json()
        assert request_id == (
            body["meta"]["request_id"]
            if response.status_code == 200
            else body["error"]["request_id"]
        )


@pytest.mark.parametrize(
    "failure,status,code",
    [
        (ModelHTTPError(503, "synthetic", {"error": SENTINEL}), 503, "provider_unavailable"),
        (ModelHTTPError(429, "synthetic", {"error": SENTINEL}), 503, "provider_rate_limited"),
        (ModelHTTPError(401, "synthetic", {"error": SENTINEL}), 502, "provider_auth_error"),
        (UnexpectedModelBehavior(SENTINEL), 502, "provider_invalid_output"),
        (UserError(SENTINEL), 503, "provider_configuration_error"),
        (TimeoutError(SENTINEL), 504, "provider_timeout"),
        (RuntimeError(SENTINEL), 500, "internal_error"),
    ],
)
def test_provider_http_errors_are_safe(settings, failure, status, code):
    class FailingProvider(BoundedProvider):
        async def attempt(self, content):
            raise failure

    settings.llm_max_retries = 0
    payload = fixture("invoice.request.json")
    payload["content"]["pages"][0]["text"] += "\n" + SENTINEL
    with TestClient(create_app(settings, FailingProvider(settings)), headers=AUTH) as client:
        assert_error(client.post("/v1/analyze", json=payload), status, code)


@pytest.mark.parametrize(
    "mode", ["missing_token", "provider_settings", "mapping_config", "provider_initialization"]
)
def test_readiness_failure_contract(settings, tmp_path, monkeypatch, mode):
    expected = []
    if mode == "missing_token":
        settings.agent_api_key = ""
        expected = ["service_token_not_configured"]
    elif mode == "provider_settings":
        settings.llm_provider = "openai"
        expected = [
            "model_not_configured",
            "remote_llm_not_authorized",
            "provider_key_not_configured",
        ]
    elif mode == "mapping_config":
        settings.form_config_dir = tmp_path
        expected = ["invalid_mapping_configuration"]
    else:

        def fail(_settings):
            raise RuntimeError(SENTINEL)

        monkeypatch.setattr("app.main.create_provider", fail)
        expected = ["provider_configuration_error"]
    with TestClient(create_app(settings), headers=AUTH) as client:
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready", "checks": expected}
        assert_headers(response)
        assert_error(
            client.post("/v1/analyze", json=fixture("invoice.request.json")), 503, "not_ready"
        )
        assert client.get("/health").json() == {"status": "ok"}


def test_request_size_and_encoding_contract(settings):
    settings.max_request_bytes = 1000
    with TestClient(create_app(settings), headers=AUTH) as client:
        response = client.post(
            "/v1/analyze", content=SENTINEL * 100, headers={"Content-Type": "application/json"}
        )
        assert_error(response, 413, "request_too_large")
        assert_error(
            client.post("/v1/analyze", json={}, headers={"Content-Encoding": "gzip"}),
            415,
            "unsupported_encoding",
        )


def test_busy_http_contract(settings):
    settings.max_concurrency = 1
    settings.queue_timeout_seconds = 0.01
    app = create_app(settings)
    with TestClient(app, headers=AUTH) as client:
        client.portal.call(app.state.orchestrator.slots.acquire)
        try:
            assert_error(
                client.post("/v1/analyze", json=fixture("invoice.request.json")), 429, "busy"
            )
        finally:
            client.portal.call(app.state.orchestrator.slots.release)


def test_analysis_deadline_contract(settings):
    class WaitingProvider(MockProvider):
        async def extract(self, content):
            await asyncio.Event().wait()

    settings.analysis_timeout_seconds = 0.01
    with TestClient(create_app(settings, WaitingProvider(settings)), headers=AUTH) as client:
        assert_error(
            client.post("/v1/analyze", json=fixture("invoice.request.json")),
            504,
            "analysis_timeout",
        )


async def test_body_receipt_timeout_contract(settings):
    settings.body_timeout_seconds = 0.01
    app = create_app(settings)

    async def delayed_body():
        await asyncio.Event().wait()
        yield b"never sent"

    with TestClient(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://contract"
        ) as client:
            response = await client.post(
                "/v1/analyze",
                content=delayed_body(),
                headers={**AUTH, "Content-Type": "application/json"},
            )
    assert_error(response, 408, "request_timeout")


def structural(node, *, property_map=False):
    if isinstance(node, dict):
        editorial = {"description", "title", "examples", "example", "summary", "operationId"}
        return {
            k: structural(v, property_map=(k == "properties" and not property_map))
            for k, v in node.items()
            if property_map or k not in editorial
        }
    if isinstance(node, list):
        return [structural(v) for v in node]
    return node


def test_openapi_v1_structure(client):
    schema = client.get("/openapi.json").json()
    assert structural({"paths": schema["paths"], "components": schema["components"]}) == fixture(
        "openapi.structure.json"
    )
    # A business property named description is not an editorial annotation.
    assert (
        "description"
        in fixture("openapi.structure.json")["components"]["schemas"]["LineItem"]["properties"]
    )
    for path in ("/v1/analyze", "/v1/prepare-xlsx"):
        assert schema["paths"][path]["post"]["security"] == [{"HTTPBearer": []}]
    # Liveness/readiness/version/docs require no service token.
    client.headers.pop("Authorization")
    for path in ("/health", "/ready", "/version", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 200
    assert client.get("/version").json()["schema_version"] == "1.0"


def test_prepare_xlsx_json_contract(client):
    recipe = fixture("prepare_xlsx.input.json")
    book = Workbook()
    book.active.title = recipe["sheet"]
    for row in recipe["rows"]:
        book.active.append(row)
    stream = io.BytesIO()
    book.save(stream)
    book.close()
    response = client.post(
        "/v1/prepare-xlsx",
        content=stream.getvalue(),
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
    )
    assert response.status_code == 200
    assert_headers(response)
    PrepareXlsxResponse.model_validate(response.json())
    assert response.json() == fixture("prepare_xlsx.response.json")


@pytest.mark.parametrize(
    "authorization,code", [("", "auth_missing"), ("Bearer synthetic-invalid-token", "auth_invalid")]
)
def test_prepare_xlsx_auth_contract(client, authorization, code):
    response = client.post(
        "/v1/prepare-xlsx",
        content=b"invalid-synthetic-workbook",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        },
    )
    assert_error(response, 401, code)


def test_content_limit_contract(settings):
    settings.max_content_chars = 100
    with TestClient(create_app(settings), headers=AUTH) as client:
        assert_error(
            client.post("/v1/analyze", json=fixture("invoice.request.json")),
            413,
            "content_too_large",
        )
