import re
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from app.schemas.common import Model
from app.schemas.mapping import InlineMapping

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]


class Page(Model):
    # null denotes a single unpaginated OCR export; never invent physical page numbers.
    page: int | None = Field(default=None, ge=1, le=100000)
    text: str = Field(max_length=200000)


class Cell(Model):
    coordinate: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    value: str | int | float | None
    formula: str | None = Field(default=None, max_length=10000)
    header_cells: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("header_cells")
    @classmethod
    def valid_headers(cls, values):
        if len(values) != len(set(values)) or any(
            not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,6}", value) for value in values
        ):
            raise ValueError("Invalid header coordinates")
        return values

    @field_validator("value", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Boolean cells must be explicitly converted to text")
        return value


class SheetRow(Model):
    row: int = Field(ge=1, le=1048576)
    cells: list[Cell] = Field(max_length=1000)

    @model_validator(mode="after")
    def coordinates_match(self):
        coordinates = [cell.coordinate for cell in self.cells]
        if len(coordinates) != len(set(coordinates)) or any(
            int(re.search(r"\d+", coordinate).group()) != self.row for coordinate in coordinates
        ):
            raise ValueError("Duplicate or inconsistent cell coordinates")
        return self


class Sheet(Model):
    name: str = Field(min_length=1, max_length=100)
    rows: list[SheetRow] = Field(max_length=5000)

    @model_validator(mode="after")
    def unique_rows(self):
        row_numbers = [row.row for row in self.rows]
        if len(set(row_numbers)) != len(row_numbers):
            raise ValueError("Duplicate rows")
        return self


class Content(Model):
    language: str = Field(default="sk", max_length=50)
    pages: list[Page] = Field(default_factory=list, max_length=100)
    sheets: list[Sheet] = Field(default_factory=list, max_length=20)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value):
        if not isinstance(value, str):
            raise ValueError("Language must be a string")
        aliases = {
            "slk": "sk",
            "eng": "en",
            "ces": "cs",
            "cze": "cs",
            "deu": "de",
            "hun": "hu",
            "pol": "pl",
            "ukr": "uk",
            "fra": "fr",
        }
        parts = value.strip().lower().split("+")
        if len(parts) > 5 or any(
            not re.fullmatch(r"[a-z]{2}|[a-z]{3}", part.strip()) for part in parts
        ):
            raise ValueError("Invalid OCR language list")
        result = [aliases.get(part.strip(), part.strip()) for part in parts]
        if any(len(part) != 2 for part in result):
            raise ValueError("Unsupported OCR language code")
        return "+".join(dict.fromkeys(result))

    @model_validator(mode="after")
    def sources(self):
        page_numbers = [page.page for page in self.pages]
        sheet_names = [sheet.name for sheet in self.sheets]
        if len(page_numbers) != len(set(page_numbers)) or len(sheet_names) != len(set(sheet_names)):
            raise ValueError("Duplicate source identifiers")
        if None in page_numbers and len(page_numbers) > 1:
            raise ValueError("Unpaginated text cannot be mixed with numbered pages")
        if not any(page.text.strip() for page in self.pages) and not any(
            cell.value is not None and str(cell.value).strip()
            for sheet in self.sheets
            for row in sheet.rows
            for cell in row.cells
        ):
            raise ValueError("Empty content")
        return self


class FormContext(Model):
    id: Identifier
    schema_: dict | None = Field(default=None, alias="schema")
    mapping_id: Identifier
    mapping: InlineMapping | None = None


class AnalysisOptions(Model):
    include_unmapped: bool = True


class AnalyzeRequest(Model):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "document_id": "doc-123",
                    "content": {
                        "language": "sk",
                        "pages": [{"page": 1, "text": "FAKTÚRA č. FA-001\nSuma spolu: 120,00 EUR"}],
                        "sheets": [],
                    },
                    "form": {
                        "id": "bbsk_11824",
                        "schema": None,
                        "mapping_id": "bbsk_11824_default",
                    },
                    "options": {"include_unmapped": True},
                }
            ]
        }
    }
    document_id: Identifier
    content: Content
    form: FormContext | None = None
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)
