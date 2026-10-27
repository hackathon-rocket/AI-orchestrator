import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "synthetic-test-token-only"


@pytest.fixture(autouse=True)
def isolate_environment(monkeypatch):
    # Never consume developer credentials or local .env in tests.
    for key in list(os.environ):
        if key.lower() in Settings.model_fields:
            monkeypatch.delenv(key)


@pytest.fixture
def settings():
    return Settings(_env_file=None, agent_api_key=TOKEN, llm_retry_backoff=0)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings), headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


@pytest.fixture
def load_fixture():
    def load(name):
        return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))

    return load
