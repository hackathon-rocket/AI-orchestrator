import io
import json
import logging
import socket

import pytest
from fastapi.testclient import TestClient
from pytest_socket import SocketConnectBlockedError

from app.core.errors import ProviderError
from app.llm.mock_provider import MockProvider
from app.main import create_app


def test_external_network_is_blocked():
    with (
        socket.socket() as sock,
        pytest.warns(UserWarning),
        pytest.raises(SocketConnectBlockedError),
    ):
        sock.connect(("203.0.113.1", 443))


def test_logs_exclude_content_tokens_and_provider_error_body(settings, load_fixture):
    class FailingProvider(MockProvider):
        async def extract(self, content):
            raise ProviderError("provider_timeout", 504)

    with TestClient(
        create_app(settings, FailingProvider(settings)),
        headers={"Authorization": "Bearer synthetic-test-token-only"},
    ) as client:
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        logger = logging.getLogger("egrant.metadata")
        logger.addHandler(handler)
        try:
            response = client.post("/v1/analyze", json=load_fixture("clean_invoice"))
            assert response.status_code == 504
            assert response.json()["error"]["code"] == "provider_timeout"
        finally:
            logger.removeHandler(handler)
        logs = output.getvalue()
        assert "synthetic-test-token-only" not in logs
        assert "FA-2026-001" not in logs
        assert "Ukážkový" not in logs
        assert "1230" not in logs
        for line in logs.splitlines():
            assert "request_id" in json.loads(line)


def test_no_cors_writes_or_confirmation_routes(client):
    assert (
        client.options(
            "/v1/analyze",
            headers={"Origin": "http://frontend.example", "Access-Control-Request-Method": "POST"},
        ).headers.get("Access-Control-Allow-Origin")
        is None
    )
    assert client.post("/v1/confirm", json={}).status_code == 404


def test_content_type_and_encoding(client):
    assert client.post("/v1/analyze", content="plain text").status_code == 415
    assert (
        client.post("/v1/analyze", json={}, headers={"Content-Encoding": "gzip"}).status_code == 415
    )


@pytest.mark.parametrize("path", ["/v1/analyze", "/v1/prepare-xlsx"])
@pytest.mark.parametrize("prefix", ["", "/service"])
def test_prefixed_routes_enforce_auth_and_body_guards(settings, path, prefix):
    settings.max_request_bytes = settings.max_xlsx_bytes = 1000
    content_type = (
        "application/json"
        if path == "/v1/analyze"
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    with TestClient(create_app(settings), root_path="/service") as client:
        response = client.post(prefix + path, content=b"invalid")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "auth_missing"
        headers = {"Authorization": "Bearer synthetic-test-token-only"}
        assert client.post(prefix + path, content=b"invalid", headers=headers).status_code == 415
        headers["Content-Type"] = content_type
        response = client.post(prefix + path, content=b"x" * 1001, headers=headers)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "request_too_large"


def test_authenticated_analysis_under_url_prefix(settings, load_fixture):
    with TestClient(create_app(settings), root_path="/service") as client:
        response = client.post(
            "/service/v1/analyze",
            json=load_fixture("clean_invoice"),
            headers={"Authorization": "Bearer synthetic-test-token-only"},
        )
    assert response.status_code == 200
    assert response.json()["requires_review"] is True


def test_media_type_and_content_coding_are_case_insensitive(client, load_fixture):
    response = client.post(
        "/v1/analyze",
        json=load_fixture("clean_invoice"),
        headers={"Content-Type": "Application/JSON; charset=utf-8", "Content-Encoding": "IDENTITY"},
    )
    assert response.status_code == 200


def test_unexpected_provider_errors_are_sanitized(settings, load_fixture, caplog):
    secret = "synthetic-private-provider-body"

    class FailingProvider(MockProvider):
        async def extract(self, content):
            raise RuntimeError(secret)

    with TestClient(
        create_app(settings, FailingProvider(settings)),
        headers={"Authorization": "Bearer synthetic-test-token-only"},
    ) as client:
        response = client.post("/v1/analyze", json=load_fixture("clean_invoice"))
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert secret not in response.text
    assert secret not in caplog.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
