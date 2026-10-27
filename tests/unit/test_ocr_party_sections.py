"""OCR party roles must end at unrelated section headings without losing addresses."""

import pytest

from app.schemas.extraction import Evidence, ExtractionData, Fact
from app.schemas.requests import Content, Page
from app.validators.evidence import observations
from app.validators.validation import validate_extraction, validate_fact


def check(text, field, value, quote):
    content = Content(pages=[Page(page=1, text=text)])
    fact = Fact(
        value=value, status="verified", confidence=0.8, evidence=Evidence(page=1, quote=quote)
    )
    return validate_fact(field, fact, content, [])


def test_original_bank_heading_stops_supplier_context():
    text = "Dodávateľ:\nLipa\nIČO: 12345679\nBANKOVÉ SPOJENIE\nIČO: 87654326"
    result = check(text, "supplier_ico", "87654326", "IČO: 87654326")
    assert result.value == "87654326"
    assert result.status == "needs_review"
    assert not result._safe_to_suggest
    entry = next(o for o in observations(text) if o.value == "87654326")
    assert entry.role == "ambiguous_ico"


@pytest.mark.parametrize("party,label", [("supplier", "Dodávateľ"), ("customer", "Odberateľ")])
@pytest.mark.parametrize(
    "suffix,identifier,first,second",
    [
        ("ico", "IČO", "12345679", "87654326"),
        ("dic", "DIČ", "2020123456", "2020654321"),
        ("vat_id", "IČ DPH", "SK2020123456", "SK2020654321"),
    ],
)
def test_section_transition_covers_both_parties_and_all_identifiers(
    party, label, suffix, identifier, first, second
):
    text = f"{label}:\nLipa\n{identifier}: {first}\nPLATOBNÉ ÚDAJE\n{identifier}: {second}"
    result = check(text, f"{party}_{suffix}", second, f"{identifier}: {second}")
    assert result.value == second
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


def test_full_validation_cannot_verify_identifier_after_boundary(settings):
    text = "Dodávateľ:\nLipa\nIČO: 12345679\nBANKOVÉ SPOJENIE\nIČO: 87654326"
    raw = ExtractionData()
    raw.fields.supplier_ico = Fact(
        value="87654326",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="IČO: 87654326"),
    )
    result, _ = validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)
    assert result.fields.supplier_ico.value == "87654326"
    assert result.fields.supplier_ico.status == "needs_review"
    assert not result.fields.supplier_ico._safe_to_suggest


@pytest.mark.parametrize(
    "heading",
    [
        "BANKOVÉ SPOJENIE",
        "Platobné údaje:",
        "FAKTURAČNÉ ÚDAJE",
        "POZNÁMKY",
        "PODMIENKY ÚHRADY",
        "PREDMET ZMLUVY",
        "   BANKOVÉ     SPOJENIE  ",
        "BANKOVÉ SPOJENIE: účet v banke",
        "--- DOPLŇUJÚCE INFORMÁCIE ---",
        "Sekcia 2: Ďalšie informácie",
    ],
)
def test_heading_variants_end_inheritance(heading):
    text = f"Dodávateľ:\nLipa\nHlavná 12\nIČO: 12345679\n\n{heading}\n\nIČO: 87654326"
    result = check(text, "supplier_ico", "87654326", "IČO: 87654326")
    assert result.value == "87654326"
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


@pytest.mark.parametrize(
    "label,field,value",
    [
        ("Dodávateľ", "supplier_ico", "12345679"),
        ("Odberateľ", "customer_ico", "87654326"),
    ],
)
@pytest.mark.parametrize(
    "body",
    [
        "Lipa",
        "LIPA S.R.O.",
        "MOST, A.S.",
        "Lipa\nHlavná 12\n949 01 Nitra",
        "LIPA\n\nHLAVNÁ 12\n949 01 NITRA",
        "Most\nNitra",
    ],
)
def test_names_addresses_and_blank_lines_preserve_party(label, field, value, body):
    text = f"{label}:\n{body}\nIČO: {value}"
    result = check(text, field, value, f"IČO: {value}")
    assert result.status == "verified"
    assert result._safe_to_suggest


def test_boundary_clears_pending_party_name_and_identifier():
    for label in ("Dodávateľ:", "Dodávateľ:\nLipa\nIČO:"):
        entries = observations(label + "\nBANKOVÉ SPOJENIE\n87654326\nIČO: 87654326")
        assert not any(
            o.role.startswith("supplier_") and ("bankove" in o.value or o.value == "87654326")
            for o in entries
        )
        assert entries[-1].role == "ambiguous_ico"


def test_new_party_reestablishes_context_after_boundary():
    text = "Dodávateľ:\nLipa\nBANKOVÉ SPOJENIE\nOdberateľ:\nMost\nIČO: 87654326"
    result = check(text, "customer_ico", "87654326", "IČO: 87654326")
    assert result.status == "verified"
    assert not check(text, "supplier_ico", "87654326", "IČO: 87654326")._safe_to_suggest


def test_two_column_boundary_only_resets_affected_column():
    text = "Dodávateľ:                 Odberateľ:\nLipa                       Most\nBANKOVÉ SPOJENIE            Nitra\nIČO: 12345679               IČO: 87654326"
    assert check(text, "supplier_ico", "12345679", "IČO: 12345679").status == "needs_review"
    assert check(text, "customer_ico", "87654326", "IČO: 87654326").status == "verified"


def test_indented_right_column_boundary_does_not_reset_supplier():
    text = "Dodávateľ:                 Odberateľ:\nLipa                       Most\n                           BANKOVÉ SPOJENIE\nIČO: 12345679               IČO: 87654326"
    assert check(text, "supplier_ico", "12345679", "IČO: 12345679").status == "verified"
    result = check(text, "customer_ico", "87654326", "IČO: 87654326")
    assert result.status == "needs_review" and not result._safe_to_suggest


def test_standalone_block_heading_clears_both_columns():
    text = "Dodávateľ:                 Odberateľ:\nLipa                       Most\n\nPLATOBNÉ ÚDAJE\nIČO: 12345679               IČO: 87654326"
    for field, value in [("supplier_ico", "12345679"), ("customer_ico", "87654326")]:
        result = check(text, field, value, f"IČO: {value}")
        assert result.status == "needs_review" and not result._safe_to_suggest


def test_observation_offsets_and_source_evidence_are_preserved():
    from app.processors.ocr_text import normalize

    text = "Dodávateľ:\nLipa\nIČO: 12345679\nBANKOVÉ SPOJENIE\nIČO: 87654326"
    for entry in observations(text):
        assert normalize(text)[entry.start : entry.end] == normalize(entry.quote)
    result = check(text, "supplier_ico", "87654326", "IČO: 87654326")
    assert result.evidence.page == 1 and result.evidence.quote == "IČO: 87654326"


def test_full_validation_without_an_earlier_identifier_still_abstains(settings):
    text = "Dodávateľ:\nLipa\nBANKOVÉ SPOJENIE\nIČO: 87654326"
    raw = ExtractionData()
    raw.fields.supplier_ico = Fact(
        value="87654326",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="IČO: 87654326"),
    )
    data, _ = validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)
    assert data.fields.supplier_ico.status == "needs_review"
    assert not data.fields.supplier_ico._safe_to_suggest


@pytest.mark.parametrize("company", ["SEKCIA S.R.O.", "ODDIEL A.S."])
def test_company_suffix_prevents_structural_marker_false_positive(company):
    text = f"Dodávateľ:\n{company}\nHlavná 12\nIČO: 12345679"
    result = check(text, "supplier_ico", "12345679", "IČO: 12345679")
    assert result.status == "verified"


def test_supplier_then_customer_roles_do_not_mix():
    text = "Dodávateľ:\nLipa\nIČO: 12345679\n\nOdberateľ:\nMost\nIČO: 87654326"
    for field, value, opposite in [
        ("supplier_ico", "12345679", "customer_ico"),
        ("customer_ico", "87654326", "supplier_ico"),
    ]:
        assert check(text, field, value, f"IČO: {value}").status == "verified"
        result = check(text, opposite, value, f"IČO: {value}")
        assert result.status == "needs_review" and not result._safe_to_suggest


def test_shared_identifier_separated_by_blank_line_remains_ambiguous():
    text = "Dodávateľ:\nLipa\nIČO: 12345679\n\nOdberateľ:\nMost\nIČO: 12345679"
    for field in ["supplier_ico", "customer_ico"]:
        result = check(text, field, "12345679", "IČO: 12345679")
        assert result.status == "needs_review" and not result._safe_to_suggest


def test_correct_supplier_not_conflicted_by_unrelated_identifier(settings):
    text = "Dodávateľ:\nLipa\nIČO: 12345679\nBANKOVÉ SPOJENIE\nIČO: 87654326"
    raw = ExtractionData()
    raw.fields.supplier_ico = Fact(
        value="12345679",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="IČO: 12345679"),
    )
    data, _ = validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)
    assert data.fields.supplier_ico.status == "verified"
    assert data.fields.supplier_ico._safe_to_suggest


@pytest.mark.parametrize("party,label", [("supplier", "Dodávateľ"), ("customer", "Odberateľ")])
@pytest.mark.parametrize(
    "heading",
    [
        "BANKOVÉ     SPOJENIE: Tatra banka",
        "BANKOVÉ     SPOJENIE:",
        "BANKOVÉ     SPOJENIE",
        "BANKOVÉ     SPOJENIE: účet",
        "PLATOBNÉ     ÚDAJE: bankový prevod",
        "FAKTURAČNÉ     ÚDAJE: doplňujúce informácie",
    ],
)
def test_ocr_spaced_inline_heading_full_validation(settings, party, label, heading):
    from app.processors.ocr_text import normalize

    text = f"{label}:\nLipa\n{heading}\nIČO: 87654326"
    raw = ExtractionData()
    field = f"{party}_ico"
    setattr(
        raw.fields,
        field,
        Fact(
            value="87654326",
            status="verified",
            confidence=0.8,
            evidence=Evidence(page=1, quote="IČO: 87654326"),
        ),
    )
    data, _ = validate_extraction(raw, Content(pages=[Page(page=1, text=text)]), settings)
    fact = getattr(data.fields, field)
    assert fact.value == "87654326"
    assert fact.status == "needs_review"
    assert not fact._safe_to_suggest
    assert fact.evidence.quote == "IČO: 87654326"
    assert fact.evidence.page == 1
    assert observations(text)[-1].role == "ambiguous_ico"
    for entry in observations(text):
        assert normalize(text)[entry.start : entry.end] == normalize(entry.quote)


@pytest.mark.parametrize("side", ["left", "right", "right_only"])
@pytest.mark.parametrize("separator", ["spaces", "pipe", "tab"])
@pytest.mark.parametrize(
    "caption",
    [
        "BANKOVÉ     SPOJENIE",
        "BANKOVÉ     SPOJENIE:",
        "BANKOVÉ     SPOJENIE: Tatra banka",
    ],
)
def test_ocr_spaced_heading_preserves_other_column(side, separator, caption):
    def row(left, right):
        if separator == "pipe":
            return f"{left} | {right}"
        if separator == "tab":
            return f"{left}\t{right}"
        return f"{left:<60}{right}"

    if side == "right_only" and separator != "spaces":
        # Explicit separators retain an empty first column.
        heading = row("", caption)
    else:
        heading = row(
            caption if side == "left" else ("" if side == "right_only" else "Nitra"),
            "Nitra" if side == "left" else caption,
        )
    text = "\n".join(
        [
            row("Dodávateľ:", "Odberateľ:"),
            row("Lipa", "Most"),
            heading,
            row("IČO: 12345679", "IČO: 87654326"),
        ]
    )
    for party, value in [("supplier", "12345679"), ("customer", "87654326")]:
        fact = check(text, f"{party}_ico", value, f"IČO: {value}")
        affected = (party == "supplier") == (side == "left")
        assert fact.status == ("needs_review" if affected else "verified")
        assert fact._safe_to_suggest is not affected
