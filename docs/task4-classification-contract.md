# Task 4 — classification contract on clean_invoice.json

Follow-up: the live failure persisted after this change. See
[classification diagnostics](classification-diagnostics.md) for the confirmed failing
validation branch, provider-schema constraints, and per-cause diagnostics. The historical
results below establish offline behavior only; they did not establish a live-model fix.

## Confirmed scope

The original real gpt-4.1-mini result discarded the rejected raw classification. Its precise failure mode cannot be established retrospectively. No new real OpenAI call was made. This change fixes the independently reproduced contract mismatch and improves the model-facing instructions; it does not claim that the original response necessarily used a localized value.

## Contract and implementation

The agent uses ExtractionData as its structured output in native, prompted and tool modes. document_type is a canonical Literal, but classification is a generic Fact, whose value admits arbitrary strings and Decimal. role_support expects a canonical type token. Previously the prompt did not explicitly distinguish classification.value from the original document title or number.

Changes:

- app/validators/semantics.py: canonical_classification() normalizes only exact canonical tokens and exact known TITLES names, with existing case/diacritic/whitespace normalization. It does not parse a type out of prose or numbered headings.
- app/validators/validation.py, validate_fact(): only classification proposals receive this vocabulary normalization. It copies the Fact and then runs the existing source, page/cell, heading, uncertainty and conflict checks. No input mutation, automatic fallback or evidence replacement.
- app/schemas/extraction.py: descriptions explicitly document canonical tokens, agreement between document_type and classification.value, heading quotes, locators and missing-value policy. Field names/types and the public response shape are unchanged.
- app/agents/prompts/system.md: explicit contract, a grounded classification example, prohibition on using only the document number or inferred financial facts, and instructions for unknown/missing evidence.
- tests/unit/test_clean_invoice_classification_contract.py: 23 cases, including the real OpenAI Responses/Pydantic AI path with httpx.MockTransport.

No modification to extraction_agent.py was needed: it already uses these prompt and schema definitions. evidence.py is byte-for-byte unchanged in Task 4. MappingEngine, Excel, money parsing, supplier/customer tracking and provider code are unchanged.

## Controlled fixture results

All cases use tests/fixtures/clean_invoice.json, original page 3, otherwise valid extracted facts and two line items. Proposals come from the existing offline fixture recognizer; classification is deliberately varied. Validation and BBSK mapping use the actual production functions.

| Proposal | Before | After | BBSK after |
|---|---|---|---|
| invoice + full heading | verified invoice | verified invoice | FA, 1230.0 |
| invoice + FAKTÚRA | verified invoice | verified invoice | FA, 1230.0 |
| faktúra + full heading | not_found / unknown | verified invoice | FA, 1230.0 |
| invoice + number only | not_found / unknown | not_found / unknown | blocked |
| Missing classification | not_found / unknown | not_found / unknown | blocked |
| Fabricated heading quote | not_found / unknown | not_found / unknown | blocked |
| contract + invoice heading | not_found / unknown | not_found / unknown | blocked |
| Heading on wrong cited page | not_found / unknown | not_found / unknown | blocked |
| Conflicting invoice/contract primary headings | needs_review / unknown | needs_review / unknown | blocked |

The successful table is rozpocet.zoznamuctovnychdokladov using bbsk_11824 / bbsk_11824_default. Correct facts remain available when classification fails. Provider uncertainty, top-level type disagreement, body-text mentions and arbitrary free-text classifications remain blocked. No attempt is made to infer missing classification from invoice number, amounts, parties or target form.

## Verification

Before implementation: 6 new tests failed, 12 passed. After implementation: all 23 new cases passed as part of the executed checks.

Commands used uv run --no-sync with work/.venv selected via UV_PROJECT_ENVIRONMENT, work/uv-cache-final via UV_CACHE_DIR, and pytest cache/temp directories under work.

- pytest tests/unit/test_clean_invoice_classification_contract.py tests/unit/test_invoice_classification.py tests/unit/test_validation_regressions.py tests/unit/test_semantic_adversarial.py tests/unit/test_mapping.py tests/unit/test_providers.py: 108 passed, 2.00 s.
- pytest tests/unit/: 393 passed, 3.57 s.
- pytest: 490 passed, 6.33 s; includes all earlier Excel, OCR, money and classification regressions.
- ruff check .: passed after fixing a description line-length issue.
- ruff format --check .: 76 files already formatted.
- python -m compileall -q app: passed.
- HTTP smoke in a working copy updated with the changed files: authenticated POST 200, unauthenticated POST 401, health/ready/version/docs/openapi 200, Excel endpoints 200, provider=mock, mapped gross=1230.0, requires_review=true.

No unavailable checks, paid API calls or real credentials. The offline OpenAI transport uses a synthetic token and no network requests.

## Limits and verdict

A model can still omit classification or cite only a number despite clearer instructions. Those results safely remain unknown. Exact alias normalization is deliberately bounded and cannot repair unknown names, missing evidence or conflicting primary headings. A future authorized real-model rerun is needed to verify live gpt-4.1-mini behavior and identify whether any additional failure mode exists.

**FIXED AND VERIFIED** for the reproduced canonical/localized contract defect and the offline pipeline. The precise original live-response failure remains unconfirmed.
