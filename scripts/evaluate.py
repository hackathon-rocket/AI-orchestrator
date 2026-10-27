"""Synthetic MOCK metrics, not evidence of real LLM or OCR accuracy."""

import argparse
import asyncio
import json
import statistics
from pathlib import Path

from app.core.orchestrator import AnalysisOrchestrator
from app.llm.mock_provider import MockProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.schemas.requests import AnalyzeRequest
from scripts.evaluate_scripted import evaluate_scripted
from scripts.settings import OfflineSettings

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


async def evaluate():
    settings = OfflineSettings()
    provider = MockProvider(settings)
    orchestrator = AnalysisOrchestrator(
        settings, provider, ConfigLoader(settings.form_config_dir, FormSchemaParser())
    )
    expected_cases = json.loads((FIXTURES / "ground_truth.json").read_text(encoding="utf-8"))
    correct = present = missing_correct = missing = 0
    hallucinated = predicted = mapped_correct = mapped = 0
    durations = []
    try:
        for name, expected in expected_cases.items():
            request = AnalyzeRequest.model_validate_json(
                (FIXTURES / f"{name}.json").read_text(encoding="utf-8")
            )
            response = await orchestrator.analyze(request, "evaluation")
            result = response.model_dump(mode="json")
            durations.append(response.meta.duration_ms)
            for key, value in expected["fields"].items():
                actual = result["extracted_data"]["fields"][key]["value"]
                predicted += actual is not None
                if value is None:
                    missing += 1
                    missing_correct += actual is None
                    hallucinated += actual is not None
                else:
                    present += 1
                    correct += actual == value
            mapped_values = {field["tag"]: field["value"] for field in result["fields"]}
            for table in result["tables"]:
                if table["rows"]:
                    mapped_values.update(
                        {
                            table["tag"] + "." + tag: field["value"]
                            for tag, field in table["rows"][0].items()
                        }
                    )
            for tag, value in expected["mapped"].items():
                mapped += 1
                mapped_correct += tag in mapped_values and mapped_values[tag] == value
    finally:
        await provider.close()
    return {
        "mode": "synthetic_mock_only",
        "cases": len(expected_cases),
        "scripted_validation": await evaluate_scripted(),
        "extraction_accuracy": correct / present if present else None,
        "missing_value_accuracy": missing_correct / missing if missing else None,
        "hallucination_rate": hallucinated / predicted if predicted else 0,
        "mapping_accuracy": mapped_correct / mapped if mapped else None,
        "counts": {
            "present_fields": present,
            "missing_fields": missing,
            "predicted_non_null": predicted,
            "hallucinated_on_absent_fields": hallucinated,
            "mapped_fields": mapped,
        },
        "latency_ms": {"median": round(statistics.median(durations), 2), "max": max(durations)},
        "scope": (
            "Only explicitly annotated fields are scored; "
            "these are fixture regression metrics, not real model accuracy."
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.dumps(asyncio.run(evaluate()), ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report + "\n", encoding="utf-8")
    print(report)
