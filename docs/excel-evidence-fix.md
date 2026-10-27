# Task 1 — Excel evidence attribution

Follow-up: [remaining conflict/performance defects A and B](excel-evidence-followup.md).

26 October 2026. Initial Task 1 status: **FIXED AND VERIFIED**.

## Scoped changes

- `app/validators/evidence.py`: `excel_label_relationship()` verifies source geometry. `quote_support()` only passes structurally justified `context_cells`/`header_cells` to semantic validation. Existing coordinate, literal quote and formula checks remain in place.
- `app/validators/validation.py`: conflict detection uses the same relationship check for supplied header metadata, preventing forged headers from downgrading a legitimate value.
- `tests/unit/test_excel_evidence_relationships.py`: 32 positive and negative regression cases. Existing BBSK mapping is used without a substitute configuration.

No public API/extraction schemas, Excel processor, providers, dependencies or mapping restrictions changed for this task. No Laravel/React changes or paid model calls.

## Reproduction

Given `A1=Mena dokladu`, `B1=EUR`, `A2=Suma spolu`, `B2=120`, `C2=999`, a proposal for C2 with context A2 was previously verified and safe to suggest. The first two regression tests failed before the patch, including full validation with real BBSK configuration.

After the patch, the literal 999.00 remains available as `needs_review`, `_safe_to_suggest=False`, and BBSK `suma_doklad` remains null. The test asserts a real invoice table row with `typ=FA`, so success is not caused by disabling classification or mapping. Correct B2=120 still passes full validation, including when C2 carries forged header metadata.

## Verification

Commands ran from the project directory with UV_PROJECT_ENVIRONMENT pointing to the task's existing isolated environment. Fresh scratch cache/temp directories avoid inherited Windows cache permissions.

| Command | Result |
|---|---|
| `uv run --no-sync pytest tests/unit/test_excel_evidence_relationships.py --basetemp ../../work/excel-attribution-final-regressions -o cache_dir=../../work/excel-attribution-final-regressions-cache --tb=short` | 32 passed, 0.17 s |
| `uv run --no-sync pytest tests/unit/ --basetemp ../../work/excel-attribution-unit -o cache_dir=../../work/excel-attribution-unit-cache --tb=short` | 129 passed, 2.84 s |
| `uv run --no-sync pytest --basetemp ../../work/excel-attribution-full -o cache_dir=../../work/excel-attribution-full-cache --tb=short` | Python 3.13: 226 passed, 5.10 s |
| `../../work/.venv312/Scripts/python.exe -m pytest --basetemp ../../work/excel-attribution-py312 -o cache_dir=../../work/excel-attribution-py312-cache --tb=short` | Python 3.12: 226 passed, 5.45 s |
| `uv run --no-sync ruff check .` | Passed |
| `uv run --no-sync ruff format --check .` | 65 files formatted |
| `../.venv/Scripts/python.exe -m scripts.smoke_http` from `work/excel-attribution-smoke` | Passed: authenticated analysis, dynamic mapping, XLSX preparation and analysis all 200; unauthenticated analysis 401 |

The smoke test ran against an isolated source copy to avoid rewriting unrelated generated examples in the deliverable. Windows Application Control initially blocked the Python 3.12 `pytest` launcher (error 4551); executing the same tests via `python -m pytest` succeeded. There are no remaining failing tests. Real paid provider calls were not attempted or required.

## Coverage and conservative limits

Tests preserve horizontal and vertical pairs, empty separator cells, column headers with repeated data rows, multiple header rows, currency-unit subheaders, unknown neighbouring captions, multi-letter column coordinates and original evidence metadata. Negative cases cover competing values, unrelated/nonexistent/invalid references, forged external headers, intervening sections/headers and formula cells.

Geometry is conservative and cannot prove arbitrary visual layouts without merged-cell/table-range metadata. Ambiguous single-column continuations, detached regions, right-side labels and complex mixed header/data rows can remain reviewable instead of verified. The original Excel file must remain available for human inspection. The fix does not infer missing layout metadata or make the LLM's coordinates authoritative.
