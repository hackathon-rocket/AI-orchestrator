import asyncio
import json

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

from app.core.errors import ProviderError, ServiceError
from app.core.orchestrator import AnalysisOrchestrator
from app.llm.base import BoundedProvider
from app.llm.compatible_provider import CompatibleProvider
from app.llm.factory import create_provider
from app.llm.openai_provider import OpenAIProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.schemas.extraction import ExtractionData
from app.schemas.requests import AnalyzeRequest, Content, Page

CONTENT = Content(pages=[Page(page=1, text="Príklad")])


class ScriptedProvider(BoundedProvider):
    def __init__(self, settings, outcomes):
        super().__init__(settings)
        self.outcomes = iter(outcomes)
        self.calls = 0

    async def attempt(self, content):
        self.calls += 1
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, float):
            await asyncio.sleep(outcome)
        return ExtractionData()


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError(),
        ModelHTTPError(429, "model"),
        ModelHTTPError(503, "model"),
        UnexpectedModelBehavior("bad JSON"),
    ],
)
async def test_transient_retry_then_success(settings, failure):
    provider = ScriptedProvider(settings, [failure, None])
    assert (await provider.extract(CONTENT)).document_type == "unknown"
    assert provider.calls == 2


async def test_retry_exhaustion_and_non_retryable(settings):
    provider = ScriptedProvider(settings, [ModelHTTPError(500, "m")] * 3)
    with pytest.raises(ProviderError, match="provider_unavailable"):
        await provider.extract(CONTENT)
    assert provider.calls == 3
    provider = ScriptedProvider(settings, [ModelHTTPError(401, "m")])
    with pytest.raises(ProviderError, match="provider_auth_error"):
        await provider.extract(CONTENT)
    assert provider.calls == 1


async def test_provider_timeout_is_bounded(settings):
    settings.llm_timeout_seconds = 0.01
    settings.llm_max_retries = 1
    provider = ScriptedProvider(settings, [0.2, 0.2])
    with pytest.raises(ProviderError, match="provider_timeout"):
        await provider.extract(CONTENT)
    assert provider.calls == 2


def test_remote_provider_explicit_enable_required(settings):
    settings.llm_provider = "openai"
    settings.llm_model = "configured-by-operator"
    assert "remote_llm_not_authorized" in settings.readiness_errors()
    with pytest.raises(ServiceError):
        create_provider(settings)


async def test_openai_responses_wire_format_without_network(settings):
    settings.llm_provider = "openai"
    settings.llm_model = "gpt-4.1-mini"
    settings.openai_api_key = "synthetic-provider-test-only"
    captured = []

    async def transport(request):
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_1",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": ExtractionData().model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            },
        )

    provider = OpenAIProvider(settings, httpx.MockTransport(transport))
    try:
        assert (await provider.extract(CONTENT)).document_type == "unknown"
        body = json.loads(captured[0].content)
        assert captured[0].url.path == "/v1/responses"
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["text"]["format"]["strict"] is True
        assert body["store"] is False
        assert not body.get("tools")
        assert len(captured) == 1
        assert "synthetic-provider-test-only" not in json.dumps(body)
    finally:
        await provider.close()


@pytest.mark.parametrize("mode", ["prompted", "native", "tool"])
async def test_compatible_modes_without_network(settings, mode):
    settings.llm_provider = "openai_compatible"
    settings.llm_base_url = "http://localhost:9009/v1"
    settings.llm_model = "local-test-model"
    settings.llm_output_mode = mode
    captured = []

    async def transport(request):
        body = json.loads(request.content)
        captured.append(body)
        message = {"role": "assistant", "content": ExtractionData().model_dump_json()}
        if mode == "tool":
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": body["tools"][0]["function"]["name"],
                            "arguments": ExtractionData().model_dump_json(),
                        },
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "local-test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if mode == "tool" else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    provider = CompatibleProvider(settings, httpx.MockTransport(transport))
    try:
        assert (await provider.extract(CONTENT)).document_type == "unknown"
        assert len(captured) == 1
        if mode == "prompted":
            assert not captured[0].get("tools")
            assert "response_format" not in captured[0]
    finally:
        await provider.close()


async def test_malformed_provider_output_retries_without_sdk_retry(settings):
    settings.llm_provider = "openai_compatible"
    settings.llm_base_url = "http://localhost:9009/v1"
    settings.llm_model = "local"
    calls = []

    async def transport(request):
        calls.append(1)
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "local",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "not json"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = CompatibleProvider(settings, httpx.MockTransport(transport))
    try:
        with pytest.raises(ProviderError, match="provider_invalid_output"):
            await provider.extract(CONTENT)
        assert len(calls) == 3
    finally:
        await provider.close()


async def test_concurrency_gate_and_cancellation(settings):
    settings.max_concurrency = 1
    settings.queue_timeout_seconds = 0.01
    provider = ScriptedProvider(settings, [0.1, None])
    orchestrator = AnalysisOrchestrator(
        settings, provider, ConfigLoader(settings.form_config_dir, FormSchemaParser())
    )
    request = AnalyzeRequest(document_id="test", content=CONTENT)
    first = asyncio.create_task(orchestrator.analyze(request, "first"))
    await asyncio.sleep(0.01)
    with pytest.raises(ServiceError, match="busy"):
        await orchestrator.analyze(request, "second")
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert (await orchestrator.analyze(request, "third")).status == "completed"


async def test_total_analysis_deadline(settings):
    settings.analysis_timeout_seconds = 0.01
    provider = ScriptedProvider(settings, [0.2])
    orchestrator = AnalysisOrchestrator(
        settings, provider, ConfigLoader(settings.form_config_dir, FormSchemaParser())
    )
    with pytest.raises(ServiceError, match="analysis_timeout"):
        await orchestrator.analyze(AnalyzeRequest(document_id="test", content=CONTENT), "deadline")


@pytest.mark.parametrize(
    "status,body,code,retries",
    [
        (401, None, "provider_auth_error", 0),
        (403, None, "provider_auth_error", 0),
        (404, None, "provider_configuration_error", 0),
        (429, None, "provider_rate_limited", 2),
        (503, None, "provider_unavailable", 2),
        (
            400,
            {
                "error": {
                    "param": "response_format",
                    "code": "unsupported_parameter",
                    "message": "sensitive OCR",
                }
            },
            "provider_unsupported_mode",
            0,
        ),
        (
            400,
            {"error": {"param": "other", "message": "sensitive OCR"}},
            "provider_rejected_request",
            0,
        ),
        (400, {"error": {"param": []}}, "provider_rejected_request", 0),
        (
            400,
            {"error": {"param": "response_format", "code": {}}},
            "provider_rejected_request",
            0,
        ),
    ],
)
async def test_stable_error_codes_and_retry_policy(settings, status, body, code, retries):
    provider = ScriptedProvider(settings, [ModelHTTPError(status, "m", body)] * 3)
    with pytest.raises(ProviderError) as failure:
        await provider.extract(CONTENT)
    assert failure.value.code == code
    assert "sensitive OCR" not in str(failure.value)
    assert provider.calls == retries + 1


@pytest.mark.parametrize("adapter", [CompatibleProvider, OpenAIProvider])
async def test_failed_initialization_closes_http_client(settings, monkeypatch, adapter):
    from pydantic_ai.exceptions import UserError

    import app.llm.client_provider as module

    settings.llm_provider = "openai_compatible"
    settings.llm_base_url = "http://localhost:9009/v1"
    settings.llm_model = "local"
    settings.openai_api_key = "synthetic-offline-test-only"
    captured = []
    real_client = httpx.AsyncClient

    class TrackingClient(real_client):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(self)

    def fail(*args, **kwargs):
        raise UserError("sensitive initialization details")

    monkeypatch.setattr(module.httpx, "AsyncClient", TrackingClient)
    monkeypatch.setattr(adapter, "create_agent", fail)
    provider = adapter(settings, httpx.MockTransport(lambda request: httpx.Response(500)))
    with pytest.raises(ProviderError, match="provider_configuration_error"):
        await provider.extract(CONTENT)
    assert len(captured) == 1 and captured[0].is_closed
    await provider.close()


async def test_cancellation_does_not_retry(settings):
    provider = ScriptedProvider(settings, [1.0, None])
    task = asyncio.create_task(provider.extract(CONTENT))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.calls == 1
