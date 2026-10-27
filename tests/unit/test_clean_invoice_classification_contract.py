"""Controlled provider proposals for the real-request clean_invoice failure."""

import json
from decimal import Decimal

import httpx
import pytest

from app.llm.mock_provider import fixture_extract
from app.llm.openai_provider import OpenAIProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import Evidence, Fact
from app.schemas.requests import Content, FormContext, Page
from app.validators.validation import validate_extraction, validate_fact


@pytest.fixture
def clean_proposal(load_fixture):
    payload = load_fixture("clean_invoice")
    content = Content.model_validate(payload["content"])
    return content, fixture_extract(content)


def proposal(value, quote, page=3):
    return Fact(
        value=value, status="verified", confidence=0.8, evidence=Evidence(page=page, quote=quote)
    )


CASES = [
    ("canonical-full", "invoice", "FAKTÚRA č. FA-2026-001", 3, False, "verified"),
    ("canonical-partial", "invoice", "FAKTÚRA", 3, False, "verified"),
    ("localized", "faktúra", "FAKTÚRA č. FA-2026-001", 3, False, "verified"),
    ("number-only", "invoice", "FA-2026-001", 3, False, "not_found"),
    ("missing", None, None, 3, False, "not_found"),
    ("fabricated", "invoice", "FAKTÚRA č. FA-999999", 3, False, "not_found"),
    ("wrong-type", "contract", "FAKTÚRA č. FA-2026-001", 3, False, "not_found"),
    ("wrong-page", "invoice", "FAKTÚRA č. FA-2026-001", 1, False, "not_found"),
    ("conflict", "invoice", "FAKTÚRA č. FA-2026-001", 3, True, "needs_review"),
]


@pytest.mark.parametrize("case,value,quote,page,conflict,status", CASES, ids=[c[0] for c in CASES])
def test_clean_fixture_classification_and_bbsk(
    settings, clean_proposal, case, value, quote, page, conflict, status
):
    content, raw = clean_proposal
    if conflict:
        content.pages[0].text += "\n\nZMLUVA č. Z-1\nPredmet zmluvy: Nájom"
    raw.classification = proposal(value, quote, page) if value else Fact()
    before = raw.model_dump_json()
    data, _ = validate_extraction(raw, content, settings)
    assert raw.model_dump_json() == before  # Validation must not rewrite the provider proposal.
    assert data.classification.status == status
    verified = status == "verified"
    assert data.document_type == ("invoice" if verified else "unknown")
    assert data.classification._safe_to_suggest == verified
    if verified:
        assert data.classification.value == "invoice"
        assert data.classification.evidence.quote == quote
        assert data.classification.evidence.page == 3
    # Missing/invalid classification must neither erase good facts nor infer a type from them.
    assert data.fields.invoice_number.status == "verified"
    assert data.fields.total_gross.value == Decimal("1230.00")
    assert data.fields.total_gross.status == "verified"
    assert len(data.line_items) == 2
    config, form, _ = ConfigLoader(settings.form_config_dir, FormSchemaParser()).resolve(
        FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default")
    )
    fields, tables, _ = map_extraction(data, config, form)
    if verified:
        assert tables[0].tag == "rozpocet.zoznamuctovnychdokladov"
        assert tables[0].rows[0]["typ"].value == "FA"
        assert tables[0].rows[0]["suma_doklad"].value == 1230.0
    else:
        assert fields == tables == []


@pytest.mark.parametrize(
    "value", ["invoice or contract", "faktúra č. FA-2026-001", "FA-2026-001", "1230.00"]
)
def test_free_text_classification_not_guessed(clean_proposal, value):
    content, _ = clean_proposal
    result = validate_fact("classification", proposal(value, "FAKTÚRA č. FA-2026-001"), content, [])
    assert result.status == "not_found"
    assert not result._safe_to_suggest


@pytest.mark.parametrize("value", ["faktúra", "FAKTURA", " Invoice ", "daňový doklad – faktúra"])
def test_localized_value_still_requires_valid_heading(clean_proposal, value):
    content, _ = clean_proposal
    valid = validate_fact("classification", proposal(value, "FAKTÚRA"), content, [])
    assert valid.status == "verified" and valid.value == "invoice"
    for quote in ["FA-2026-001", "Dodávateľ: Ukážkový dodávateľ s.r.o."]:
        invalid = validate_fact("classification", proposal(value, quote), content, [])
        assert invalid.status == "not_found" and not invalid._safe_to_suggest


async def test_openai_wire_localized_proposal_without_network(settings, clean_proposal):
    content, raw = clean_proposal
    raw.classification.value = "faktúra"
    settings.llm_model = "gpt-4.1-mini"
    settings.openai_api_key = "synthetic-offline-token"
    captured = []

    async def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "resp_offline_classification",
                "object": "response",
                "created_at": 1,
                "model": "gpt-4.1-mini",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_offline",
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
                "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
            },
        )

    provider = OpenAIProvider(settings, httpx.MockTransport(respond))
    try:
        proposed = await provider.extract(content)
        assert proposed.classification.value == "faktúra"
        data, _ = validate_extraction(proposed, content, settings)
        assert data.document_type == "invoice"
        assert data.classification.value == "invoice"
        assert data.classification.status == "verified"
        assert len(captured) == 1
        schema = captured[0]["text"]["format"]["schema"]
        assert "canonical" in schema["properties"]["classification"]["description"]
        classification = schema["$defs"]["ClassificationProposal"]
        assert classification["properties"]["value"]["anyOf"] == [
            {"type": "string", "enum": ["invoice", "receipt", "contract", "bank_statement"]},
            {"type": "null"},
        ]
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "value,kind,heading",
    [
        ("bloček", "receipt", "POKLADNIČNÝ DOKLAD č. P-1"),
        ("zmluva", "contract", "ZMLUVA č. Z-1"),
        ("výpis z účtu", "bank_statement", "VÝPIS Z ÚČTU"),
    ],
)
def test_other_known_type_names_still_need_source(value, kind, heading):
    content = Content(pages=[Page(page=3, text=heading)])
    result = validate_fact("classification", proposal(value, heading), content, [])
    assert result.value == kind and result.status == "verified"


def test_localized_type_does_not_turn_body_text_into_heading(clean_proposal):
    content, _ = clean_proposal
    content.pages[0].text = "Toto nie je FAKTÚRA.\nSuma spolu: 1 230,00 EUR"
    result = validate_fact("classification", proposal("faktúra", "FAKTÚRA"), content, [])
    assert result.status == "not_found"
    assert not result._safe_to_suggest


def test_provider_uncertainty_and_top_level_mismatch_are_preserved(settings, clean_proposal):
    content, raw = clean_proposal
    raw.classification.value = "faktúra"
    raw.classification.status = "needs_review"
    data, _ = validate_extraction(raw, content, settings)
    assert data.document_type == "unknown"
    assert data.classification.status == "needs_review"
    raw.classification.status = "verified"
    raw.document_type = "contract"
    data, _ = validate_extraction(raw, content, settings)
    assert data.document_type == "unknown"
