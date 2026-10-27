"""Excel label relationships must be established by source structure, not model hints."""

from decimal import Decimal

import pytest

from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.mapping.mapper import map_extraction
from app.schemas.extraction import CellLocator, Evidence, ExtractionData, Fact
from app.schemas.requests import Cell, Content, FormContext, Page, Sheet, SheetRow
from app.validators.validation import validate_extraction, validate_fact


def source(values, headers=None, formulas=None):
    rows = {}
    for coordinate, value in values.items():
        row = int("".join(c for c in coordinate if c.isdigit()))
        rows.setdefault(row, []).append(
            Cell(
                coordinate=coordinate,
                value=value,
                header_cells=(headers or {}).get(coordinate, []),
                formula=(formulas or {}).get(coordinate),
            )
        )
    return Content(
        pages=[Page(page=1, text="FAKTÚRA č. XLS-1")],
        sheets=[
            Sheet(
                name="Sheet",
                rows=[SheetRow(row=row, cells=cells) for row, cells in sorted(rows.items())],
            )
        ],
    )


def candidate(value, cell, context):
    return Fact(
        value=str(value),
        status="verified",
        confidence=0.8,
        evidence=Evidence(
            quote=str(value), locator=CellLocator(sheet="Sheet", cell=cell, context_cells=context)
        ),
    )


def test_unrelated_same_row_amount_cannot_use_neighbours_label():
    content = source({"A1": "Mena dokladu", "B1": "EUR", "A2": "Suma spolu", "B2": 120, "C2": 999})
    result = validate_fact("total_gross", candidate(999, "C2", ["A2"]), content, [])
    assert result.value == Decimal("999.00")
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


def test_wrong_amount_cannot_reach_real_bbsk_mapping(settings):
    content = source({"A1": "Mena dokladu", "B1": "EUR", "A2": "Suma spolu", "B2": 120, "C2": 999})
    raw = ExtractionData(
        document_type="invoice",
        classification=Fact(
            value="invoice",
            status="verified",
            confidence=0.8,
            evidence=Evidence(page=1, quote="FAKTÚRA č. XLS-1"),
        ),
    )
    raw.fields.total_gross = candidate(999, "C2", ["A2"])
    data, _ = validate_extraction(raw, content, settings)
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    mapping, form, _ = loader.resolve(FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default"))
    _, tables, _ = map_extraction(data, mapping, form)
    assert data.fields.total_gross.status != "verified"
    assert not data.fields.total_gross._safe_to_suggest
    assert data.document_type == "invoice"
    assert len(tables) == 1 and len(tables[0].rows) == 1
    assert tables[0].rows[0]["typ"].value == "FA"
    assert tables[0].rows[0]["suma_doklad"].value is None


@pytest.mark.parametrize(
    "values,cell,context",
    [
        ({"A2": "Suma spolu", "B2": 120}, "B2", ["A2"]),
        ({"A2": "Suma spolu", "B2": None, "C2": 120}, "C2", ["A2"]),
        ({"A2": "Suma spolu", "B2": "  ", "C2": 120}, "C2", ["A2"]),
        ({"B1": "Suma spolu", "B2": 120}, "B2", ["B1"]),
        ({"B1": "Suma spolu", "B2": None, "B3": 120}, "B3", ["B1"]),
        (
            {
                "A1": "Číslo faktúry",
                "B1": "Suma spolu",
                "A2": "X-1",
                "B2": 120,
                "A3": "X-2",
                "B3": 130,
            },
            "B3",
            ["B1"],
        ),
        (
            {
                "A1": "Číslo faktúry",
                "B1": "Suma spolu",
                "B2": "EUR",
                "A3": "X-1",
                "B3": 120,
                "A4": "X-2",
                "B4": 130,
            },
            "B4",
            ["B1"],
        ),
        (
            {
                "A1": "Prehľad faktúr",
                "A2": "Číslo faktúry",
                "B2": "Suma spolu",
                "A3": "X-1",
                "B3": 120,
                "A4": "X-2",
                "B4": 130,
            },
            "B4",
            ["B2"],
        ),
        ({"Z1": "Číslo faktúry", "AA1": "Suma spolu", "Z2": "X-1", "AA2": 120}, "AA2", ["AA1"]),
    ],
)
def test_valid_horizontal_vertical_and_table_relationships(values, cell, context):
    content = source(values)
    proposed = candidate(values[cell], cell, context)
    result = validate_fact("total_gross", proposed, content, [])
    assert result.status == "verified"
    assert result._safe_to_suggest
    assert result.value == Decimal(str(values[cell])).quantize(Decimal(".01"))
    assert result.evidence == proposed.evidence


@pytest.mark.parametrize(
    "values,cell,context",
    [
        ({"A2": "Suma spolu", "B2": 120, "D2": 999}, "D2", ["A2"]),
        ({"A2": "Suma spolu", "B2": 120, "A3": 999}, "A3", ["A2"]),
        ({"B1": "Suma spolu", "B2": 120, "B3": 999}, "B3", ["B1"]),
        (
            {
                "A1": "Číslo faktúry",
                "B1": "Suma spolu",
                "A2": "X-1",
                "B2": 120,
                "A4": "X-2",
                "B4": 999,
            },
            "B4",
            ["B1"],
        ),
        (
            {"A1": "Číslo faktúry", "B1": "Suma spolu", "B2": "Základ DPH", "A3": "X-1", "B3": 999},
            "B3",
            ["B1"],
        ),
        (
            {
                "A1": "Číslo faktúry",
                "B1": "Suma spolu",
                "A2": "X-1",
                "B2": 120,
                "A3": "Poznámka",
                "B3": "Iný údaj",
                "A4": "X-2",
                "B4": 999,
            },
            "B4",
            ["B1"],
        ),
    ],
)
def test_competing_values_and_intervening_headers_are_not_authoritative(values, cell, context):
    result = validate_fact(
        "total_gross", candidate(values[cell], cell, context), source(values), []
    )
    assert result.status != "verified"
    assert not result._safe_to_suggest


@pytest.mark.parametrize("context", ["Z99", "not-a-cell", "A0", "B999999999", "A1"])
def test_missing_invalid_or_unrelated_coordinates_fail_safely(context):
    content = source({"A1": "Suma spolu", "B1": 120, "C2": 999})
    result = validate_fact("total_gross", candidate(999, "C2", [context]), content, [])
    assert result.status != "verified"
    assert not result._safe_to_suggest


@pytest.mark.parametrize("model_context", [[], ["A2"]])
def test_forged_external_header_cells_cannot_bypass_validation(model_context):
    content = source({"A2": "Suma spolu", "B2": 120, "C2": 999}, headers={"C2": ["A2"]})
    result = validate_fact("total_gross", candidate(999, "C2", model_context), content, [])
    assert result.value == Decimal("999.00")
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


def test_valid_external_header_relationship_is_preserved():
    content = source(
        {"A1": "Číslo faktúry", "B1": "Suma spolu", "A2": "X-1", "B2": 120, "A3": "X-2", "B3": 130},
        headers={"B3": ["B1"]},
    )
    result = validate_fact("total_gross", candidate(130, "B3", []), content, [])
    assert result.status == "verified"
    assert result._safe_to_suggest


@pytest.mark.parametrize("formula_cell", ["A2", "B2"])
def test_formula_label_and_formula_value_remain_rejected(formula_cell):
    content = source({"A2": "Suma spolu", "B2": 120}, formulas={formula_cell: "=1+1"})
    result = validate_fact("total_gross", candidate(120, "B2", ["A2"]), content, [])
    assert result.value is None
    assert not result._safe_to_suggest


def test_formula_is_a_competing_cell_not_an_empty_separator():
    content = source({"A2": "Suma spolu", "B2": None, "C2": 999}, formulas={"B2": "=SUM(D2:D3)"})
    result = validate_fact("total_gross", candidate(999, "C2", ["A2"]), content, [])
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


@pytest.mark.parametrize("headers", [None, {"C2": ["A2"]}])
def test_valid_adjacent_amount_survives_full_validation(settings, headers):
    content = source(
        {"A1": "Mena dokladu", "B1": "EUR", "A2": "Suma spolu", "B2": 120, "C2": 999},
        headers=headers,
    )
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, "B2", ["A2"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.total_gross.value == Decimal("120.00")
    assert data.fields.total_gross.status == "verified"
    assert data.fields.total_gross._safe_to_suggest


@pytest.mark.parametrize("row,value", [(2, 120), (3, 130)])
def test_column_headers_allow_unrecognized_neighbouring_captions(row, value):
    content = source(
        {
            "A1": "P. č.",
            "B1": "Suma spolu",
            "C1": "Poznámka",
            "A2": 1,
            "B2": 120,
            "C2": "Prvý",
            "A3": 2,
            "B3": 130,
            "C3": "Druhý",
        }
    )
    result = validate_fact("total_gross", candidate(value, f"B{row}", ["B1"]), content, [])
    assert result.status == "verified"
    assert result._safe_to_suggest


@pytest.mark.parametrize("with_headers", [False, True])
def test_distinct_table_totals_without_pages_require_review(settings, with_headers):
    content = source(
        {
            "A1": "FAKTÚRA č. XLS-1",
            "A2": "Číslo faktúry",
            "B2": "Suma spolu",
            "A3": "F-1",
            "B3": 120,
            "A4": "F-2",
            "B4": 999,
            "A6": "Mena dokladu",
            "B6": "EUR",
        },
        headers={"B3": ["B2"], "B4": ["B2"]} if with_headers else None,
    )
    content.pages = []
    assert (
        all(not c.header_cells for s in content.sheets for r in s.rows for c in r.cells)
        if not with_headers
        else True
    )
    raw = ExtractionData(document_type="invoice", classification=candidate("invoice", "A1", []))
    raw.classification.evidence.quote = "FAKTÚRA č. XLS-1"
    raw.fields.total_gross = candidate(999, "B4", ["B2"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.document_type == "invoice"
    assert data.classification.status == "verified"
    assert data.fields.total_gross.value == Decimal("999.00")
    assert data.fields.total_gross.status == "needs_review"
    assert "conflicting_values" in data.fields.total_gross.validation_reasons
    assert not data.fields.total_gross._safe_to_suggest
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    config, form, _ = loader.resolve(FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default"))
    _, tables, _ = map_extraction(data, config, form)
    assert len(tables) == 1 and len(tables[0].rows) == 1
    assert tables[0].rows[0]["suma_doklad"].value is None


@pytest.mark.parametrize("vertical", [False, True])
def test_single_total_without_header_metadata_remains_verified(settings, vertical):
    values = {"A1": "Mena dokladu", "B1": "EUR", "A3": "Suma spolu"}
    coordinate = "A4" if vertical else "B3"
    values[coordinate] = 120
    content = source(values)
    content.pages = []
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, coordinate, ["A3"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.total_gross.status == "verified"
    assert data.fields.total_gross._safe_to_suggest


def test_equal_repeated_totals_are_not_a_distinct_value_conflict(settings):
    content = source(
        {
            "A1": "Číslo faktúry",
            "B1": "Suma spolu",
            "A2": "F-1",
            "B2": 120,
            "A3": "F-1",
            "B3": 120,
            "A5": "Mena dokladu",
            "B5": "EUR",
        }
    )
    content.pages = []
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, "B3", ["B1"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.total_gross.status == "verified"
    assert "conflicting_values" not in data.fields.total_gross.validation_reasons


def test_formula_totals_do_not_become_competing_facts(settings):
    content = source(
        {
            "A1": "Číslo faktúry",
            "B1": "Suma spolu",
            "A2": "F-1",
            "B2": 120,
            "A3": "F-2",
            "B3": 999,
            "A5": "Mena dokladu",
            "B5": "EUR",
        },
        formulas={"B3": "=1000-1"},
    )
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, "B2", ["B1"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.total_gross.status == "verified"
    assert data.fields.total_gross._safe_to_suggest


@pytest.mark.parametrize("headers", [None, {"C1": ["A1"]}])
def test_unrelated_currency_cannot_become_document_currency(settings, headers):
    content = source(
        {"A1": "Mena dokladu", "B1": "Neurčená", "C1": "EUR", "A3": "Suma spolu", "B3": 120},
        headers=headers,
    )
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, "B3", ["A3"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.currency.value is None
    assert data.fields.total_gross.currency.value is None
    assert data.fields.total_gross.status == "needs_review"
    assert "currency_missing" in data.fields.total_gross.validation_reasons


@pytest.mark.parametrize("header_hint", [False, True])
def test_unrelated_currency_hint_cannot_verify_amount(settings, header_hint):
    content = source(
        {"B1": "EUR", "B2": "Účet", "A3": "Suma spolu", "B3": 120},
        headers={"B3": ["B1"]} if header_hint else None,
    )
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, "B3", ["A3"] if header_hint else ["A3", "B1"])
    data, _ = validate_extraction(raw, content, settings)
    assert data.fields.total_gross.currency.value is None
    assert data.fields.total_gross.status == "needs_review"


@pytest.mark.parametrize(
    "values,cell,context",
    [
        ({"B1": "Suma spolu", "B2": "EUR", "B3": 120}, "B3", ["B1", "B2"]),
        ({"B1": "Suma spolu", "A2": "EUR", "B2": 120}, "B2", ["B1", "A2"]),
        ({"A1": "Suma spolu", "B1": 120, "C1": "EUR"}, "B1", ["A1", "C1"]),
    ],
)
def test_structurally_related_currency_units_remain_supported(settings, values, cell, context):
    raw = ExtractionData()
    raw.fields.total_gross = candidate(120, cell, context)
    data, _ = validate_extraction(raw, source(values), settings)
    assert data.fields.total_gross.currency.value == "EUR"
    assert data.fields.total_gross.status == "verified"
