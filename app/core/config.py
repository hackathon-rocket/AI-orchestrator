from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

import configs


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_file_encoding="utf-8", validate_assignment=True
    )

    agent_api_key: SecretStr = SecretStr("")

    llm_provider: Literal["mock", "openai", "openai_compatible", "openrouter"] = "mock"
    llm_model: str = ""
    openai_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    llm_base_url: str = ""
    llm_output_mode: Literal["native", "prompted", "tool"] = "prompted"
    allow_remote_llm: bool = False
    llm_timeout_seconds: float = Field(default=30, gt=0, le=120)
    llm_max_retries: int = Field(default=2, ge=0, le=3)
    llm_retry_backoff: float = Field(default=0.2, ge=0, le=2)
    llm_max_output_tokens: int = Field(default=12_000, ge=256, le=32_000)

    max_concurrency: int = Field(default=4, ge=1, le=32)
    queue_timeout_seconds: float = Field(default=1, gt=0, le=10)
    analysis_timeout_seconds: float = Field(default=100, gt=0, le=480)
    body_timeout_seconds: float = Field(default=10, gt=0, le=60)
    max_request_bytes: int = Field(default=2_000_000, ge=1000, le=10_000_000)
    max_content_chars: int = Field(default=150_000, ge=100, le=1_000_000)
    max_pages: int = Field(default=50, ge=1, le=100)
    max_sheets: int = Field(default=10, ge=1, le=20)
    max_rows: int = Field(default=2000, ge=1, le=5000)
    max_cells: int = Field(default=10_000, ge=1, le=100_000)

    max_xlsx_bytes: int = Field(default=5_000_000, ge=1000, le=20_000_000)
    max_xlsx_expanded_bytes: int = Field(default=20_000_000, ge=1000, le=50_000_000)
    max_xlsx_entries: int = Field(default=200, ge=10, le=1000)
    max_xlsx_compression_ratio: float = Field(default=200, ge=1, le=1000)
    max_xlsx_xml_nodes: int = Field(default=150_000, ge=100, le=500_000)
    max_xlsx_concurrency: int = Field(default=2, ge=1, le=8)
    xlsx_timeout_seconds: float = Field(default=15, gt=0, le=60)

    max_form_nodes: int = Field(default=5000, ge=1, le=20_000)
    max_form_depth: int = Field(default=20, ge=1, le=50)
    amount_tolerance: Decimal = Field(default=Decimal("0.02"), ge=0, le=1)
    form_config_dir: Path = Path(configs.__file__).parent / "forms"

    @model_validator(mode="after")
    def base_url_policy(self):
        if not self.llm_base_url:
            return self

        endpoint = urlsplit(self.llm_base_url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("LLM_BASE_URL must be an HTTP(S) endpoint without credentials")
        return self

    def readiness_errors(self) -> list[str]:
        errors = []
        if len(self.agent_api_key.get_secret_value()) < 16:
            errors.append("service_token_not_configured")
        if self.llm_provider == "mock":
            return errors

        if not self.llm_model:
            errors.append("model_not_configured")
        if self.llm_provider in {"openai", "openrouter"} and not self.allow_remote_llm:
            errors.append("remote_llm_not_authorized")

        provider_key = {
            "openai": self.openai_api_key,
            "openrouter": self.openrouter_api_key,
        }.get(self.llm_provider)
        if provider_key is not None and not provider_key.get_secret_value():
            errors.append("provider_key_not_configured")

        if self.llm_provider == "openai_compatible":
            if not self.llm_base_url:
                errors.append("endpoint_not_configured")
            elif (
                urlsplit(self.llm_base_url).hostname not in {"localhost", "127.0.0.1", "::1"}
                and not self.allow_remote_llm
            ):
                errors.append("remote_llm_not_authorized")
        return errors
