"""Rejected classifications must explain the failing check without echoing raw data."""

import pytest

from app.llm.mock_provider import fixture_extract
from app.schemas.extraction import CellLocator, Evidence, Fact
from app.schemas.requests import Cell, Content, Page, Sheet, SheetRow
from app.validators.validation import validate_extraction, validate_fact

HEADING = "FAKTÚRA č. FA-2026-001"


def proposal(value="invoice", quote=HEADING, page=3, **kwargs):
    return Fact(
        value=value,
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=page, quote=quote),
        **kwargs,
    )


@pytest.mark.parametrize(
    "fact,text,reason",
    [
        (Fact(), HEADING, "classification_missing"),
        (Fact(value="invoice"), HEADING, "classification_evidence_missing"),
        (proposal(source_kind="calculated"), HEADING, "classification_not_document"),
        (proposal("secret-invalid-value"), HEADING, "classification_value_invalid"),
        (proposal("unknown"), HEADING, "classification_unknown"),
        (proposal(page=1), HEADING, "classification_evidence_mismatch"),
        (proposal(quote="secret-fabricated-quote"), HEADING, "classification_evidence_mismatch"),
        (proposal(quote="FA-2026-001"), HEADING, "classification_quote_not_heading"),
        (proposal("contract"), HEADING, "classification_heading_mismatch"),
        (
            proposal(quote="FAKTÚRA"),
            "Toto nie je FAKTÚRA na spracovanie.",
            "classification_heading_missing",
        ),
        (
            proposal(quote="FAKTÚRA"),
            HEADING + "\nPoznámka: FAKTÚRA je v prílohe.",
            "classification_quote_ambiguous",
        ),
        (
            proposal(),
            HEADING.replace("č.", "č ."),
            "classification_evidence_spacing",
        ),
    ],
)
def test_rejection_reasons_are_specific_server_generated_and_private(fact, text, reason):
    # A provider cannot supply diagnostic codes or authorization flags.
    fact.validation_reasons = ["secret-provider-reason"]
    fact._safe_to_suggest = True
    fact._role_supported = True
    before = fact.model_dump_json()
    warnings = []
    result = validate_fact(
        "classification", fact, Content(pages=[Page(page=3, text=text)]), warnings
    )
    assert fact.model_dump_json() == before
    assert result.value is None
    assert result.status == "not_found"
    assert result.confidence == 0
    assert result.evidence is None
    assert result.validation_reasons == [reason]
    assert not result._safe_to_suggest and not result._role_supported
    assert len(warnings) == 1 and f"[{reason}]" in warnings[0]
    assert "secret-" not in result.model_dump_json() + " ".join(warnings)
    assert HEADING not in " ".join(warnings)


@pytest.mark.parametrize("formula", [None, '=CONCAT("FAKT", "ÚRA")'])
def test_excel_classification_diagnostics_keep_cell_evidence_rules(formula):
    content = Content(
        sheets=[
            Sheet(
                name="Doklad",
                rows=[
                    SheetRow(row=1, cells=[Cell(coordinate="A1", value=HEADING, formula=formula)])
                ],
            )
        ]
    )
    fact = Fact(
        value="contract" if formula is None else "invoice",
        status="verified",
        evidence=Evidence(quote=HEADING, locator=CellLocator(sheet="Doklad", cell="A1")),
    )
    result = validate_fact("classification", fact, content, [])
    assert result.validation_reasons == [
        "classification_formula" if formula else "classification_heading_mismatch"
    ]
    assert not result._safe_to_suggest


def test_top_level_disagreement_retains_grounded_evidence_but_blocks_mapping(settings):
    content = Content(pages=[Page(page=3, text=HEADING)])
    raw = fixture_extract(content)
    raw.document_type = "contract"
    data, warnings = validate_extraction(raw, content, settings)
    assert data.document_type == "unknown"
    assert data.classification.value == "invoice"
    assert data.classification.status == "needs_review"
    assert data.classification.evidence == raw.classification.evidence
    assert data.classification.validation_reasons == ["classification_document_type_mismatch"]
    assert not data.classification._safe_to_suggest
    assert any("[classification_document_type_mismatch]" in warning for warning in warnings)
