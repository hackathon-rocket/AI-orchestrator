from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from app import __version__
from app.core.errors import ServiceError
from app.schemas.requests import AnalyzeRequest
from app.schemas.responses import (
    AnalyzeResponse,
    ErrorResponse,
    HealthResponse,
    PrepareXlsxResponse,
    ReadinessResponse,
    VersionResponse,
)

router = APIRouter()
bearer = HTTPBearer(auto_error=False)


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@router.get(
    "/ready", response_model=ReadinessResponse, responses={503: {"model": ReadinessResponse}}
)
async def ready(request: Request) -> JSONResponse:
    errors = request.app.state.settings.readiness_errors()
    if request.app.state.config_error:
        errors.append("invalid_mapping_configuration")
    if request.app.state.provider_error:
        errors.append("provider_configuration_error")
    response = ReadinessResponse(status="not_ready" if errors else "ready", checks=errors)
    return JSONResponse(response.model_dump(), status_code=503 if errors else 200)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(version=__version__)


@router.post(
    "/v1/analyze",
    response_model=AnalyzeResponse,
    dependencies=[Depends(bearer)],
    responses={
        status: {"model": ErrorResponse}
        for status in [401, 408, 413, 415, 422, 429, 500, 502, 503, 504]
    },
)
async def analyze(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    orchestrator = request.app.state.orchestrator
    if orchestrator is None:
        raise ServiceError("not_ready", "Služba nie je pripravená.", 503)
    return await orchestrator.analyze(payload, request.state.request_id)


@router.post(
    "/v1/prepare-xlsx",
    response_model=PrepareXlsxResponse,
    dependencies=[Depends(bearer)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
    responses={
        status: {"model": ErrorResponse} for status in [401, 408, 413, 415, 422, 429, 500, 503, 504]
    },
)
async def prepare_xlsx(request: Request) -> PrepareXlsxResponse:
    content = await request.app.state.workbooks.prepare(await request.body())
    warnings = ["Príprava zošita nevykonáva vzorce ani analýzu LLM."]
    if any(cell.formula for sheet in content.sheets for row in sheet.rows for cell in row.cells):
        warnings.append("Vzorce sú zachované iba ako text; vypočítané hodnoty nie sú dôkazom.")
    return PrepareXlsxResponse(content=content, warnings=warnings)
