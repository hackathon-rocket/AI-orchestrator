import asyncio
import threading

import pytest

from app.core.errors import ServiceError
from app.core.workbooks import WorkbookService


async def test_parser_slot_remains_held_after_http_cancellation(settings, monkeypatch):
    import app.core.workbooks as module

    entered = threading.Event()
    release = threading.Event()

    def parse(*args):
        entered.set()
        release.wait(timeout=2)
        return "prepared"

    monkeypatch.setattr(module, "read_xlsx_bytes", parse)
    settings.max_xlsx_concurrency = 1
    settings.queue_timeout_seconds = 0.01
    service = WorkbookService(settings)
    first = asyncio.create_task(service.prepare(b"x"))
    try:
        await asyncio.to_thread(entered.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        with pytest.raises(ServiceError, match="busy"):
            await service.prepare(b"y")
    finally:
        release.set()
        await service.close()
    assert service.slots._value == 1


async def test_analysis_deadline_covers_cpu_phase_without_releasing_running_worker(
    settings, monkeypatch
):
    import threading

    from app.core.orchestrator import AnalysisOrchestrator
    from app.llm.mock_provider import MockProvider
    from app.mapping.config_loader import ConfigLoader
    from app.mapping.form_parser import FormSchemaParser
    from app.schemas.requests import AnalyzeRequest, Content, Page

    settings.max_concurrency = 1
    settings.analysis_timeout_seconds = 0.05
    settings.queue_timeout_seconds = 0.01
    orchestrator = AnalysisOrchestrator(
        settings, MockProvider(settings), ConfigLoader(settings.form_config_dir, FormSchemaParser())
    )
    release = threading.Event()
    original = orchestrator.postprocess

    def slow(*args):
        release.wait(timeout=2)
        return original(*args)

    monkeypatch.setattr(orchestrator, "postprocess", slow)
    request = AnalyzeRequest(
        document_id="deadline", content=Content(pages=[Page(page=1, text="FAKTÚRA č. F-1")])
    )
    try:
        with pytest.raises(ServiceError, match="analysis_timeout"):
            await orchestrator.analyze(request, "first")
        with pytest.raises(ServiceError, match="busy"):
            await orchestrator.analyze(request, "second")
    finally:
        release.set()
        await orchestrator.close()
    assert orchestrator.slots._value == 1
