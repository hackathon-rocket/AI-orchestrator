import asyncio
from time import perf_counter

from app.core.config import Settings
from app.core.errors import ServiceError
from app.llm.base import LLMProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.mapper import map_extraction
from app.observability.logging import event
from app.processors.ocr_text import validate_content_limits
from app.schemas.requests import AnalyzeRequest
from app.schemas.responses import AnalyzeResponse, MappingStatus, Metadata
from app.validators.validation import validate_extraction


class AnalysisOrchestrator:
    def __init__(self, settings: Settings, provider: LLMProvider, loader: ConfigLoader):
        self.settings = settings
        self.provider = provider
        self.loader = loader
        self.slots = asyncio.Semaphore(settings.max_concurrency)
        self.processing_tasks = set()

    def postprocess(self, raw, content, config, form):
        data, warnings = validate_extraction(raw, content, self.settings)
        mapping_status: MappingStatus
        if config:
            fields, tables, mapping_warnings = map_extraction(data, config, form)
            warnings.extend(mapping_warnings)
            if data.document_type not in config.document_types:
                mapping_status = "unsupported_document_type"
            elif any(field.value is not None for field in fields) or any(
                cell.value is not None
                for table in tables
                for row in table.rows
                for cell in row.values()
            ):
                mapping_status = "mapped"
            else:
                mapping_status = "no_values"
        else:
            fields = []
            tables = []
            warnings.append("Cieľový formulár nebol zadaný; údaje nie sú mapované.")
            mapping_status = "no_form"
        return data, warnings, fields, tables, mapping_status

    def _processing_finished(self, task: asyncio.Task) -> None:
        self.processing_tasks.discard(task)
        self.slots.release()
        if not task.cancelled():
            task.exception()  # Consume errors even if the HTTP caller has left.

    async def close(self) -> None:
        if self.processing_tasks:
            await asyncio.gather(*self.processing_tasks, return_exceptions=True)

    async def analyze(self, request: AnalyzeRequest, request_id: str) -> AnalyzeResponse:
        started_at = perf_counter()
        validate_content_limits(request.content, self.settings)

        config = None
        form = None
        schema_source = "none"
        resolved = None
        if request.form:
            resolved = self.loader.resolve_with_metadata(request.form)
            config, form, schema_source = resolved.config, resolved.form, resolved.schema_source

        event(request_id, "input", document_id=request.document_id, provider=self.provider.name)
        try:
            await asyncio.wait_for(self.slots.acquire(), self.settings.queue_timeout_seconds)
        except TimeoutError:
            raise ServiceError(
                "busy", "Služba je vyťažená. Skúste požiadavku zopakovať.", 429
            ) from None

        processing = None
        try:
            async with asyncio.timeout(self.settings.analysis_timeout_seconds):
                raw = await self.provider.extract(request.content)
                event(
                    request_id,
                    "extraction",
                    document_id=request.document_id,
                    provider=self.provider.name,
                )
                processing = asyncio.create_task(
                    asyncio.to_thread(self.postprocess, raw, request.content, config, form)
                )
                self.processing_tasks.add(processing)
                processing.add_done_callback(self._processing_finished)
                data, warnings, fields, tables, mapping_status = await asyncio.shield(processing)
                event(
                    request_id,
                    "validation",
                    document_id=request.document_id,
                    provider=self.provider.name,
                )
        except TimeoutError:
            raise ServiceError(
                "analysis_timeout", "Časový limit analýzy bol prekročený.", 504
            ) from None
        finally:
            # Once started, the worker owns its slot until completion, even after cancellation.
            if processing is None:
                self.slots.release()

        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        event(
            request_id,
            "completed",
            document_id=request.document_id,
            provider=self.provider.name,
            duration_ms=duration_ms,
        )
        return AnalyzeResponse(
            document_id=request.document_id,
            document_type=data.document_type,
            mapping_status=mapping_status,
            fields=fields,
            tables=tables,
            warnings=list(dict.fromkeys(warnings)),
            extracted_data=data if request.options.include_unmapped else None,
            meta=Metadata(
                provider=self.provider.name,
                model=self.provider.model_name,
                request_id=request_id,
                duration_ms=duration_ms,
                mapping_id=config.id if config else None,
                schema_source=schema_source,
                mapping_version=resolved.mapping_version if resolved else None,
                mapping_fingerprint=resolved.mapping_fingerprint if resolved else None,
                form_schema_fingerprint=resolved.form_schema_fingerprint if resolved else None,
                authoritative_constraints_fingerprint=(
                    resolved.authoritative_constraints_fingerprint if resolved else None
                ),
            ),
        )
