from decimal import Decimal
from typing import Literal

from pydantic import Field, PrivateAttr, field_validator, model_validator

from app.schemas.common import Model

DocumentType = Literal["invoice", "receipt", "contract", "bank_statement", "unknown"]


class CellLocator(Model):
    sheet: str = Field(min_length=1, max_length=100)
    cell: str = Field(pattern=r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
    # Optional semantic context, e.g. B1's column header for a value at B2.
    context_cells: list[str] = Field(default_factory=list, max_length=8)


class Evidence(Model):
    page: int | None = Field(default=None, ge=1)
    quote: str = Field(min_length=1, max_length=4000)
    locator: CellLocator | None = None

    @model_validator(mode="after")
    def one_source(self):
        if self.page is not None and self.locator is not None:
            raise ValueError("Evidence must address one source")
        return self


class CurrencyResolution(Model):
    value: str | None = None
    status: Literal["verified", "needs_review", "not_found"] = "not_found"
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    reasons: list[str] = Field(default_factory=list, max_length=20)


class Fact(Model):
    value: str | Decimal | None = None
    status: Literal["verified", "needs_review", "not_found"] = "not_found"
    evidence: Evidence | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    source_kind: Literal["document", "calculated"] = "document"
    validation_reasons: list[str] = Field(default_factory=list, max_length=20)
    currency: CurrencyResolution | None = None
    # These decisions are server-only. Neither a provider nor an API caller can set them.
    _safe_to_suggest: bool = PrivateAttr(default=False)
    _role_supported: bool = PrivateAttr(default=False)

    @field_validator("value", mode="before")
    @classmethod
    def no_bool(cls, value):
        if isinstance(value, bool):
            raise ValueError("Boolean is not a document value")
        return value

    @model_validator(mode="after")
    def missing_policy(self):
        if self.value is None:
            object.__setattr__(self, "status", "not_found")
            object.__setattr__(self, "confidence", 0.0)
            object.__setattr__(self, "evidence", None)
        elif self.status == "not_found":
            object.__setattr__(self, "status", "needs_review")
        return self


class UniversalFields(Model):
    invoice_number: Fact = Field(default_factory=Fact)
    receipt_number: Fact = Field(default_factory=Fact)
    contract_number: Fact = Field(default_factory=Fact)
    supplier_name: Fact = Field(default_factory=Fact)
    supplier_ico: Fact = Field(default_factory=Fact)
    supplier_dic: Fact = Field(default_factory=Fact)
    supplier_vat_id: Fact = Field(default_factory=Fact)
    customer_name: Fact = Field(default_factory=Fact)
    customer_ico: Fact = Field(default_factory=Fact)
    customer_dic: Fact = Field(default_factory=Fact)
    customer_vat_id: Fact = Field(default_factory=Fact)
    issue_date: Fact = Field(default_factory=Fact)
    due_date: Fact = Field(default_factory=Fact)
    delivery_date: Fact = Field(default_factory=Fact)
    payment_date: Fact = Field(default_factory=Fact)
    total_net: Fact = Field(default_factory=Fact)
    total_vat: Fact = Field(default_factory=Fact)
    total_gross: Fact = Field(default_factory=Fact)
    paid_amount: Fact = Field(default_factory=Fact)
    currency: Fact = Field(default_factory=Fact)
    iban: Fact = Field(default_factory=Fact)
    payment_method: Fact = Field(default_factory=Fact)
    expense_description: Fact = Field(default_factory=Fact)
    contract_subject: Fact = Field(default_factory=Fact)
    contract_date: Fact = Field(default_factory=Fact)
    contract_amount: Fact = Field(default_factory=Fact)
    statement_period_start: Fact = Field(default_factory=Fact)
    statement_period_end: Fact = Field(default_factory=Fact)
    opening_balance: Fact = Field(default_factory=Fact)
    closing_balance: Fact = Field(default_factory=Fact)


class LineItem(Model):
    description: Fact = Field(default_factory=Fact)
    quantity: Fact = Field(default_factory=Fact)
    unit: Fact = Field(default_factory=Fact)
    unit_price: Fact = Field(default_factory=Fact)
    net: Fact = Field(default_factory=Fact)
    vat: Fact = Field(default_factory=Fact)
    gross: Fact = Field(default_factory=Fact)


class Transaction(Model):
    date: Fact = Field(default_factory=Fact)
    description: Fact = Field(default_factory=Fact)
    amount: Fact = Field(default_factory=Fact)
    reference: Fact = Field(default_factory=Fact)


class ExtractionData(Model):
    document_type: DocumentType = Field(
        default="unknown",
        description="Canonical document type; must agree with classification.value.",
    )
    classification: Fact = Field(
        default_factory=Fact,
        description=(
            "Document classification fact: value must be a canonical type "
            "(invoice, receipt, contract, bank_statement), never a translated title "
            "or document number. "
            "Quote the exact source heading including its type words and give its original page "
            "or cell locator. Use null/not_found when no reliable heading supports a type."
        ),
    )
    fields: UniversalFields = Field(default_factory=UniversalFields)
    line_items: list[LineItem] = Field(default_factory=list, max_length=200)
    transactions: list[Transaction] = Field(default_factory=list, max_length=200)
