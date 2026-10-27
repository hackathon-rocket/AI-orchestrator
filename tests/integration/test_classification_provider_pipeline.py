"""Exercise OpenAI parsing, validation, BBSK mapping and the HTTP contract offline."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm.openai_provider import OpenAIProvider
from app.main import create_app
from app.schemas.extraction import Evidence, ExtractionData, Fact, UniversalFields

HEADING = "FAKTÚRA č. FA-2026-001"


def fact(value, quote):
    return Fact(
        value=value, status="verified", confidence=0.8, evidence=Evidence(page=3, quote=quote)
    )


@pytest.mark.parametrize("include_unmapped", [True, False])
@pytest.mark.parametrize(
    "kind,quote,reason",
    [
        ("invoice", HEADING, None),
        ("faktúra", HEADING, None),
        ("invoice", "FA-2026-001", "classification_quote_not_heading"),
        ("contract", HEADING, "classification_heading_mismatch"),
        ("unknown", HEADING, "classification_unknown"),
        (None, None, "classification_missing"),
    ],
)
def test_openai_to_bbsk_and_private_rejection_diagnostics(
    settings, load_fixture, capsys, kind, quote, reason, include_unmapped
):
    payload = load_fixture("clean_invoice")
    payload["options"]["include_unmapped"] = include_unmapped
    raw = ExtractionData(
        document_type="invoice",
        classification=fact(kind, quote) if kind else Fact(),
        fields=UniversalFields(
            invoice_number=fact("FA-2026-001", HEADING),
            total_gross=fact("1230.00", "Suma spolu: 1 230,00 EUR"),
            currency=fact("EUR", "Mena: EUR"),
        ),
    )
    raw.classification.validation_reasons = ["private-provider-reason"]
    settings.llm_provider = "openai"
    settings.llm_model = "gpt-4.1-mini"
    settings.openai_api_key = "synthetic-offline-only"
    settings.allow_remote_llm = True
    calls = []

    async def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_synthetic",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_synthetic",
                        "status": "completed",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": raw.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    provider = OpenAIProvider(settings, httpx.MockTransport(respond))
    with TestClient(create_app(settings, provider=provider)) as client:
        response = client.post(
            "/v1/analyze",
            json=payload,
            headers={"Authorization": "Bearer synthetic-test-token-only"},
        )
    assert response.status_code == 200
    result = response.json()
    assert len(calls) == 1
    assert calls[0]["store"] is False and not calls[0].get("tools")
    assert result["requires_review"] is True
    assert "private-provider-reason" not in response.text
    if reason:
        assert result["document_type"] == "unknown" and result["tables"] == []
        assert any(f"[{reason}]" in warning for warning in result["warnings"])
    else:
        assert result["document_type"] == "invoice"
        table = result["tables"][0]
        assert table["tag"] == "rozpocet.zoznamuctovnychdokladov"
        row = table["rows"][0]
        assert row["typ"]["value"] == "FA"
        assert row["suma_doklad"]["value"] == 1230
        assert row["typ"]["page"] == 3
        assert row["typ"]["evidence"] == HEADING
        assert row["suma_dotacia"]["value"] is None
        assert row["datum_1"]["value"] is None
        assert row["p_c"]["value"] is None
    if include_unmapped:
        classification = result["extracted_data"]["classification"]
        assert classification["validation_reasons"] == ([reason] if reason else [])
        assert classification["value"] == (None if reason else "invoice")
        fields = result["extracted_data"]["fields"]
        assert fields["invoice_number"]["status"] == "verified"
        assert fields["total_gross"]["value"] == "1230.00"
    else:
        assert result["extracted_data"] is None
    logs = capsys.readouterr()
    for private in [
        HEADING,
        "FA-2026-001",
        '"value": "1230.00"',
        "private-provider-reason",
        "synthetic-offline-only",
    ]:
        assert private not in logs.out + logs.err
