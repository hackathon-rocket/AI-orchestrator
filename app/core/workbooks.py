"""Bound outstanding parser threads even when HTTP callers cancel or time out."""

import asyncio

from app.core.config import Settings
from app.core.errors import ServiceError
from app.processors.excel import read_xlsx_bytes
from app.schemas.requests import Content


class WorkbookService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.slots = asyncio.Semaphore(settings.max_xlsx_concurrency)
        self.tasks: set[asyncio.Task[Content]] = set()

    async def prepare(self, payload: bytes) -> Content:
        try:
            await asyncio.wait_for(self.slots.acquire(), self.settings.queue_timeout_seconds)
        except TimeoutError:
            raise ServiceError("busy", "Služba je zaneprázdnená.", 429) from None
        task = asyncio.create_task(asyncio.to_thread(read_xlsx_bytes, payload, self.settings))
        self.tasks.add(task)

        def finished(done: asyncio.Task[Content]) -> None:
            self.tasks.discard(done)
            self.slots.release()
            if not done.cancelled():
                done.exception()  # Consume errors after an abandoned HTTP request.

        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.settings.xlsx_timeout_seconds)
        except TimeoutError:
            raise ServiceError(
                "workbook_timeout", "Časový limit spracovania zošita bol prekročený.", 504
            ) from None

    async def close(self) -> None:
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
