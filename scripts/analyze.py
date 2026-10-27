"""Analyze local JSON/OCR exports/XLSX using mock mode only."""

import argparse
import asyncio
import json
from pathlib import Path

from app.core.orchestrator import AnalysisOrchestrator
from app.llm.mock_provider import MockProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.processors.excel import read_xlsx
from app.schemas.requests import AnalyzeRequest, Content, FormContext, Page
from scripts.settings import OfflineSettings


def read_json(path: Path, limit: int):
    if path.stat().st_size > limit:
        raise ValueError("Input exceeds configured size limit")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def from_ocr_export(raw: dict) -> Content:
    # No guess about vendor-specific raw nesting. Adapt new shapes explicitly in Laravel.
    if isinstance(raw.get("pages"), list) and raw["pages"]:
        return Content(pages=[Page.model_validate(p) for p in raw["pages"]])
    if isinstance(raw.get("text"), str):
        return Content(pages=[Page(page=None, text=raw["text"])])
    raise ValueError("Expected pages[{page,text}] or a top-level text string")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="AnalyzeRequest JSON")
    source.add_argument("--ocr-json", type=Path, help="Export containing pages or text")
    source.add_argument("--xlsx", type=Path)
    parser.add_argument("--document-id", default="local-demo")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    settings = OfflineSettings()

    if args.input:
        request = AnalyzeRequest.model_validate(read_json(args.input, settings.max_request_bytes))
    else:
        if args.xlsx:
            content = read_xlsx(args.xlsx, settings)
        else:
            ocr_export = read_json(args.ocr_json, settings.max_request_bytes)
            content = from_ocr_export(ocr_export)

        request = AnalyzeRequest(
            document_id=args.document_id,
            content=content,
            form=FormContext(id="bbsk_11824", mapping_id="bbsk_11824_default"),
        )

    provider = MockProvider(settings)
    try:
        orchestrator = AnalysisOrchestrator(
            settings, provider, ConfigLoader(settings.form_config_dir, FormSchemaParser())
        )
        result = await orchestrator.analyze(request, "local-cli")
        serialized = result.model_dump_json(indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
        else:
            print(serialized)
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())
