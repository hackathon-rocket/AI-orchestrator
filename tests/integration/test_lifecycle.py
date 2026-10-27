import importlib

import pytest
from fastapi.testclient import TestClient

from app.core.orchestrator import AnalysisOrchestrator
from app.core.workbooks import WorkbookService
from app.llm.mock_provider import MockProvider
from app.main import create_app


def test_import_does_not_read_deployment_settings(settings, monkeypatch):
    import app.main

    monkeypatch.setenv("MAX_CONCURRENCY", "invalid-deployment-value")
    module = importlib.reload(app.main)
    with TestClient(module.create_app(settings)) as client:
        assert client.get("/ready").status_code == 200


def test_shutdown_closes_remaining_resources_after_cleanup_failure(settings, monkeypatch):
    closed = []

    async def close_workbooks(self):
        closed.append("workbooks")
        raise RuntimeError("synthetic cleanup failure")

    async def close_analysis(self):
        closed.append("analysis")

    async def close_provider(self):
        closed.append("provider")

    monkeypatch.setattr(WorkbookService, "close", close_workbooks)
    monkeypatch.setattr(AnalysisOrchestrator, "close", close_analysis)
    monkeypatch.setattr(MockProvider, "close", close_provider)
    with (
        pytest.raises(RuntimeError, match="synthetic cleanup failure"),
        TestClient(create_app(settings)),
    ):
        pass
    assert closed == ["workbooks", "analysis", "provider"]
