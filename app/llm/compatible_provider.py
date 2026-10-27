from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.openai import OpenAIProvider

from app.agents.extraction_agent import create_agent
from app.llm.client_provider import OpenAIClientProvider


class CompatibleProvider(OpenAIClientProvider):
    def client_options(self) -> dict[str, str]:
        router = self.settings.llm_provider == "openrouter"
        key = self.settings.openrouter_api_key if router else self.settings.llm_api_key
        return {
            "base_url": "https://openrouter.ai/api/v1" if router else self.settings.llm_base_url,
            "api_key": key.get_secret_value() or "local-no-auth",
        }

    def create_agent(self, client: AsyncOpenAI) -> Agent:
        settings = self.settings
        profile = OpenAIModelProfile(
            supports_json_schema_output=settings.llm_output_mode == "native",
            supports_json_object_output=False,
            supports_tools=settings.llm_output_mode == "tool",
        )
        model = OpenAIChatModel(
            settings.llm_model,
            profile=profile,
            provider=OpenAIProvider(openai_client=client),
        )
        return create_agent(model, settings.llm_output_mode, settings.llm_max_output_tokens)
