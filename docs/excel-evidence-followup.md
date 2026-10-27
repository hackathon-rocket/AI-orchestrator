# Task 1 follow-up — Excel conflicts and indexing

Status: **FIXED AND VERIFIED**. Only issues A/B were addressed.

## Root causes

A: Global conflict detection read supplied `header_cells` plus a legacy A/B row convention. A valid LLM citation B4→B2 did not cause B3→B2 to be discovered when metadata was absent. It therefore confused a valid row-level amount with a unique document total.

B: Each label/value relationship rebuilt occupied cells and rescanned the whole sheet and the column prefix. Repeating this for distinct rows caused quadratic or worse work, even when all values used the same header.

## Changes

- `app/validators/evidence.py`: request-local `ExcelSheetIndex` prepares cell coordinates, occupied rows/columns, normalized labels, adjacent cells and structural header runs. `EvidenceIndex` holds one such index per sheet. Header runs are traversed once and reused for distinct value queries; known semantic headers terminate each other's runs. `quote_support()` uses the shared index. `excel_label_relationship()` remains a compatible helper; request paths pass an existing index.
- `app/validators/validation.py`: global conflicts use all source-derived values associated with each semantic role, independent of optional header metadata. Distinct totals retain the original candidate/evidence with `needs_review`, `conflicting_values` and `_safe_to_suggest=False`. Existing mapper protections then withhold the amount.
- `tests/unit/test_excel_evidence_relationships.py`: exact supplied example with no pages, both with/without headers, real BBSK mapping, single horizontal/vertical totals, repeated equal values and formula exclusion.
- `tests/unit/test_excel_index_scaling.py`: deterministic operation-count checks at 200/500/1000 rows; one index per sheet during complete validation; no content sharing between requests. All distinct row queries reuse prepared runs without additional scans.
- `scripts/benchmark_excel_validation.py`: reproducible offline timing workload. Input construction is outside the measured full-validation call.

No API/extraction schemas, providers, Excel processor, Laravel, React, dependencies or protected mapping rules were modified. There are no global caches of document content. Classification and number parsing rules were not changed.

## Before/after

The exact missing-metadata regression failed before the fix: 999.00 was `verified`. Its metadata-present counterpart already passed. Both now return review-required, unsafe-to-suggest totals and a real BBSK invoice row with `suma_doklad=null`.

Measured locally on Windows/Python 3.13.0 using the same three-column workload with header metadata; one timed run per size per revision:

| Data rows | Before, seconds | After, seconds |
|---|---:|---:|
| 200 | 0.579738 | 0.073372 |
| 500 | 3.272833 | 0.180928 |
| 1000 | 16.945518 | 0.426872 |

The 1000-row run is about 40 times faster. Timings are observations, not CI thresholds. The deterministic scaling test bounds actual column visits and verifies index reuse, so it does not depend on machine speed. [Raw measurements](examples/excel_validation_benchmark.json).

## Executed checks

Commands ran in the project using the existing isolated Python environment (`UV_PROJECT_ENVIRONMENT=../../work/.venv`, resolved to an absolute path):

- Before patch: `python -m pytest tests/unit/test_excel_evidence_relationships.py -k distinct_table --basetemp ../../work/excel-ab-red -o cache_dir=../../work/excel-ab-red-cache --tb=short`: one expected failure without metadata, one pass with metadata.
- `uv run --no-sync pytest tests/unit/ --basetemp ../../work/excel-ab-units -o cache_dir=../../work/excel-ab-units-cache --tb=short`: **141 passed in 1.97 s**.
- `uv run --no-sync pytest --basetemp ../../work/excel-ab-full -o cache_dir=../../work/excel-ab-full-cache --tb=short`: **238 passed in 6.61 s**, including all original 32 Excel relationship regressions and relevant Excel/API/mapping tests. No remaining failures.
- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync ruff format --check .`: 68 files formatted.
- `python -m scripts.benchmark_excel_validation --output ../../work/excel-benchmark-before.json` before edits, and `uv run --no-sync python -m scripts.benchmark_excel_validation --output ../../work/excel-benchmark-after-final.json` after edits: results above.
- `../.venv/Scripts/python.exe -m scripts.smoke_http` from an isolated copy at `work/excel-ab-smoke`: health/readiness/docs, authenticated analysis, dynamic mapping, XLSX preparation/analysis passed; unauthenticated analysis returned 401. The isolated copy avoids rewriting unrelated example outputs.

No paid LLM/network model calls were needed or made.

## Remaining limitations

Structural association does not establish which of several document rows the caller intended to analyze. Distinct document-level totals require review; multi-document registers should be split upstream. Equal repeated amounts are not considered a distinct-value conflict, but do not by themselves prove document identity. Complex merged layouts, detached regions, unknown labels or ambiguous section boundaries may still abstain. Original source evidence and the human confirmation boundary remain intact.
