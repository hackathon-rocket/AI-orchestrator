import pytest
from openpyxl import Workbook

from app.core.errors import ServiceError
from app.processors.excel import read_xlsx
from app.processors.ocr_text import validate_content_limits
from app.schemas.requests import Cell, Content, Page, Sheet, SheetRow


def test_xlsx_retains_coordinates_and_does_not_evaluate_formulas(settings, tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.title = "Doklad"
    sheet["A1"] = "Číslo faktúry"
    sheet["B1"] = "XLS-001"
    sheet["A2"] = "Suma spolu"
    sheet["B2"] = 120.5
    sheet["B3"] = "=B2*2"
    path = tmp_path / "synthetic.xlsx"
    book.save(path)
    book.close()
    content = read_xlsx(path, settings)
    assert content.sheets[0].name == "Doklad"
    assert content.sheets[0].rows[1].cells[1].coordinate == "B2"
    assert content.sheets[0].rows[1].cells[1].value == 120.5
    assert content.sheets[0].rows[2].cells[0].value is None
    assert content.sheets[0].rows[2].cells[0].formula == "=B2*2"


def test_numeric_zero_counts_toward_content_limit(settings):
    settings.max_content_chars = 100
    content = Content(
        pages=[Page(page=1, text="x" * 100)],
        sheets=[
            Sheet(name="Sheet", rows=[SheetRow(row=1, cells=[Cell(coordinate="A1", value=0)])])
        ],
    )
    with pytest.raises(ServiceError, match="content_too_large"):
        validate_content_limits(content, settings)
