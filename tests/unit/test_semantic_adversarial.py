import pytest

from app.schemas.extraction import CellLocator, Evidence, Fact
from app.schemas.requests import Cell, Content, Page, Sheet, SheetRow
from app.validators.validation import validate_fact


@pytest.mark.parametrize(
    "text,quote",
    [
        ("Invoice is mentioned in this paragraph.", "Invoice"),
        ("Poznámka: faktúra sa nachádza v prílohe.", "faktúra"),
        ("Faktúra bola uhradená podľa zmluvy.", "Faktúra"),
    ],
)
def test_classification_requires_actual_heading(text, quote):
    source = Content(pages=[Page(page=1, text=text)])
    fact = Fact(
        value="invoice", status="verified", confidence=0.8, evidence=Evidence(page=1, quote=quote)
    )
    assert validate_fact("classification", fact, source, []).value is None


def test_two_column_party_blocks():
    text = "Dodávateľ:                 Odberateľ:\nLipa                       Most\nIČO: 12 345 679             IČO: 87 654 326"
    source = Content(pages=[Page(page=1, text=text)])
    for key, value, quote in [
        ("supplier_ico", "12345679", "IČO: 12 345 679"),
        ("customer_ico", "87654326", "IČO: 87 654 326"),
    ]:
        fact = Fact(
            value=value, status="verified", confidence=0.8, evidence=Evidence(page=1, quote=quote)
        )
        result = validate_fact(key, fact, source, [])
        assert result.status == "verified"


def test_shared_identifier_is_ambiguous_without_section_in_quote():
    text = "Dodávateľ:\nLipa\nIČO: 12345679\nOdberateľ:\nMost\nIČO: 12345679"
    source = Content(pages=[Page(page=1, text=text)])
    fact = Fact(
        value="12345679",
        status="verified",
        confidence=0.8,
        evidence=Evidence(page=1, quote="IČO: 12345679"),
    )
    result = validate_fact("supplier_ico", fact, source, [])
    assert result.status == "needs_review"
    assert not result._safe_to_suggest


@pytest.mark.parametrize("context", ["Z999", "bogus", "B3"])
def test_bad_cell_context_is_rejected(context):
    source = Content(
        sheets=[
            Sheet(
                name="Sheet",
                rows=[
                    SheetRow(
                        row=1,
                        cells=[
                            Cell(coordinate="A1", value="Suma spolu"),
                            Cell(coordinate="B1", value=120),
                        ],
                    )
                ],
            )
        ]
    )
    fact = Fact(
        value="120",
        status="verified",
        confidence=0.8,
        evidence=Evidence(
            quote="120", locator=CellLocator(sheet="Sheet", cell="B1", context_cells=[context])
        ),
    )
    assert validate_fact("total_gross", fact, source, []).value is None


def test_formula_header_cannot_prove_role():
    source = Content(
        sheets=[
            Sheet(
                name="Sheet",
                rows=[
                    SheetRow(
                        row=1,
                        cells=[
                            Cell(coordinate="A1", value="Suma spolu", formula='="Suma spolu"'),
                            Cell(coordinate="B1", value=120),
                        ],
                    )
                ],
            )
        ]
    )
    fact = Fact(
        value="120",
        status="verified",
        confidence=0.8,
        evidence=Evidence(
            quote="120", locator=CellLocator(sheet="Sheet", cell="B1", context_cells=["A1"])
        ),
    )
    assert validate_fact("total_gross", fact, source, []).value is None
