"""Currency comes from explicit document evidence, never locale or destination type."""

import re

from app.processors.ocr_text import normalize
from app.schemas.extraction import CellLocator, CurrencyResolution, Evidence, ExtractionData, Fact
from app.schemas.requests import Content
from app.validators.evidence import EvidenceIndex, ExcelSheetIndex
from app.validators.semantics import MONEY_FIELDS

CURRENCY_PATTERN = re.compile(r"\b(?:EUR|USD|CZK|GBP|CHF|PLN|HUF)\b|€", re.I)


def codes(text: str) -> set[str]:
    return {"EUR" if token == "€" else token.upper() for token in CURRENCY_PATTERN.findall(text)}


def _source_evidence(
    text: str, *, page: int | None = None, locator: CellLocator | None = None
) -> list[Evidence]:
    if not codes(text):
        return []
    if len(text) <= 4000:
        return [Evidence(page=page, quote=text, locator=locator)]
    # Long OCR lines/cells exceed the citation limit. Keep an original snippet for
    # every currency so shortening evidence cannot hide a conflicting currency.
    snippets = {
        match[0].upper(): text[max(0, match.start() - 80) : match.end() + 80]
        for match in CURRENCY_PATTERN.finditer(text)
    }
    return [Evidence(page=page, quote=quote, locator=locator) for quote in snippets.values()]


def _unit_supports(sheet: ExcelSheetIndex, unit: str, value: str) -> bool:
    """A hinted unit must be adjacent or inside the amount's actual column run."""
    if sheet.cells[unit].formula or sheet.text[unit] not in sheet.units:
        return False
    if sheet.right.get(unit) == value or sheet.right.get(value) == unit:
        return True
    unit_column, unit_row = sheet.positions[unit]
    value_column, value_row = sheet.positions[value]
    return (
        unit_column == value_column
        and unit_row < value_row
        and any(
            unit in targets and value in targets
            for label, targets in sheet.targets.items()
            if sheet.roles[label] & MONEY_FIELDS
        )
    )


def resolve_currencies(
    data: ExtractionData, content: Content, warnings: list[str], index: EvidenceIndex
) -> None:
    explicit_currency_evidence: list[Evidence] = []
    total_currency_evidence: list[Evidence] = []
    for page in content.pages:
        for line in page.text.splitlines():
            if re.match(
                r"^(?:mena(?: dokladu)?|currency|document currency)\s*:", normalize(line)
            ) or re.match(r"^vsetky sumy su uvedene v\b", normalize(line)):
                explicit_currency_evidence.extend(_source_evidence(line, page=page.page))
        for entry in index.observed[page.page]:
            if entry.role == "total_gross" and codes(entry.value):
                total_currency_evidence.extend(_source_evidence(entry.quote, page=page.page))

    for sheet_name, sheet in index.sheets.items():
        currency_cells = sheet.role_values.get("currency", set())
        for coordinate, cell in sheet.cells.items():
            if coordinate in currency_cells and codes(str(cell.value)):
                explicit_currency_evidence.extend(
                    _source_evidence(
                        str(cell.value),
                        locator=CellLocator(sheet=sheet_name, cell=coordinate),
                    )
                )
    document_evidence = explicit_currency_evidence or total_currency_evidence
    document_codes = set().union(*(codes(evidence.quote) for evidence in document_evidence))
    if data.fields.currency.value is None and len(document_codes) == 1:
        fact = Fact(
            value=next(iter(document_codes)),
            evidence=document_evidence[0],
            confidence=0.85,
            status="verified",
        )
        fact._safe_to_suggest = fact._role_supported = True
        data.fields.currency = fact
    elif len(document_codes) > 1 and data.fields.currency.value is not None:
        data.fields.currency.status = "needs_review"
        data.fields.currency._safe_to_suggest = False
        data.fields.currency.validation_reasons = ["currency_conflict"]

    records = [data.fields, *data.line_items, *data.transactions]
    for record in records:
        for key in type(record).model_fields:
            fact = getattr(record, key)
            if key not in MONEY_FIELDS or fact.value is None:
                continue
            local_evidence = [fact.evidence] if fact.evidence and codes(fact.evidence.quote) else []
            if fact.evidence and fact.evidence.locator:
                locator = fact.evidence.locator
                sheet = index.sheets[locator.sheet]
                cells = sheet.cells
                value_cell = cells.get(locator.cell)
                context_cells = locator.context_cells + (
                    value_cell.header_cells if value_cell else []
                )
                for coordinate in dict.fromkeys(context_cells):
                    cell = cells.get(coordinate)
                    if cell and _unit_supports(sheet, coordinate, locator.cell):
                        local_evidence.append(
                            Evidence(
                                quote=str(cell.value),
                                locator=CellLocator(sheet=locator.sheet, cell=coordinate),
                            )
                        )
            evidence = local_evidence + (
                explicit_currency_evidence if local_evidence else document_evidence
            )
            observed_codes = set().union(*(codes(item.quote) for item in evidence))
            if len(observed_codes) == 1:
                fact.currency = CurrencyResolution(
                    value=next(iter(observed_codes)), status="verified", evidence=evidence[:100]
                )
            else:
                reason = "currency_conflict" if observed_codes else "currency_missing"
                fact.currency = CurrencyResolution(
                    status="needs_review" if observed_codes else "not_found",
                    evidence=evidence[:100],
                    reasons=[reason],
                )
                fact.status = "needs_review"
                fact.confidence = min(fact.confidence, 0.5)
                fact.validation_reasons = list(dict.fromkeys([*fact.validation_reasons, reason]))
                if observed_codes:
                    fact._safe_to_suggest = False
                warnings.append(
                    f"{key}: mena chýba alebo obsahuje rozporné údaje; skontrolujte originál."
                )
