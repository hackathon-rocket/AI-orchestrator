from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider as PydanticOpenAIProvider

from app.agents.extraction_agent import create_agent
from app.llm.client_provider import OpenAIClientProvider


class OpenAIProvider(OpenAIClientProvider):
    def client_options(self) -> dict[str, str]:
        return {
            "api_key": self.settings.openai_api_key.get_secret_value(),
            "base_url": "https://api.openai.com/v1",
        }

    def create_agent(self, client: AsyncOpenAI) -> Agent:
        model = OpenAIResponsesModel(
            self.settings.llm_model, provider=PydanticOpenAIProvider(openai_client=client)
        )
        agent = create_agent(model, "native", self.settings.llm_max_output_tokens)
        agent.model_settings = {
            "max_tokens": self.settings.llm_max_output_tokens,
            "openai_store": False,
        }
        return agent
