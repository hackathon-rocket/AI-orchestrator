"""Scripted raw model proposals: these tests are independent of the mock recognizer."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from scripts.evaluate_scripted import ScriptedProvider

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/scripted_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_scripted_raw_pipeline(settings, case):
    provider = ScriptedProvider(settings, case["raw"])
    with TestClient(create_app(settings, provider=provider)) as client:
        response = client.post(
            "/v1/analyze",
            headers={"Authorization": "Bearer " + settings.agent_api_key.get_secret_value()},
            json={
                "document_id": case["name"],
                "content": case["content"],
                "form": {"id": "bbsk_11824", "mapping_id": "bbsk_11824_default"},
            },
        )
    assert response.status_code == 200, response.text
    result = response.json()
    for key, expected in case["expected"].items():
        actual = result["extracted_data"]["fields"][key]
        assert actual["value"] == expected["value"], key
        assert actual["status"] == expected["status"], key
        if actual["value"] is not None:
            assert actual["evidence"] == case["raw"]["fields"][key]["evidence"] | {"locator": None}
    mapped = {
        tag: field["value"]
        for table in result["tables"]
        for row in table["rows"]
        for tag, field in row.items()
    }
    for tag, value in case["mapped"].items():
        assert tag in mapped and mapped[tag] == value
    if result["document_type"] == "unknown":
        assert result["tables"] == []
    assert result["requires_review"] is True
