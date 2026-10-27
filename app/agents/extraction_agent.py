from pathlib import Path

from pydantic_ai import Agent, NativeOutput, PromptedOutput, ToolOutput
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits

from app.agents.proposal import ExtractionProposal
from app.schemas.extraction import ExtractionData
from app.schemas.requests import Content

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")


def create_agent(model: Model, mode: str, max_tokens: int) -> Agent:
    output_type = {
        "native": NativeOutput(ExtractionProposal, strict=True),
        "prompted": PromptedOutput(ExtractionProposal),
        "tool": ToolOutput(ExtractionProposal, strict=True),
    }[mode]
    return Agent(
        model,
        output_type=output_type,
        system_prompt=SYSTEM_PROMPT,
        retries=0,
        model_settings={"max_tokens": max_tokens},
        name="document_extraction",
    )


async def run_agent(agent: Agent, content: Content) -> ExtractionData:
    result = await agent.run(
        "UNTRUSTED_DOCUMENT_JSON\n" + content.model_dump_json(),
        usage_limits=UsageLimits(request_limit=1, tool_calls_limit=0),
    )
    # The provider-only schema must not escape into the validated response model.
    return ExtractionData.model_validate(result.output.model_dump())
