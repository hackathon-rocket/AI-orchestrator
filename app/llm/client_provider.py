"""Shared client lifecycle for providers using the OpenAI SDK."""

import httpx
from openai import AsyncOpenAI
from pydantic_ai import Agent

from app.agents.extraction_agent import run_agent
from app.core.config import Settings
from app.llm.base import BoundedProvider
from app.schemas.extraction import ExtractionData
from app.schemas.requests import Content


class OpenAIClientProvider(BoundedProvider):
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__(settings)
        self.transport = transport
        self.client: AsyncOpenAI | None = None
        self.agent: Agent | None = None

    def client_options(self) -> dict[str, str]:
        raise NotImplementedError

    def create_agent(self, client: AsyncOpenAI) -> Agent:
        raise NotImplementedError

    async def initialize(self) -> Agent:
        if self.agent is not None:
            return self.agent
        http = httpx.AsyncClient(
            transport=self.transport,
            timeout=self.settings.llm_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )
        try:
            client = AsyncOpenAI(http_client=http, max_retries=0, **self.client_options())
            agent = self.create_agent(client)
        except BaseException:
            await http.aclose()
            raise
        self.client, self.agent = client, agent
        return agent

    async def attempt(self, content: Content) -> ExtractionData:
        agent = await self.initialize()
        return await run_agent(agent, content)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
            self.client = self.agent = None
