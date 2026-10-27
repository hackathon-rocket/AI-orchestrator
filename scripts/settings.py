"""Deterministic settings for offline tools, independent of local credentials/configuration."""

from typing import Literal

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.core.config import Settings


class OfflineSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)
    llm_provider: Literal["mock"] = "mock"
    allow_remote_llm: Literal[False] = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)
