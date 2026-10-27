# Task 2 — OCR supplier/customer section context

## A. Implementation Summary

Only `app/validators/evidence.py` production code changed: `observations()` now invalidates inherited party context and pending label/value associations at explicit unrelated section captions. `_party_section_boundary()` recognizes a small set of Slovak captions plus decorated/explicit section markers; uppercase alone never establishes a boundary. Company suffixes protect names such as `SEKCIA S.R.O.` from structural-marker detection.

Column state remains independent. Party headings establish text offsets; a standalone indented line can be associated with the right column instead of being mistaken for the left. A boundary alongside another column resets only the affected column. A standalone heading at the block's left edge clears both columns conservatively. Blank lines alone do not clear party ownership. Ambiguous identifiers keep their literal value/evidence and existing review/suggestion safeguards.

## B. Root Cause

Unknown headings previously neither cleared `sections` nor reliably invalidated `pending`. Generic IČO/DIČ/IČ DPH labels consequently inherited the last supplier/customer role after the document changed sections. Clearing a pending value alone would not fix the persistent party context.

The initial test run reproduced seven false verifications: the exact bank-heading example plus both parties across all three identifier types. One full-pipeline test already passed before the change because the two different identifiers triggered the independent conflict check. An additional full-pipeline case without an earlier identifier now verifies that safety does not depend on that incidental conflict.

## C. Regression Tests

All new cases are in `tests/unit/test_ocr_party_sections.py` (42 parameterized cases). Existing two-column/shared-identifier tests also remain unchanged.

| Scenario | Before | After | Test |
|---|---|---|---|
| Original `BANKOVÉ SPOJENIE` → unrelated IČO | Reproduced `verified` | `needs_review`, `_safe_to_suggest=False`, observation `ambiguous_ico` | `test_original_bank_heading_stops_supplier_context` |
| Both parties, IČO / DIČ / IČ DPH | Six reproduced false verifications | All six preserve literal values for review | `test_section_transition_covers_both_parties_and_all_identifiers` |
| Known/structural headings, blank lines and OCR spacing | Expanded coverage; not all variants separately measured before | No stale inheritance in tested variants | `test_heading_variants_end_inheritance` |
| Names, addresses and uppercase company suffixes | Existing functionality retained; added controls | Valid attribution remains verified | `test_names_addresses_and_blank_lines_preserve_party`, `test_company_suffix_prevents_structural_marker_false_positive` |
| Two columns, boundary in one column | Existing two-column behavior plus new boundary cases | Unaffected party stays verified | `test_two_column_boundary_only_resets_affected_column`, `test_indented_right_column_boundary_does_not_reset_supplier` |
| Supplier followed by customer; identical quoted IČO | Existing conservative behavior retained | Distinct parties correct; shared quote remains ambiguous | `test_supplier_then_customer_roles_do_not_mix`, `test_shared_identifier_separated_by_blank_line_remains_ambiguous` |
| Pending party name/identifier at a boundary | New transition coverage | Heading/next value cannot fill the stale pending field | `test_boundary_clears_pending_party_name_and_identifier` |
| Full validation, with/without earlier supplier identifier | Conflict check incidentally protected the original two-value case | Unsupported identifier cannot be suggested in either case | `test_full_validation_cannot_verify_identifier_after_boundary`, `test_full_validation_without_an_earlier_identifier_still_abstains` |
| Correct supplier plus unrelated bank identifier | New positive full-pipeline control | Correct supplier remains verified | `test_correct_supplier_not_conflicted_by_unrelated_identifier` |
| Source offsets and evidence | Existing contract retained | Original quote/page and normalized span are unchanged | `test_observation_offsets_and_source_evidence_are_preserved` |

The shipped mappings do not target party identifier fields; no artificial production mapping was introduced. `_safe_to_suggest=False` is asserted directly and through full validation.

## D. Test Results

Executed with the existing isolated Python 3.13 environment, `UV_PROJECT_ENVIRONMENT` pointing to `work/.venv`. Fresh task-local cache/temp paths avoid inherited Windows cache permissions.

- Initial `python -m pytest tests/unit/test_ocr_party_sections.py --basetemp ../../work/ocr-party-red -o cache_dir=../../work/ocr-party-red-cache --tb=short`: **7 expected failures, 1 pass** before the fix.
- `uv run --no-sync pytest tests/unit/test_ocr_party_sections.py tests/unit/test_semantic_adversarial.py tests/unit/test_validation_regressions.py --basetemp ../../work/ocr-party-focused -o cache_dir=../../work/ocr-party-focused-cache --tb=short`: **66 passed in 0.73 s**.
- `uv run --no-sync pytest tests/unit/ --basetemp ../../work/ocr-party-unit -o cache_dir=../../work/ocr-party-unit-cache --tb=short`: **183 passed in 3.48 s**.
- `uv run --no-sync pytest --basetemp ../../work/ocr-party-full -o cache_dir=../../work/ocr-party-full-cache --tb=short`: **280 passed in 5.80 s**.
- `uv run --no-sync ruff check .`: passed.
- `uv run --no-sync ruff format --check .`: passed, 70 files formatted.
- `uv run --no-sync python -m compileall -q app`: passed.
- `../.venv/Scripts/python.exe -m scripts.smoke_http` in an isolated source copy under `work/ocr-party-smoke`: passed (authenticated analysis and XLSX paths 200, unauthenticated analysis 401). Isolation avoids rewriting unrelated generated examples.

No remaining failures or unavailable required checks. No real credentials or paid LLM calls. AST comparison against the pre-task source confirms that only `observations()` changed among existing functions/classes; `_party_section_boundary()` is the only added function. Excel indexing, financial parsing, classification, `quote_support()` and `role_support()` are unchanged.

## E. Files Changed

- `app/validators/evidence.py`
- `tests/unit/test_ocr_party_sections.py` (new)
- `docs/ocr-party-section-fix.md` (this report)

No public schemas, API contracts, provider, dependency, Laravel or React changes.

## F. Remaining Limitations

Unrecognized captions without explicit section markers, heading recognition errors and OCR that destroys column alignment remain limitations of text-only layout interpretation. The bounded vocabulary is not a general page-layout engine. An apparent full-width heading conservatively ends both party contexts; unusual layouts can therefore require review. A matching identifier/checksum does not prove entity identity, and human confirmation remains required.

## G. Final Verdict

**FIXED AND VERIFIED** for the reproduced stale-context defect, equivalent party/identifier variants and the tested layout/section transitions.
