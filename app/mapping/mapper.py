from decimal import Decimal

from app.mapping.form_parser import FormField, ParsedForm
from app.schemas.extraction import ExtractionData, Fact
from app.schemas.mapping import InlineMapping
from app.schemas.responses import ExtractedField, ExtractedTable, TableColumn
from app.validators.dates import parse_date
from app.validators.financial import parse_money
from app.validators.identifiers import normalize_iban


def ui_field(target: FormField, fact: Fact) -> ExtractedField:
    return ExtractedField(
        tag=target.tag if target.table else target.path,
        label=target.label,
        type=target.frontend_type,
        value=float(fact.value) if isinstance(fact.value, Decimal) else fact.value,
        confidence=fact.confidence,
        evidence=fact.evidence.quote if fact.evidence else None,
        page=fact.evidence.page if fact.evidence else None,
        status=fact.status,
        validation_reasons=fact.validation_reasons,
        source_locator=fact.evidence.locator if fact.evidence else None,
    )


def map_extraction(
    data: ExtractionData, config: InlineMapping, form: ParsedForm
) -> tuple[list[ExtractedField], list[ExtractedTable], list[str]]:
    if data.document_type not in config.document_types:
        return [], [], ["Mapovanie nie je určené pre rozpoznaný typ dokumentu."]

    fields: list[ExtractedField] = []
    rows: dict[str, dict[str, ExtractedField]] = {}
    warnings: list[str] = []

    for rule in config.rules:
        target = form.fields[rule.destination]
        if not target.editable:
            warnings.append(
                f"Pole {target.label}: chránený alebo nepodporovaný cieľ bol vynechaný."
            )
            continue

        source = (
            data.classification
            if rule.source == "classification"
            else getattr(data.fields, rule.source)
        )
        fact = source.model_copy(deep=True) if source._safe_to_suggest else Fact()
        if source.value is not None and not source._safe_to_suggest:
            warnings.append(
                f"Pole {target.label}: zdroj neprešiel bezpečnostnou validáciou mapovania."
            )

        if fact.value is not None and rule.required_currency:
            if (
                source.currency is None
                or source.currency.status != "verified"
                or source.currency.value != rule.required_currency
            ):
                fact = Fact()
                warnings.append(f"Pole {target.label}: mena chýba alebo nezodpovedá mapovaniu.")
        if fact.value is not None and rule.review_warning:
            warnings.append(rule.review_warning)
        if source.status == "needs_review":
            warnings.append(
                f"Pole {target.label}: návrh vyžaduje kontrolu "
                f"({', '.join(source.validation_reasons)})."
            )

        if fact.value is not None and rule.value_map is not None:
            mapped_value = rule.value_map.get(str(fact.value))
            if mapped_value is None:
                fact = Fact()
                warnings.append(f"Pole {target.label}: pre hodnotu chýba povolené mapovanie.")
            else:
                fact.value = mapped_value

        if fact.value is not None:
            try:
                if target.options and str(fact.value) not in target.options:
                    raise ValueError("Invalid select")
                if target.frontend_type in {"number", "currency"}:
                    fact.value = parse_money(fact.value)
                elif target.frontend_type == "date":
                    fact.value = parse_date(str(fact.value))
                elif target.frontend_type == "iban":
                    fact.value = normalize_iban(str(fact.value))
                elif target.frontend_type == "string":
                    fact.value = str(fact.value)
            except (ValueError, ArithmeticError):
                warnings.append(f"Pole {target.label}: hodnota nemá povolený typ alebo kľúč.")
                fact = Fact()

        if fact.value is None and target.required:
            warnings.append(f"Povinné pole {target.label} ostáva prázdne.")
        cell = ui_field(target, fact)
        if target.table:
            rows.setdefault(target.table, {})[target.tag] = cell
        else:
            fields.append(cell)

    for path in config.reserved:
        target = form.fields[path]
        warnings.append(f"Pole {target.label} musí doplniť alebo potvrdiť používateľ.")
        if target.table in rows and target.editable:
            rows[target.table][target.tag] = ui_field(target, Fact())

    tables = []
    for path, row in rows.items():
        table = form.tables[path]
        columns = [
            TableColumn(tag=cell.tag, label=cell.label, type=cell.type) for cell in row.values()
        ]
        has_values = any(cell.value is not None for cell in row.values())
        # One accounting document produces at most one table row.
        tables.append(
            ExtractedTable(
                tag=path,
                label=table.label,
                columns=columns,
                rows=[row] if has_values else [],
            )
        )
    return fields, tables, warnings
