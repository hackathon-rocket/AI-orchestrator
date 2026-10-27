"""Evaluate a small, explicitly synthetic set of independent raw model proposals."""

import asyncio
import json
import statistics
from pathlib import Path

from app.core.orchestrator import AnalysisOrchestrator
from app.llm.base import BoundedProvider
from app.mapping.config_loader import ConfigLoader
from app.mapping.form_parser import FormSchemaParser
from app.schemas.extraction import ExtractionData
from app.schemas.requests import AnalyzeRequest
from scripts.settings import OfflineSettings

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/scripted_cases.json"


class ScriptedProvider(BoundedProvider):
    def __init__(self, settings, raw):
        super().__init__(settings)
        self.raw = raw
        self.model_name = "scripted-synthetic-proposals"

    async def attempt(self, content):
        return ExtractionData.model_validate(self.raw)


async def evaluate_scripted():
    settings = OfflineSettings()
    loader = ConfigLoader(settings.form_config_dir, FormSchemaParser())
    cases = json.loads(await asyncio.to_thread(FIXTURE.read_text, encoding="utf-8"))
    counts = dict(
        annotated_fields=0,
        expected_present=0,
        predicted_present=0,
        verified_correct=0,
        reviewable_correct=0,
        incorrectly_extracted=0,
        false_positives=0,
        false_rejections=0,
        correct_missing=0,
        status_correct=0,
        mapping_correct=0,
        mapping_total=0,
    )
    durations = []
    for case in cases:
        provider = ScriptedProvider(settings, case["raw"])
        orchestrator = AnalysisOrchestrator(settings, provider, loader)
        request = AnalyzeRequest.model_validate(
            {
                "document_id": case["name"],
                "content": case["content"],
                "form": {"id": "bbsk_11824", "mapping_id": "bbsk_11824_default"},
            }
        )
        result = await orchestrator.analyze(request, "synthetic-evaluation")
        data = result.model_dump(mode="json")
        durations.append(result.meta.duration_ms)
        for key, expected in case["expected"].items():
            actual = data["extracted_data"]["fields"][key]
            counts["annotated_fields"] += 1
            counts["expected_present"] += expected["value"] is not None
            counts["predicted_present"] += actual["value"] is not None
            counts["status_correct"] += actual["status"] == expected["status"]
            if actual["value"] is None:
                outcome = "correct_missing" if expected["value"] is None else "false_rejections"
                counts[outcome] += 1
            elif actual["value"] != expected["value"]:
                counts["incorrectly_extracted"] += 1
                counts["false_positives"] += expected["value"] is None
            elif actual["status"] == "verified":
                counts["verified_correct"] += 1
            elif actual["status"] == "needs_review":
                counts["reviewable_correct"] += 1
        mapped = {
            tag: field["value"]
            for table in data["tables"]
            for row in table["rows"]
            for tag, field in row.items()
        }
        for tag, expected in case["mapped"].items():
            counts["mapping_total"] += 1
            counts["mapping_correct"] += tag in mapped and mapped[tag] == expected
        await provider.close()
    return {
        "mode": "synthetic_scripted_raw_proposals_only",
        "cases": len(cases),
        "counts": counts,
        "correct_retention_coverage": (counts["verified_correct"] + counts["reviewable_correct"])
        / counts["expected_present"],
        "false_rejection_rate": counts["false_rejections"] / counts["expected_present"],
        "incorrect_prediction_rate": counts["incorrectly_extracted"]
        / max(counts["predicted_present"], 1),
        "status_accuracy": counts["status_correct"] / counts["annotated_fields"],
        "mapping_accuracy": counts["mapping_correct"] / counts["mapping_total"],
        "latency_ms": {"median": round(statistics.median(durations), 2), "max": max(durations)},
        "scope": (
            "Only annotated synthetic fields. Scripted proposals test deterministic validation, "
            "not model recall or real OCR quality."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(evaluate_scripted()), ensure_ascii=False, indent=2))
