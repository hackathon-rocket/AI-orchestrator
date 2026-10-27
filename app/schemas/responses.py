from typing import Literal

from pydantic import Field

from app.schemas.common import Model
from app.schemas.extraction import CellLocator, DocumentType, ExtractionData
from app.schemas.requests import Content

FieldType = Literal["string", "number", "currency", "date", "ico", "dic", "iban"]


class ExtractedField(Model):
    tag: str
    label: str
    type: FieldType
    value: str | float | None
    confidence: float = Field(ge=0, le=1)
    evidence: str | None = None
    page: int | None = None
    status: Literal["verified", "needs_review", "not_found"] = "not_found"
    validation_reasons: list[str] = Field(default_factory=list)
    source_locator: CellLocator | None = None


class TableColumn(Model):
    tag: str
    label: str
    type: FieldType


class ExtractedTable(Model):
    tag: str
    label: str
    columns: list[TableColumn]
    rows: list[dict[str, ExtractedField]]


class Metadata(Model):
    schema_version: Literal["1.0"] = "1.0"
    provider: str
    model: str
    request_id: str
    duration_ms: float
    mapping_id: str | None = None
    schema_source: Literal["request", "configured", "none"] = "none"


class AnalyzeResponse(Model):
    document_id: str
    status: Literal["completed"] = "completed"
    document_type: DocumentType
    fields: list[ExtractedField]
    tables: list[ExtractedTable]
    warnings: list[str]
    requires_review: Literal[True] = True
    extracted_data: ExtractionData | None = None
    meta: Metadata


class ErrorDetail(Model):
    code: str
    message: str
    request_id: str


class PrepareXlsxResponse(Model):
    content: Content
    warnings: list[str] = Field(default_factory=list)


class ErrorResponse(Model):
    error: ErrorDetail


class HealthResponse(Model):
    status: Literal["ok"] = "ok"


class ReadinessResponse(Model):
    status: Literal["ready", "not_ready"]
    checks: list[str]


class VersionResponse(Model):
    version: str
    schema_version: Literal["1.0"] = "1.0"
