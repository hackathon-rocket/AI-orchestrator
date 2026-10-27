import re
from decimal import Decimal
from itertools import chain

from app.core.config import Settings
from app.schemas.extraction import ExtractionData, Fact
from app.schemas.requests import Content
from app.validators.currency import resolve_currencies
from app.validators.dates import parse_date
from app.validators.evidence import (
    EvidenceIndex,
    heading_types,
    literal_support,
    primary_heading_types,
    quote_support,
    role_support,
)
from app.validators.financial import amounts_balance, parse_money
from app.validators.identifiers import normalize_dic, normalize_iban, normalize_ico
from app.validators.semantics import DATE_FIELDS, LABELS, MONEY_FIELDS, canonical_classification

CLASSIFICATION_REASONS = {
    "classification_missing": "Model neposkytol klasifikáciu dokumentu.",
    "classification_not_document": "Klasifikácia nemá priamy zdroj v dokumente.",
    "classification_evidence_missing": "Pri klasifikácii chýba citácia zdroja.",
    "classification_value_invalid": "Hodnota klasifikácie nie je podporovaný typ dokumentu.",
    "classification_unknown": "Model uviedol neurčený typ dokumentu.",
    "classification_evidence_mismatch": "Citácia sa nenašla na uvedenej strane alebo v bunke.",
    "classification_formula": "Vzorec bunky nemôže dokazovať typ dokumentu.",
    "classification_heading_missing": "Citovaný zdroj neobsahuje rozpoznaný nadpis dokumentu.",
    "classification_heading_mismatch": "Nadpis zdroja nepodporuje navrhnutý typ dokumentu.",
    "classification_quote_not_heading": "Citácia nezasahuje do slov nadpisu určujúcich typ.",
    "classification_quote_ambiguous": "Citácia sa vyskytuje aj mimo podporujúceho nadpisu.",
    "classification_evidence_spacing": "Medzery v citácii neumožnili overiť polohu nadpisu.",
    "classification_conflict": "Zdroj obsahuje nadpisy viacerých typov dokumentov.",
    "classification_document_type_mismatch": "Klasifikácia a document_type modelu sa nezhodujú.",
}


def classification_warning(reason: str) -> str:
    # Fixed codes/messages only: no rejected value, quote or provider-supplied reason.
    return f"Typ dokumentu [{reason}]: {CLASSIFICATION_REASONS[reason]}"


def validate_classification(
    fact: Fact, content: Content, warnings: list[str], index: EvidenceIndex | None = None
) -> Fact:
    def reject(reason: str) -> Fact:
        warnings.append(classification_warning(reason))
        return Fact(validation_reasons=[reason])

    if fact.value is None:
        return reject("classification_missing")
    if fact.source_kind != "document":
        return reject("classification_not_document")
    if fact.evidence is None:
        return reject("classification_evidence_missing")
    canonical = canonical_classification(str(fact.value))
    if canonical is None:
        return reject("classification_value_invalid")
    if canonical == "unknown":
        return reject("classification_unknown")
    support = quote_support(fact.evidence, content, index)
    if not support.matched:
        return reject("classification_evidence_mismatch")
    if support.formula:
        return reject("classification_formula")
    role = role_support("classification", canonical, fact.evidence, support)
    if role != "supported":
        headings = (
            heading_types(fact.evidence.quote)
            if fact.evidence.locator
            else primary_heading_types(support.source)
        )
        if not headings:
            return reject("classification_heading_missing")
        if canonical not in headings:
            return reject("classification_heading_mismatch")
        if support.approximate:
            return reject("classification_evidence_spacing")
        return reject(
            "classification_quote_ambiguous"
            if role == "ambiguous"
            else "classification_quote_not_heading"
        )
    reasons = ["provider_uncertainty"] if fact.status == "needs_review" else []
    if reasons:
        warnings.append("Typ dokumentu [provider_uncertainty]: model vyžaduje kontrolu typu.")
    result = Fact(
        value=canonical,
        evidence=fact.evidence,
        status="needs_review" if reasons else "verified",
        confidence=min(fact.confidence, 0.5 if reasons else 0.85),
        validation_reasons=reasons,
    )
    result._role_supported = True
    result._safe_to_suggest = not reasons
    return result


def normalize_value(key: str, value: str | Decimal) -> str | Decimal:
    if key in MONEY_FIELDS:
        return parse_money(value)
    if key in DATE_FIELDS:
        return parse_date(str(value))
    if key.endswith("_ico"):
        return normalize_ico(str(value))
    if key.endswith("_dic"):
        return normalize_dic(str(value))
    if key.endswith("_vat_id"):
        result = re.sub(r"\s", "", str(value)).upper()
        if not re.fullmatch(r"SK\d{10}", result):
            raise ValueError("Unsupported VAT ID")
        return result
    if key == "iban":
        return normalize_iban(str(value))
    if key == "quantity":
        result = Decimal(str(value).replace(",", "."))
        if not result.is_finite() or abs(result) > Decimal("1000000000"):
            raise ValueError("Invalid quantity")
        return result
    if key == "currency" and str(value) not in {"EUR", "USD", "CZK", "GBP", "CHF", "PLN", "HUF"}:
        raise ValueError("Unsupported currency")
    return str(value).strip()


def validate_fact(
    key: str, fact: Fact, content: Content, warnings: list[str], index: EvidenceIndex | None = None
) -> Fact:
    if key == "classification":
        return validate_classification(fact, content, warnings, index)
    if fact.value is None:
        return Fact()
    label = LABELS.get(key, ["typ dokumentu"])[0]
    if fact.source_kind != "document" or fact.evidence is None:
        warnings.append(f"{label}: hodnota nemá priamy dôkaz a bola odmietnutá.")
        return Fact()
    support = quote_support(fact.evidence, content, index)
    if not support.matched or support.formula:
        warnings.append(f"{label}: zdrojový dôkaz sa nepodarilo overiť.")
        return Fact()
    role = role_support(key, fact.value, fact.evidence, support)
    grounded = literal_support(key, fact.value, fact.evidence.quote)
    if not grounded:
        warnings.append(f"{label}: hodnota sa nenachádza v citovanom dôkaze.")
        return Fact()
    if role == "conflicting" and (key in DATE_FIELDS or key in MONEY_FIELDS):
        warnings.append(f"{label}: zdroj uvádza inú významovú rolu; hodnota bola odmietnutá.")
        return Fact()
    try:
        value = normalize_value(key, fact.value)
    except (ValueError, ArithmeticError):
        warnings.append(f"{label}: neplatný alebo nejednoznačný formát; skontrolujte originál.")
        return Fact(
            value=fact.value,
            evidence=fact.evidence,
            status="needs_review",
            confidence=min(fact.confidence, 0.3),
            validation_reasons=["invalid_format"],
        )
    reasons = []
    if role != "supported":
        reasons.append("role_conflict" if role == "conflicting" else "role_ambiguous")
    if support.approximate:
        reasons.append("ocr_spacing")
    if fact.status == "needs_review":
        reasons.append("provider_uncertainty")
    if reasons:
        warnings.append(f"{label}: návrh vyžaduje kontrolu významu alebo kvality OCR.")
    result = Fact(
        value=value,
        evidence=fact.evidence,
        status="needs_review" if reasons else "verified",
        confidence=min(fact.confidence, 0.5 if reasons else 0.85),
        validation_reasons=reasons,
    )
    result._role_supported = role == "supported"
    # Ambiguous harmless text may be suggested. Sensitive semantic roles need positive support.
    sensitive = (
        key in MONEY_FIELDS | DATE_FIELDS
        or key.endswith(("_ico", "_dic", "_vat_id"))
        or key == "iban"
    )
    result._safe_to_suggest = role == "supported" or (role == "ambiguous" and not sensitive)
    return result


def validate_extraction(
    raw: ExtractionData, content: Content, settings: Settings
) -> tuple[ExtractionData, list[str]]:
    data = raw.model_copy(deep=True)
    index = EvidenceIndex(content)
    warnings: list[str] = []
    data.classification = validate_fact(
        "classification", data.classification, content, warnings, index
    )
    headings = set().union(*(primary_heading_types(p.text) for p in content.pages))
    for sheet in content.sheets:
        for row in sheet.rows:
            for cell in row.cells:
                if cell.value is not None and not cell.formula:
                    headings.update(heading_types(str(cell.value)))
    if len(headings) > 1 and data.classification.value is not None:
        data.classification.status = "needs_review"
        data.classification.validation_reasons = ["classification_conflict"]
        data.classification._safe_to_suggest = False
        warnings.append(classification_warning("classification_conflict"))
    if data.classification.value is not None and data.classification.value != data.document_type:
        data.classification.status = "needs_review"
        data.classification._safe_to_suggest = False
        data.classification.validation_reasons.append("classification_document_type_mismatch")
        warnings.append(classification_warning("classification_document_type_mismatch"))
    if data.classification.status != "verified" or data.classification.value != data.document_type:
        data.document_type = "unknown"
        warnings.append("Typ dokumentu nebol spoľahlivo určený.")
    for record in [data.fields, *data.line_items, *data.transactions]:
        for key in type(record).model_fields:
            setattr(record, key, validate_fact(key, getattr(record, key), content, warnings, index))
    # Never pick a convenient first value among conflicting labelled source values.
    for key, labels in LABELS.items():
        if key not in type(data.fields).model_fields:
            continue
        fact = getattr(data.fields, key)
        if fact.value is None:
            continue
        page_values = (
            entry.value
            for entries in index.observed.values()
            for entry in entries
            if entry.role == key
        )
        # Include every structurally associated cell, even without header hints.
        cell_values = (
            str(sheet.cells[coordinate].value)
            for sheet in index.sheets.values()
            for coordinate in sheet.role_values.get(key, ())
        )
        observed = set()
        for value in chain(page_values, cell_values):
            try:
                observed.add(str(normalize_value(key, value)))
            except (ValueError, ArithmeticError):
                observed.add(value)
        if len(observed) > 1:
            fact.status = "needs_review"
            fact.confidence = min(fact.confidence, 0.3)
            fact._safe_to_suggest = False
            fact.validation_reasons.append("conflicting_values")
            warnings.append(f"{labels[0]}: dokument obsahuje rozporné hodnoty.")
    fields = data.fields
    if all(getattr(fields, k)._role_supported for k in ["total_net", "total_vat", "total_gross"]):
        if not amounts_balance(
            fields.total_net.value,
            fields.total_vat.value,
            fields.total_gross.value,
            settings.amount_tolerance,
        ):
            warnings.append("Súčet základu a DPH sa nezhoduje s celkovou sumou.")
            for k in ["total_net", "total_vat", "total_gross"]:
                fact = getattr(fields, k)
                fact.status = "needs_review"
                fact.confidence = min(fact.confidence, 0.4)
                fact._safe_to_suggest = False
                fact.validation_reasons.append("arithmetic_conflict")
    resolve_currencies(data, content, warnings, index)
    expected = {
        "invoice": ["invoice_number", "supplier_name", "total_gross", "due_date", "payment_date"],
        "receipt": ["receipt_number", "total_gross"],
        "contract": ["contract_number", "contract_subject"],
        "bank_statement": ["iban", "statement_period_start", "statement_period_end"],
    }
    for key in expected.get(data.document_type, []):
        if getattr(fields, key).value is None:
            warnings.append(
                f"{LABELS[key][0]}: údaj sa v dodanom obsahu nenašiel alebo nebol overený."
            )
    if content.pages and any(p.page is None for p in content.pages):
        warnings.append("OCR nemá rozdelenie na strany; čísla strán nie sú dostupné.")
    if any(c.formula for s in content.sheets for r in s.rows for c in r.cells):
        warnings.append("Vzorce zošita sa nevykonávajú a nie sú priamym dôkazom hodnôt.")
    if any(
        re.search(
            r"ignore.{0,30}instructions|system prompt|api[_ ]?key|ignoruj.{0,30}pokyny",
            p.text,
            re.I,
        )
        for p in content.pages
    ):
        warnings.append("Dokument obsahuje text podobný pokynom; obsah sa spracúva iba ako údaje.")
    warnings.append("Výsledok je návrh. Pred použitím skontrolujte údaje a pôvodný dokument.")
    return data, list(dict.fromkeys(warnings))
