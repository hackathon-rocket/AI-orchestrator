import asyncio
from typing import Protocol

import httpx
import openai
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UserError

from app.core.config import Settings
from app.core.errors import ProviderError
from app.schemas.extraction import ExtractionData
from app.schemas.requests import Content


class LLMProvider(Protocol):
    name: str
    model_name: str

    async def extract(self, content: Content) -> ExtractionData: ...
    async def close(self) -> None: ...


class BoundedProvider:
    """The only retry loop. SDK and Pydantic AI retries are disabled."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.name = settings.llm_provider
        self.model_name = settings.llm_model or "mock"

    async def attempt(self, content: Content) -> ExtractionData:
        raise NotImplementedError

    async def extract(self, content: Content) -> ExtractionData:
        for attempt in range(self.settings.llm_max_retries + 1):
            try:
                async with asyncio.timeout(self.settings.llm_timeout_seconds):
                    return ExtractionData.model_validate(await self.attempt(content))
            except (TimeoutError, httpx.TimeoutException, openai.APITimeoutError):
                error = ProviderError("provider_timeout", 504)
            except (UnexpectedModelBehavior, ValidationError):
                error = ProviderError("provider_invalid_output")
            except UserError:
                raise ProviderError("provider_configuration_error", 503) from None
            except (ModelHTTPError, openai.APIStatusError) as exc:
                if exc.status_code in {401, 403}:
                    raise ProviderError("provider_auth_error", 502) from None
                if exc.status_code == 404:
                    raise ProviderError("provider_configuration_error", 503) from None
                if exc.status_code != 429 and exc.status_code < 500:
                    body = getattr(exc, "body", None)
                    detail = body.get("error", body) if isinstance(body, dict) else {}
                    if (
                        isinstance(detail, dict)
                        and detail.get("param")
                        in ("response_format", "text.format", "tools", "tool_choice")
                        and detail.get("code")
                        in ("unsupported_parameter", "unsupported_value", "unsupported_feature")
                    ):
                        raise ProviderError("provider_unsupported_mode", 502) from None
                    raise ProviderError("provider_rejected_request") from None
                error = ProviderError(
                    "provider_rate_limited" if exc.status_code == 429 else "provider_unavailable",
                    503,
                )
            except (httpx.TransportError, openai.APIConnectionError):
                error = ProviderError("provider_unavailable", 503)
            if attempt == self.settings.llm_max_retries:
                raise error from None
            await asyncio.sleep(self.settings.llm_retry_backoff * 2**attempt)
        raise ProviderError()

    async def close(self):
        pass
