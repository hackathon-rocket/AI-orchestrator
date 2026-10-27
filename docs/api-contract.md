# API Contract v1.0

This is the existing synchronous Laravel-to-service contract. Laravel owns application-user authorization, OCR, storage, queues, persistence and confirmation. React receives proposals through Laravel. The service extracts and validates facts and proposes mapped values; it does not write to eGrant. This document freezes existing behavior, not a new API design.

## 1. Endpoints and authentication

| Method/path | Authentication | Successful JSON |
|---|---|---|
| `POST /v1/analyze` | Service Bearer token | `AnalyzeResponse` |
| `POST /v1/prepare-xlsx` | Same token | `PrepareXlsxResponse` |
| `GET /health` | Public | `{"status":"ok"}` |
| `GET /ready` | Public | `{"status":"ready","checks":[]}` |
| `GET /version` | Public | `{"version":"0.2.0","schema_version":"1.0"}` (application version may change) |
| `GET /docs`, `GET /openapi.json` | Public | Swagger UI / generated OpenAPI |

Use `Authorization: Bearer <AGENT_API_KEY>` from Laravel's server environment. Never expose it to React. There is no browser CORS configuration or user-authentication protocol here. No cookie, query-token, confirmation or eGrant-write API is provided. Production ingress may restrict the public documentation routes.

The configured token must have at least 16 characters. With a configured token, absent/empty credentials return `401 auth_missing`; an incorrect token or scheme returns `401 auth_invalid`. These responses include `WWW-Authenticate: Bearer`. Authentication runs before media-type/body validation. If the service token is unconfigured, protected requests return `503 not_ready` instead of 401. This is independent of application-user authentication in Laravel.

All HTTP responses carry `X-Request-ID` and `Cache-Control: no-store`. A supplied `X-Request-ID` parseable as a UUID is retained in canonical UUID form; an invalid/missing ID is replaced. Successful analysis includes the same ID in `meta.request_id`; errors include it in `error.request_id`. The ID is correlation metadata, not an idempotency key. The service does not persist requests.

## 2. AnalyzeRequest

`POST /v1/analyze` accepts `Content-Type: application/json`, optionally with charset parameters. Content-type matching is case-insensitive. Only absent or `identity` Content-Encoding is supported; compressed request bodies are rejected.

The complete synthetic request is [invoice.request.json](../tests/fixtures/contracts/v1/invoice.request.json), copied from `tests/fixtures/clean_invoice.json`. Abbreviated example:

```json
{
  "document_id": "synthetic-clean-invoice",
  "content": {
    "language": "sk",
    "pages": [{"page": 3, "text": "FAKTÚRA č. FA-2026-001\nMena: EUR\nSuma spolu: 1 230,00 EUR"}],
    "sheets": []
  },
  "form": {"id": "bbsk_11824", "schema": null, "mapping_id": "bbsk_11824_default"},
  "options": {"include_unmapped": true}
}
```

| Property | Required/default | Meaning |
|---|---|---|
| `document_id` | Required, non-null | Opaque reference, 1–100 ASCII letters/digits/`_`/`-`. No file path or OCR content. |
| `content` | Required, non-null | OCR pages and/or structured sheets; see below. |
| `form` | Optional, default `null` | Target form and approved mapping. Omission and explicit null both disable mapping. |
| `options` | Optional object | Defaults to `{"include_unmapped":true}`; explicit `options:null` is invalid. |

Typed envelopes forbid extra properties. Raw `form.schema` is a form-export dictionary, not a typed envelope. Models use Pydantic's existing coercion behavior; this is not a promise of strict JSON primitive typing. Do not depend on coercion in integrations.

## 3. OCR pages and structured sheets

`content.language` defaults to `sk`. Two-letter codes and supported aliases (`slk`, `eng`, `ces`, `cze`, `deu`, `hun`, `pol`, `ukr`, `fra`) are accepted in up to five `+`-separated components. Case/whitespace/aliases are normalized and duplicates removed (`SLK+ENG` → `sk+en`). Unknown three-letter aliases and empty components are rejected. Language is a hint, never evidence of currency.

`pages` and `sheets` default to empty arrays; explicit null is invalid. Either may be empty if the other supplies meaningful data. At least one non-whitespace page or non-null/non-whitespace cell is required.

- Page: `{"page":3,"text":"..."}`. `text` is required, up to 200,000 characters per model. `page` is a positive original source number, 1–100000; non-contiguous numbers are preserved. A missing/null page denotes one unpaginated OCR export. It cannot coexist with another page. Do not fabricate page 1 for combined text.
- Sheet: required `name` (1–100 characters) and `rows` array. Sheet names must be unique.
- Row: required `row` (1–1048576) and `cells` array. Rows must be unique within a sheet.
- Cell: required `coordinate` and `value`; optional `formula` defaults null, `header_cells` defaults `[]`. Value is string/number/null; boolean cells are rejected. Coordinates match `^[A-Z]{1,3}[1-9][0-9]{0,6}$`, must agree with their row, and cannot repeat. Header coordinates use the same pattern, cannot repeat and are limited to eight. These are relationship hints, not authorization to verify a role.

Example sheet-only content:

```json
{"pages":[],"sheets":[{"name":"Doklad","rows":[{"row":2,"cells":[
  {"coordinate":"A2","value":"Suma spolu"},
  {"coordinate":"B2","value":120.5}
]}]}]}
```

Formula strings are never executed; formula cells are not direct value evidence. Coordinates, sheet names and original page indices are not renumbered. See [structured_excel.request.json](../tests/fixtures/contracts/v1/structured_excel.request.json) and its complete golden response. Its amount remains reviewable for missing currency; contract tests preserve that status rather than inventing EUR.

## 4. Form/schema resolution

A non-null form requires `id` and `mapping_id` (same identifier rules as `document_id`). `schema` and `mapping` default to null.

- Without inline `mapping`, `mapping_id` must be registered for `id`. A supplied authoritative `schema` takes precedence over the configured schema. Configured target protections remain enforced. Metadata reports `schema_source="request"` for supplied schema, otherwise `"configured"`.
- With inline `mapping`, its `id`/`form_id` must agree with the envelope. An unregistered form requires its schema; a registered form may reuse the configured schema. Rules are validated before provider execution and are never persisted. The existing optional mapping `version` is an opaque label; this API does not implement version negotiation/tracking.
- Without a form, metadata has `mapping_id=null`, `schema_source="none"`, `fields=[]`, `tables=[]`, and a no-mapping warning. Extraction still runs.

Inline mappings contain `id`, `form_id`, `document_types` (default invoice), `rules`, optional `version` and `reserved`. Each rule names an extraction `source` and target `destination`; optional fields are `value_map`, `required_currency` and `review_warning`. There are no request-supplied paths, executable expressions, provider settings or credentials. See [mapping rules](egrant-mapping.md). Laravel must authorize the administrator, tenant and authoritative schema before forwarding configuration.

## 5. AnalyzeResponse

HTTP 200 returns all of these keys, including explicit nullable values:

| Property | JSON type | Meaning |
|---|---|---|
| `document_id` | string | Original reference. |
| `status` | `"completed"` | The analysis request finished. |
| `document_type` | string enum | `invoice`, `receipt`, `contract`, `bank_statement`, `unknown`. |
| `fields` | array of ExtractedField | Mapped scalar suggestions, possibly empty. |
| `tables` | array of ExtractedTable | Mapped table suggestions, possibly empty. |
| `warnings` | array of strings | Review, missing data, validation and mapping warnings. |
| `requires_review` | literal `true` | Human review is always required. |
| `extracted_data` | object or null | Universal extraction, controlled by `include_unmapped`. |
| `meta` | object | Processing/provider/schema metadata, below. |

**Completed analysis, verified facts, available mapping and human confirmation are separate states.** `completed` does not mean mapping succeeded, all required values exist, the document is eligible, it is safe to submit automatically, or a user confirmed anything. There is no `mapping_status` property. Warnings and actual mapped values must be inspected.

Unknown/ambiguous classification does not erase independently supported facts. It blocks mappings configured only for recognized types. A recognized receipt or contract can complete successfully and still have no mapping. BBSK's current `bbsk_11824_default` mapping supports invoices only; the presence of other options in a form's select does not implement other document mappings.

A successful BBSK invoice response has `fields=[]` and one table row in `rozpocet.zoznamuctovnychdokladov`: `typ="FA"`, `c_dokladu="FA-2026-001"`, `suma_doklad=1230.0`, original evidence on page 3. Two invoice items remain in universal `line_items`; they are not expanded into two accounting-document rows. Grant share, expense classification, ordinal, attachment and payment confirmation are not invented.

The response has no `document` summary. Laravel adds its stored document object when composing the React response. See [Laravel integration](laravel-integration.md).

## 6. ExtractedField, ExtractedTable and TableColumn

Every mapped field and table cell has these properties:

| Property | Type / behavior |
|---|---|
| `tag` | String: full target path for scalar fields, short child tag for table cells. |
| `label` | String from the target schema. |
| `type` | `string`, `number`, `currency`, `date`, `ico`, `dic`, `iban`. Select values use `string`. |
| `value` | String, JSON number or null. Dates use ISO `YYYY-MM-DD`; no monetary serialization change is introduced here. |
| `confidence` | Number in [0,1]; current validator caps proposals at 0.85, lower for review. It is a heuristic, not calibrated accuracy. |
| `evidence` | Original quote string or null. |
| `page` | Original page integer or null. Null for spreadsheet/unpaginated sources is legitimate. |
| `status` | `verified`, `needs_review`, `not_found`. |
| `validation_reasons` | Array of reason-code strings; may be empty. |
| `source_locator` | CellLocator or null. |

ExtractedTable: `tag` (full table path), `label`, `columns`, `rows`. TableColumn: `tag`, `label`, `type`. Each row is an object mapping child tags to full ExtractedField cells, including their statuses/evidence. A returned table may have no proposed rows if all cell values are absent. Clients should address fields/columns by tags rather than depend on object-key order.

The original seven frontend field properties remain available. Laravel/React can retain or display additive status/reason/locator fields. Missing or protected values never become fabricated suggestions.

## 7. Evidence, verification and missing values

When `include_unmapped=true` (default), `extracted_data` contains `document_type`, `classification`, fixed universal `fields`, `line_items`, and bank `transactions`.

Each Fact has `value`, `status`, `evidence`, `confidence`, `source_kind`, `validation_reasons`, `currency`. `source_kind` is normally `document`; calculated proposals are not accepted as direct evidence. Numeric Decimal facts serialize as strings in this richer representation (e.g. `"1230.00"`), while mapped monetary values are JSON numbers. This existing difference is intentional and protected by fixtures. Clients must not apply binary floating point when recomputing financial validation.

Evidence is `{"page":3,"quote":"...","locator":null}` or spreadsheet evidence such as `{"page":null,"quote":"120.5","locator":{"sheet":"Doklad","cell":"B2","context_cells":["A2"]}}`. A numbered page and cell locator cannot coexist. Context cells must exist, be non-formula and satisfy deterministic geometry/semantic checks. A literal number alone never establishes its role as a document total.

Currency metadata is null or `{value,status,evidence,reasons}`; evidence is an array of source Evidence objects. It is recomputed independently of provider claims. Currency-restricted mapping needs a verified matching currency; no language-derived currency or automatic conversion is provided.

- `verified`: source evidence, semantic role and applicable deterministic checks passed. It is not proof that OCR is correct or accounting approval.
- `needs_review`: retain a reviewable value with evidence/reasons when supported sufficiently to show it. A supported-role suggestion may remain mapped with this status (e.g. provider uncertainty). Not all reviewable values are safe to map.
- `not_found`: rejected/absent values have `value=null`, `confidence=0`, `evidence=null`. Reasons may explain rejection; null is not zero or an empty string.

Conflicting values/roles, malformed amounts, financial inconsistencies and protected destinations block trusted suggestions. Sensitive amounts/dates/identifiers need positive role support. Private server flags are not serialized and cannot be set by clients. Missing/invalid classification has server reason codes and fixed warnings; a type mismatch remains reviewable/unknown. See [classification diagnostics](classification-diagnostics.md).

Omitted extraction fields use the missing-value policy. In requests, nullable `form`, `form.schema`, cell values and source page numbers have the meanings above; arbitrary nulls in required structures are rejected.

## 8. include_unmapped and metadata

`options.include_unmapped=false` preserves the key `extracted_data` with value **null**. It does not remove mapped suggestions, warnings, status, metadata or `requires_review`. True returns the richer extraction. Both behaviors have full golden fixtures.

Metadata has exactly the existing properties:

| Property | Meaning |
|---|---|
| `schema_version` | Literal `"1.0"`; distinct from the application release. |
| `provider` | Adapter name (`mock`, `openai`, `openai_compatible`, `openrouter`). |
| `model` | Configured model identifier; `mock` in offline fixtures. |
| `request_id` | Canonical UUID matching the response header. |
| `duration_ms` | Finite elapsed milliseconds; varies per request. |
| `mapping_id` | Resolved mapping identifier or null without form. |
| `schema_source` | `request`, `configured`, or `none`. |

Metadata is not proof that a particular fact is correct. There are no additional mapping-status or form-version metadata fields.

## 9. Limits and error envelope

Default limits (server configurable): 2,000,000 JSON body bytes, 150,000 content characters, 50 pages, 10 sheets, 2,000 rows total, 10,000 cells, 5,000 form nodes, depth 20. Hard Pydantic ceilings also apply (100 pages, 20 sheets, 5,000 rows/sheet, 1,000 cells/row). Limits can therefore fail either request schema validation or the configured resource check. Body receipt timeout is 10 seconds; default analysis deadline is 100 seconds. Provider calls have bounded retries/timeouts. There is no streaming progress API.

Handled failures on API routes and middleware use ErrorResponse:

```json
{"error":{"code":"auth_missing","message":"Vyžaduje sa autentifikácia služby.","request_id":"<canonical UUID>"}}
```

Messages are fixed safe text. Error responses never include invalid request input, OCR, credentials, rejected model values, raw provider bodies, stack traces or exceptions. Tests check safe shapes, matching request IDs, no-store headers and synthetic leak sentinels. Unexpected framework/provider exceptions are sanitized as `internal_error`. Errors are not completed AnalyzeResponses.

| HTTP | Existing codes / meaning |
|---|---|
| 401 | `auth_missing`, `auth_invalid` (with Bearer challenge) |
| 404/405 | `http_error` for unsupported route/method |
| 408 | `request_timeout` receiving the body |
| 413 | `request_too_large`, `content_too_large`, `workbook_too_large` |
| 415 | `unsupported_media_type`, `unsupported_encoding` |
| 422 | `invalid_request`; mapping/schema/workbook validation codes listed below |
| 429 | `busy`: analysis or workbook concurrency slot not acquired within queue deadline |
| 500 | `internal_error` |
| 502 | `provider_invalid_output`, `provider_rejected_request`, `provider_auth_error`, `provider_unsupported_mode`; base ProviderError fallback `provider_failure` |
| 503 | `not_ready`, `provider_unavailable`, `provider_rate_limited`, `provider_configuration_error`, startup mapping-configuration failure |
| 504 | `provider_timeout`, `analysis_timeout`, `workbook_timeout` |

Existing mapping/schema 422 codes: `unknown_mapping`, `invalid_form_schema`, `invalid_mapping_destination`, `invalid_select_mapping`, `protected_mapping_destination`, `reserved_mapping_destination`, `invalid_mapping_type`, `mapping_identity_mismatch`, `missing_form_schema`. Existing workbook 422 codes include `invalid_workbook`, `empty_workbook`, `unsupported_workbook_features`. Runtime resource or configuration settings can change which validation layer fails first; clients should use the code and status together.

`busy` is concurrency/backpressure, not a time-window quota. Upstream model rate limiting is `503 provider_rate_limited`, not 429. A permanent configuration/auth/mode failure should not be retried as though it were transient. See the bounded retry guidance in [Laravel integration](laravel-integration.md).

## 10. Readiness exception to the envelope

`GET /ready` intentionally returns **ReadinessResponse**, including on HTTP 503:

```json
{"status":"not_ready","checks":["service_token_not_configured"]}
```

Checks can include `service_token_not_configured`, `model_not_configured`, `remote_llm_not_authorized`, `provider_key_not_configured`, `endpoint_not_configured`, `invalid_mapping_configuration`, `provider_configuration_error`. Multiple checks may be returned. Readiness checks configuration, never contacts an LLM, and does not guarantee provider connectivity or that a requested mapping exists. `/health` stays `{"status":"ok"}` while readiness fails. Analysis returns an ErrorResponse with `not_ready` if startup did not create its orchestrator. With a valid service token, XLSX preparation does not depend on LLM readiness.

## 11. XLSX preparation

`POST /v1/prepare-xlsx` accepts **raw XLSX bytes**, not multipart or JSON, using `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` and the same Bearer token. It returns HTTP 200 with `{"content":{"language":"sk","pages":[],"sheets":[...]},"warnings":[...]}`. Pass `content` to `/v1/analyze` in a separate request and retain preparation warnings in Laravel. No uploaded file is stored and no model is called by preparation.

Defaults: 5 MB compressed input, 20 MB expanded ZIP, 200 ZIP entries, compression ratio 200, 150,000 XML nodes, two concurrent parsers and a 15-second parsing deadline. Ordinary content limits also apply.

Sheets, rows and coordinates are preserved. Excel date cells become ISO strings. Semantic header relationships are conservative hints checked again during analysis. Formulas have `value=null` and preserved `formula` text; cached results are not trusted. Empty sheets may survive in a nonempty workbook; entirely empty/formula-only input fails. Macros, embedded objects, external relationships and malformed/oversized archives are rejected. Cancellation does not free a parser slot until its bounded worker finishes. No XLSX parsing/formula behavior is changed by this contract work.

The deterministic fixture [prepare_xlsx.input.json](../tests/fixtures/contracts/v1/prepare_xlsx.input.json) describes a synthetic workbook recipe; [prepare_xlsx.response.json](../tests/fixtures/contracts/v1/prepare_xlsx.response.json) protects its full JSON result. ZIP timestamps are irrelevant and are not snapshotted.

## 12. OpenAPI and compatibility policy

`/openapi.json` is generated from the actual routes/models and exposes Bearer authentication, JSON analysis input, binary XLSX input and typed success/error responses. `/ready` explicitly declares its own 503 shape. Do not use stale generated files under `docs/examples` as the authoritative live schema.

Contract tests protect the paths/methods, request/response references, required fields, types, nullability, enum values, defaults, constraints, extra-property policy and security declarations. Editorial OpenAPI descriptions, examples, titles, summaries and operation IDs are excluded from the structural fixture. A property name is never excluded merely because it matches an editorial keyword.

API paths stay `/v1/...`; metadata stays schema version `1.0`. This task changes no public field, route, authentication requirement, serialization, validation or mapping behavior. Contract fixtures are reviewed expectations, not permission for silent breaking changes. Future changes require explicit compatibility review of existing consumers and appropriate versioning; this task adds no version negotiation mechanism.

## 13. Contract fixtures and tests

Read [fixture notes](../tests/fixtures/contracts/v1/README.md) and run:

```sh
uv run pytest tests/integration/test_api_contract_v1.py -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q app tests
uv run python -m scripts.smoke_http
```

On Windows where the system pytest temporary directory is inaccessible, add `--basetemp work/contract-v1-target` for targeted tests and a distinct `--basetemp work/contract-v1-full` for the full suite. This changes only test temporary storage.

Seven paired request/response goldens cover mapped invoice, unsupported receipt, unknown, no form, mapped-only output, structured Excel and unpaginated OCR. Error and XLSX goldens plus a structural OpenAPI fixture protect the remaining boundaries. Only `meta.request_id`, `meta.duration_ms` and the error request ID are normalized after checking their structure and header agreement. Provider/model metadata, warnings, amounts, evidence, pages, coordinates and validation states remain exact. Expected files are never generated or updated during test execution. Review every fixture diff as an API change.

Existing extraction/security/Excel/classification/money tests remain in force; these contract tests supplement them rather than replace semantic validation tests. Tests run with mock extraction and simulated provider failures, with external networking blocked. HTTP smoke uses loopback and a fresh ephemeral service token.

## 14. Integration limitations

- HTTP 200 and `completed` cannot tell Laravel that mapping was available; inspect output/warnings. No separate mapping-status flag exists.
- BBSK invoice mapping does not implement receipt or contract mapping. Unknown/unsupported types must remain unmapped.
- Rich Fact Decimal strings and mapped JSON numbers differ; consumers must preserve financial precision and nulls.
- A null page means unpaginated/spreadsheet evidence, not page 1. `extracted_data=null` means omitted by option, not failed extraction.
- Warnings are human-facing Slovak messages, with selected diagnostic codes; they are not a complete machine-readable workflow state model.
- Readiness is configuration-only and uses a different failure envelope. XLSX preparation may work while analysis is not ready.
- Pydantic input coercions and resource/schema validation layers remain as implemented; integrations should send explicit documented types.
- There is no result persistence, end-user authorization, document-summary wrapper, multi-document workbook splitting, user confirmation or eGrant submission in this service. Laravel must implement those boundaries.
