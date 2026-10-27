"""Deterministic scaling checks: bound indexing work rather than wall-clock timing."""

import pytest

from app.validators.evidence import EvidenceIndex, ExcelSheetIndex
from app.validators.validation import validate_extraction
from scripts.benchmark_excel_validation import workload


@pytest.mark.parametrize("count", [200, 500, 1000])
def test_unique_relationship_queries_reuse_linear_column_runs(count):
    _, content = workload(count)
    index = EvidenceIndex(content).sheets["Sheet"]
    initial_steps = index.scan_steps
    assert initial_steps <= 3 * (count + 1)
    assert len(index.role_values["total_gross"]) == count
    for row in range(2, count + 2):
        assert index.related("B1", f"B{row}")
    assert index.scan_steps == initial_steps


@pytest.mark.parametrize("headers", [False, True])
def test_full_validation_builds_one_index_per_sheet(settings, monkeypatch, headers):
    raw, content = workload(200, headers=headers)
    built = []
    original = ExcelSheetIndex.__init__

    def tracked(self, cells):
        original(self, cells)
        built.append(self)

    monkeypatch.setattr(ExcelSheetIndex, "__init__", tracked)
    result, _ = validate_extraction(raw, content, settings)
    assert len(built) == 1
    assert built[0].scan_steps <= 3 * 201
    assert result.fields.total_gross.status == "needs_review"
    assert not result.fields.total_gross._safe_to_suggest


def test_indexes_do_not_share_document_content():
    _, first = workload(2)
    _, second = workload(2)
    second.sheets[0].rows[1].cells[1].value = 555
    a = EvidenceIndex(first).sheets["Sheet"]
    b = EvidenceIndex(second).sheets["Sheet"]
    assert a.cells["B2"].value == 999
    assert b.cells["B2"].value == 555
    assert a.targets is not b.targets
