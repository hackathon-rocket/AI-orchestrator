"""Offline, explicit labelled-text fixture recognizer; not generic document intelligence."""

import re

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from app.agents.extraction_agent import create_agent, run_agent
from app.llm.base import BoundedProvider
from app.processors.ocr_text import normalize
from app.schemas.extraction import (
    CellLocator,
    Evidence,
    ExtractionData,
    Fact,
    LineItem,
    Transaction,
)
from app.schemas.requests import Content
from app.validators.evidence import heading_types
from app.validators.semantics import LABELS


def candidate(value: str, evidence: Evidence) -> Fact:
    return Fact(value=value, evidence=evidence, status="verified", confidence=0.8)


def extract_label(line: str, labels: list[str]) -> str | None:
    for label in sorted(labels, key=len, reverse=True):
        # Diacritics/whitespace normalization is for comparison only; preserve source value.
        match = re.match(
            r"^" + re.escape(normalize(label)) + r"(?:\s*:\s*|\s+)(.+)$", normalize(line)
        )
        if match:
            # Splitting by label length works for ordinary fixture strings including Slovak accents.
            return line.strip()[len(label) :].lstrip(" :")
    return None


def _classify(data: ExtractionData, text: str, evidence: Evidence) -> None:
    for kind in heading_types(text):
        if data.document_type == "unknown":
            data.document_type = kind
            data.classification = candidate(kind, evidence)
        elif data.document_type != kind:
            data.classification.status = "needs_review"


def _fill_record(record: LineItem | Transaction, line: str, evidence: Evidence) -> None:
    for segment in line.split("|"):
        for key in type(record).model_fields:
            value = extract_label(segment.strip(), LABELS[key])
            if value:
                setattr(record, key, candidate(value, evidence))


def fixture_extract(content: Content) -> ExtractionData:
    data = ExtractionData()
    known_headers = {
        normalize(label).rstrip(" :") for labels in LABELS.values() for label in labels
    }
    for page in content.pages:
        for line in page.text.splitlines():
            if not line.strip():
                continue
            evidence = Evidence(page=page.page, quote=line)
            _classify(data, line, evidence)
            if normalize(line).startswith("polozka:"):
                record = LineItem()
                _fill_record(record, line, evidence)
                data.line_items.append(record)
                continue
            if normalize(line).startswith("datum transakcie:"):
                record = Transaction()
                _fill_record(record, line, evidence)
                data.transactions.append(record)
                continue
            for key in type(data.fields).model_fields:
                value = extract_label(line, LABELS[key])
                if value and getattr(data.fields, key).value is None:
                    setattr(data.fields, key, candidate(value, evidence))
    # Demo workbook convention: A-column labels, B-column values (one document per request).
    for sheet in content.sheets:
        source_cells = {cell.coordinate: cell for row in sheet.rows for cell in row.cells}
        for cell in source_cells.values():
            if (
                cell.formula
                or cell.value is None
                or normalize(str(cell.value)).rstrip(" :") in known_headers
            ):
                continue
            evidence = Evidence(
                quote=str(cell.value),
                locator=CellLocator(
                    sheet=sheet.name, cell=cell.coordinate, context_cells=cell.header_cells
                ),
            )
            _classify(data, str(cell.value), evidence)
            for header_key in cell.header_cells:
                header = source_cells.get(header_key)
                if not header or header.formula:
                    continue
                for key in type(data.fields).model_fields:
                    if (
                        normalize(str(header.value)) in map(normalize, LABELS[key])
                        and getattr(data.fields, key).value is None
                    ):
                        setattr(data.fields, key, candidate(str(cell.value), evidence))
        for row in sheet.rows:
            label_cell = source_cells.get(f"A{row.row}")
            value_cell = source_cells.get(f"B{row.row}")
            if (
                not label_cell
                or not value_cell
                or value_cell.value is None
                or value_cell.formula
                or normalize(str(value_cell.value)).rstrip(" :") in known_headers
            ):
                continue
            evidence = Evidence(
                quote=str(value_cell.value),
                locator=CellLocator(
                    sheet=sheet.name,
                    cell=value_cell.coordinate,
                    context_cells=[label_cell.coordinate],
                ),
            )
            for key, labels in LABELS.items():
                if key not in type(data.fields).model_fields:
                    continue
                if normalize(str(label_cell.value)) in map(normalize, labels):
                    setattr(data.fields, key, candidate(str(value_cell.value), evidence))
    return data


class MockProvider(BoundedProvider):
    def __init__(self, settings):
        super().__init__(settings)
        self.name = "mock"
        self.model_name = "mock"

    async def attempt(self, content: Content) -> ExtractionData:
        output = fixture_extract(content)

        async def respond(messages, info):
            return ModelResponse(parts=[TextPart(output.model_dump_json())])

        agent = create_agent(
            FunctionModel(respond, model_name="mock"),
            "prompted",
            self.settings.llm_max_output_tokens,
        )
        return await run_agent(agent, content)
