# Classification rejected after restart

## Confirmed deployment cause (2026-10-27)

After adding the diagnostics below, the live response still returned an empty
`classification.validation_reasons`. That result cannot come from the updated rejection
path. Inspection confirmed that port 8001 was owned by Docker, and the running
`egrant-ai-agent-agent-1` container belonged to the old `outputs/egrant-ai-agent` checkout.
Its imported validator did not contain `validate_classification`; its source hash differed
from the current `AI-orchestrator` checkout. The container had no host source-code mount.

The service therefore kept executing the old image across restarts. Rebuild and recreate
the service from the current repository:

```sh
docker compose up -d --build agent
```

`docker-compose.yml` now fixes the project name to `egrant-ai-agent`, matching the existing
service and avoiding a second project/port collision after moving the repository. Plain
`restart` does not incorporate source changes. Rebuilding/restarting does not itself call
the LLM. Live-model extraction still requires a separate operator request.

The running local service was rebuilt and recreated from this repository on 2026-10-27.
Its Compose working-directory label now points to `AI-orchestrator`, and the imported
validator's SHA-256 matches the host source. `/health`, `/ready`, and `/version` return
200. An offline check inside the rebuilt container produces BBSK type `FA`, gross 1230.0,
original page 3; a number-only classification citation returns
`classification_quote_not_heading`. The full suite was rerun: 517 tests passed, Ruff
check passed, and all 81 Python files passed the formatting check. No paid extraction
request was sent to the running service.

## Meaning of the original warning

The reported response contained `document_type="unknown"`, a null classification, and
`typ dokumentu: hodnota sa nenachádza v citovanom dôkaze.`. In the previous validator,
this warning means the classification vocabulary and source-quote checks passed, but
`role_support("classification", ...)` did not return `supported`. It does **not** mean
that the model returned no classification, nor that the quote was absent from the source.

On `clean_invoice.json`, that same warning can be reproduced with all of these proposals:

- `invoice` citing only `FA-2026-001`;
- `unknown` citing the full invoice heading;
- `contract` citing the full invoice heading.

A canonical `invoice` citing `FAKTÚRA č. FA-2026-001` on original page **3** passes
validation and produces the BBSK row. The old response discarded the rejected proposal,
so it cannot identify which failure occurred in the live request. Restarting cannot
repair an unsupported proposal. This change does not claim to reproduce or resolve the
precise live-model proposal error. The stale container explains why local fixes and new
diagnostics were absent from the user's responses.

## Changes

The model-facing `ExtractionProposal` schema now restricts `classification.value` to
`invoice`, `receipt`, `contract`, `bank_statement`, or null. Previously its generic
`Fact.value` schema allowed any string or number; canonical names were only described
in the prompt. Native, prompted and tool modes use the same restricted generation schema.

Parsing intentionally remains tolerant so a malformed or legacy localized classification
does not discard otherwise useful extracted facts or trigger extra provider calls.
The result is converted to the public `ExtractionData` model before validation.
Every classification still requires source and semantic verification. There is no
fallback from amounts, document numbers, a destination form, or an uncited heading.

Rejections keep the existing `null / not_found / 0 / evidence=null` policy and now carry
a server-generated code in `classification.validation_reasons`. The same code is included
in a fixed warning, including when `include_unmapped=false`. No raw proposal, quote,
identifier, or provider-supplied reason is copied into diagnostics or logs.

The public response shape, `fields`, `tables`, and `requires_review=true` are unchanged.
Valid classifications with provider uncertainty or top-level `document_type` disagreement
remain available with their evidence as `needs_review`, but cannot authorize mapping.

## Reading the next response

Repeat the same request after loading the updated service, then inspect the code in
`extracted_data.classification.validation_reasons` or `warnings`. Remote calls remain
an operator action; local verification uses synthetic responses only.

| Code | Meaning / corrective action |
|---|---|
| `classification_missing` | No classification value was proposed. Independent facts remain available. |
| `classification_value_invalid` | The value is not a canonical type or an exact supported localized alias. Use a canonical type, not a number or free-form heading. |
| `classification_unknown` | The proposal used the string `unknown`; missing classification should use null. |
| `classification_not_document` | Calculated classification is not accepted. |
| `classification_evidence_missing` | A non-null type has no source citation. |
| `classification_evidence_mismatch` | Check the exact source quote and original page/cell. Page 3 must not be renumbered to page 1. Invalid cell context also fails this check. |
| `classification_formula` | A spreadsheet formula is not direct evidence. |
| `classification_heading_missing` | No supported heading was recognized in the cited source. Prose mentions do not establish a type. |
| `classification_heading_mismatch` | The recognized source heading does not support the proposed type. |
| `classification_quote_not_heading` | Quote the type words in the heading; a document number alone is insufficient. |
| `classification_quote_ambiguous` | The quote occurs both in the heading and elsewhere. Use a longer exact heading citation. |
| `classification_evidence_spacing` | Only punctuation-spacing matching succeeded; heading position was not proven. Preserve the exact source text. |
| `classification_conflict` | Competing primary document types require review. |
| `classification_document_type_mismatch` | The top-level type and grounded classification disagree. Both must use the same canonical type. |
| `provider_uncertainty` | The model marked its classification as requiring review. |

The heading recognizer is conservative and vocabulary-based; an unrecognized genuine
heading may still require an additional regression and a narrowly scoped recognizer fix.
Do not remove the BBSK document-type gate to hide a classification failure.

## Offline verification

Regression tests cover the separate rejection branches, preservation of independent facts,
source coordinates, uncertainty and conflicts, provider schema constraints, and the complete
OpenAI Responses parsing → HTTP response → BBSK mapping path with `httpx.MockTransport`.
Both `include_unmapped` modes are checked. Diagnostics do not expose rejected content,
and logs remain metadata-only. Mock results are not model accuracy measurements.

Required commands: `uv run pytest`, `uv run ruff check .`,
`uv run ruff format --check .`, `uv run python -m scripts.smoke_http`.

Executed on 2026-10-26:

- `uv run pytest`: **517 passed**. The first full run had 516 passes and one
  Windows temp-directory permission error; rerunning with `PYTEST_DEBUG_TEMPROOT`
  pointing to the ignored workspace directory `work/pytest-temp` passed all tests.
- `uv run ruff check .`: passed.
- `uv run ruff format --check .`: 81 files already formatted.
- HTTP smoke: authenticated analysis 200, unauthenticated analysis 401;
  BBSK `FA-2026-001`, gross 1230.0, original page 3, `requires_review=true`;
  dynamic mapping and XLSX preparation/analysis also returned 200. Existing generated
  examples were restored after smoke to avoid replacing their request IDs/timestamps.

The relocated virtual environment had a broken pytest executable; an offline reinstall
of the already locked pytest/ruff packages repaired its launchers without version changes.
No real credentials or paid provider calls were used in verification. Confirmation of
the specific live failure still requires a new response containing the diagnostic code.
