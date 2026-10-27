from app.core.errors import ServiceError
from app.llm.compatible_provider import CompatibleProvider
from app.llm.mock_provider import MockProvider
from app.llm.openai_provider import OpenAIProvider


def create_provider(settings):
    if settings.readiness_errors():
        raise ServiceError("not_ready", "Služba nie je nakonfigurovaná.", 503)
    if settings.llm_provider == "mock":
        return MockProvider(settings)
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    return CompatibleProvider(settings)
