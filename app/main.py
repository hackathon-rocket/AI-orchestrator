from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app import __version__
from app.api.middleware import RequestGuard
from app.api.routes import router
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.orchestrator import AnalysisOrchestrator
from app.core.workbooks import WorkbookService
from app.llm.base import LLMProvider
from app.llm.factory import create_provider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.observability.logging import configure_logging, event


def create_app(settings: Settings | None = None, provider: LLMProvider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        # Importing the app must not load local credentials or validate deployment settings.
        active_settings = settings if settings is not None else Settings()
        configure_logging()
        application.state.settings = active_settings
        application.state.config_error = False
        application.state.provider_error = False
        application.state.workbooks = WorkbookService(active_settings)
        application.state.orchestrator = None
        active_provider = provider
        try:
            loader = ConfigLoader(
                active_settings.form_config_dir,
                FormSchemaParser(active_settings.max_form_nodes, active_settings.max_form_depth),
            )
        except ServiceError:
            application.state.config_error = True
            loader = None
        if loader and not active_settings.readiness_errors():
            try:
                active_provider = active_provider or create_provider(active_settings)
                application.state.orchestrator = AnalysisOrchestrator(
                    active_settings, active_provider, loader
                )
            except Exception:
                application.state.provider_error = True
        async with AsyncExitStack() as cleanup:
            if active_provider is not None:
                cleanup.push_async_callback(active_provider.close)
            if application.state.orchestrator is not None:
                cleanup.push_async_callback(application.state.orchestrator.close)
            cleanup.push_async_callback(application.state.workbooks.close)
            yield

    application = FastAPI(
        title="eGrant AI Agent",
        version=__version__,
        lifespan=lifespan,
        description="Human-review proposals. No eGrant writes or confirmation.",
    )
    application.add_middleware(RequestGuard)
    application.include_router(router)

    @application.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError):
        event(
            request.state.request_id,
            "failed",
            provider=request.app.state.settings.llm_provider,
            error=exc.code,
        )
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status_code,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return await service_error(
            request, ServiceError("invalid_request", "Požiadavka má neplatný formát.")
        )

    @application.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return await service_error(
            request, ServiceError("http_error", "Požiadavka nie je podporovaná.", exc.status_code)
        )

    return application


app = create_app()
