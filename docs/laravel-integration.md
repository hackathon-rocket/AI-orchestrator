# Laravel integration boundary

Proposed flow:

```text
Upload → DocumentController → ProcessDocumentOcr → Extraction.text/raw
→ future AnalyzeDocument queue job → internal POST /v1/analyze
→ MongoDB result persistence → GET /api/documents/{id}
→ React review/edit → POST /api/documents/{id}/confirm
```

The inspected backend already performs upload/storage/OCR. The AI job, AI client, frontend result assembly and confirmation path below are **integration work still to implement in Laravel**, not changes made by this repository.

Keep the bearer token in Laravel's server environment and service configuration. The browser must not receive it. A future `AiClient` can send a typed request:

```php
$response = Http::withToken(config('services.agent.key'))
    ->connectTimeout(3)
    ->timeout(110) // greater than the agent's default 100-second analysis deadline
    ->acceptJson()
    ->post(config('services.agent.url').'/v1/analyze', [
        'document_id' => (string) $document->id,
        'content' => [
            'language' => 'slk+eng', // normalized by this service to sk+en
            'pages' => $pages, // true OCR page indexes, or one {page: null, text: ...}
            'sheets' => [],
        ],
        'form' => [
            'id' => 'bbsk_11824',
            'schema' => $authoritativeFormExport,
            'mapping_id' => 'bbsk_11824_default',
        ],
        'options' => ['include_unmapped' => true],
    ]);
$response->throw();
$proposal = $response->json();
```

Use Laravel's stored `Extraction.raw` when verified page metadata is available. Otherwise send `Extraction.text` as one unpaginated page with `page:null`, so UI evidence does not claim an invented page. Do not assume the inspected OCR client decomposes pages; it stores the provider's raw JSON unchanged. Build and test the adapter against a real OCR export.

## OCR input adapter contract and shared fixtures

The following is an integration specification for the future Laravel adapter, not code already implemented in Laravel or a new Python OCR service. Use the [shared synthetic fixtures](../tests/fixtures/laravel_ocr/v1/README.md) and [case expectations](../tests/fixtures/laravel_ocr/v1/cases.json) in Laravel contract tests.

### What the inspected Laravel implementation proves

`OcrClient` uploads a file to `{OCR_API_URL}/ocr-upload`, with the observed default language hint `slk+eng`, and returns provider JSON. `ProcessDocumentOcr` saves:

| Stored Extraction field | Observed assignment |
|---|---|
| engine | `ocr-api` |
| language | `result['language'] ?? null` |
| filename | Original filename |
| source | `result['source'] ?? null` |
| text | `result['text'] ?? ''` |
| raw | Complete provider result |

The job then marks the document `ocr_completed`. That state alone does not prove nonempty OCR, valid pagination, analysis success or human approval. No guaranteed raw-response page structure or verified page-by-page transformation was established. A `DocumentPage` model's existence does not prove that this job populates it.

**Unresolved dependency:** no authentic sanitized OCR provider response fixture was found in this repository. Do not assume `raw.pages`, `page_number`, blocks or segments are actual provider fields. Obtain a sanitized real response and establish the provider's indexing semantics before implementing that branch. The storage fixture's `raw:null` is explicitly redacted synthetic data, not a description of the actual provider response.

### Adapter decision procedure

1. Choose an opaque document reference matching the service ID rules. Keep filename, source and raw in Laravel; these are not additional AnalyzeRequest/content fields.
2. If independently verified page-level text and original positive page identifiers exist, construct normalized `pages` from that trusted representation. Preserve source IDs, gaps, blank pages and Unicode text. Do not renumber by array position or assume a vendor uses one-based indexing. The normalized page examples are synthetic API examples, not vendor raw exports.
3. Otherwise use the known stored `Extraction.text` as one `{page:null,text:originalText}`. An omitted page is also unpaginated in the current API; explicit null is recommended. Never substitute page 1 for unavailable physical pagination.
4. Reject absent/non-string/all-Unicode-whitespace OCR locally before dispatching AI. An empty text result can be stored with ocr_completed by the observed job, so this preflight is necessary. Do not silently trim, collapse whitespace, split arbitrary paragraphs into pages, or truncate text to make it fit. Test emptiness separately and retain the original string.
5. Supply a validated stored language hint or the configured hint actually used for OCR. Stored null must not be forwarded as `content.language:null`. If omitted, the service defaults to sk; that default does not recover a missing mixed-language hint. `slk+eng` becomes sk+en, `ces+eng` becomes cs+en; duplicate hints are removed. Language never proves currency or type.
6. Send separately prepared spreadsheet sources as sheets, preserving sheet/row/cell coordinates and header hints. Do not manufacture PDF pages for cells. Nonempty pages and sheets may coexist; all sources must belong to the same document analysis. A meaningful sheet can make otherwise blank OCR content acceptable.
7. Respect both schema and configured resource limits. Reject or explicitly route oversize input to a separate, provenance-preserving workflow; never silently drop pages or trailing evidence. A multi-document register must be split upstream, not interpreted as one document merely to fit limits.

The existing request models perform some Pydantic coercion: for example `page="3"` becomes 3 and `page=true` becomes 1. The API is not a strict-primitive validator. Laravel must supply actual JSON positive integers and reject booleans/string guesses in its adapter before assigning source page IDs. No input validation behavior is changed by these tests.

### Shared fixtures and response checks

The fixture directory contains six normalized requests, one synthetic/redacted known-field storage record, stable selected expectations and 17 invalid payloads. Combined-text and numbered-page invoice examples use the existing synthetic clean invoice. `numbered_pages` deliberately places the heading/dates on page 3, amounts on page 7 and invoice lines on page 11. `blank_page_gap` preserves a blank page 2 and evidence on page 9. `sheet_only` uses amount cell B10 without a PDF page. A mixed-source request proves that adding a sheet does not erase OCR page provenance. The non-financial text example remains unknown and unmapped.

The service returns `completed` for accepted analyses, not confirmation of OCR quality. Retain original/null page references, source_locator, warning/reason arrays, mapping_status and configuration fingerprints. Wrong-page evidence remains rejected even when the same amount exists elsewhere in the supplied document. `requires_review` always stays true.

All-empty/missing/non-string OCR, contradictory pagination and malformed language hints return the existing 422 invalid_request. Hard model page/sheet/length ceilings also return 422. Configured aggregate limits return 413 content_too_large, while the streamed byte limit returns 413 request_too_large before JSON/extraction. Errors do not echo OCR or credentials. Use the shared invalid cases for a PHP-side conformance test rather than parsing Slovak messages.

### Limit distinctions and current helper limitations

| Constraint | Current value | Rejection layer |
|---|---|---|
| Pages | Configured default 50; hard ceiling 100 | 413 / 422 |
| Text per page | Hard ceiling 200,000 characters | 422 |
| Total source text/cell/formula characters | Configured default 150,000 | 413 |
| Sheets | Configured default 10; hard ceiling 20 | 413 / 422 |
| Analysis HTTP body | Configured default 2,000,000 bytes | 413 |
| Evidence quote | Maximum 4000 characters | Provider proposal schema |

Unicode character limits and UTF-8 byte limits are different. A page allowed by its hard field ceiling can still exceed the smaller configured aggregate limit. Exact-boundary tests retain evidence at the end of a 200,000-character page under an explicitly raised aggregate limit; rejected oversized inputs never reach extraction.

`scripts.analyze.from_ocr_export()` is an offline convenience for explicitly normalized pages/text exports. It is not a verified OCR provider adapter and currently does not carry an exported language hint through to Content (the default is sk). Do not use it as proof of live Laravel raw compatibility. The fixture-only mock also constructs Evidence for every nonblank line: a single line over 4000 characters can cause provider_invalid_output even when the input contract accepts the page. This is an existing mock limitation, not permission to truncate real OCR or a change to provider behavior. Real-provider handling requires an authorized evaluation.

Run `uv run pytest tests/integration/test_laravel_ocr_compatibility.py -q --basetemp work/task4-ocr-target` for these shared cases. No OCR service, real model, storage/queue implementation or changes to Laravel are required by the Python tests.

## Queue and persistence boundary

Dispatch `AnalyzeDocument` after successful OCR persistence. Choose a job timeout above 110 seconds and queue visibility/retry-after longer than the job timeout. Retry a small bounded number of times only for retryable categories such as `busy`, `provider_rate_limited`, `provider_unavailable` and timeouts, with delay. Use the error code as well as HTTP status: `provider_configuration_error` is a permanent 503 until configuration is repaired. Do not retry bad auth, schema/input errors or invalid provider configuration automatically. Avoid an additional `Http::retry` loop stacked with job retries and the service's three-attempt maximum. For example, two job attempts can mean up to six model calls; budget accordingly. Persist results idempotently against document/version and mapping/version; Python is stateless.

The analysis response also contains the server-computed `mapping_status`:

| Value | Laravel interpretation |
|---|---|
| `mapped` | At least one non-null form suggestion exists; save for human review. This may be a partial row or only the document-type cell. |
| `no_form` | No target form was supplied; universal extraction can still be saved. |
| `unsupported_document_type` | The selected mapping does not support the validated type (including unknown). No invoice row should be inferred. |
| `no_values` | The mapping is applicable but yielded no safe non-null suggestions. Do not treat a table definition as a populated row. |

Use this enum rather than parsing localized warnings or testing whether tables/fields arrays are nonempty. `status="completed"` means execution completed for all four outcomes; none is user confirmation. Zero amounts and safe `needs_review` suggestions count as mapped. The field is returned even when `include_unmapped=false`; configuration/provider failures retain the existing HTTP error envelope. Keep the enum in persisted analysis results if needed for the review workflow. Strict client schemas must accept the additive response field. This does not change Laravel's own document status model or require a new frontend wrapper.

For persistence, retain `meta.mapping_id`, `meta.mapping_version`, `meta.mapping_fingerprint`, `meta.form_schema_fingerprint`, `meta.authoritative_constraints_fingerprint`, and `meta.schema_source` alongside the result. The API `meta.schema_version` remains `"1.0"`; it is not a form version.

- `mapping_version` is the optional human-managed label of the effective mapping (bundled configurations start at `"1.0"`); inline null/omitted versions remain null.
- `mapping_fingerprint` identifies actual validated mapping content, including its declared label and all rules/defaults, excluding the schema filename/location.
- `form_schema_fingerprint` identifies the full selected raw schema, including request overrides or registered fallback.
- `authoritative_constraints_fingerprint` identifies additional server-registered protected/reserved decisions for rule destinations. It is null when the mapping and selected schema already cover all dependencies (also for no form or wholly unregistered inline forms). Registered constraints are still enforced when a request supplies its own schema.

The identifiers use `sha256:<64 lowercase hex>`. Formatting and object-key ordering do not affect them; array ordering does. Compare fingerprints when deciding whether configuration changed, even if a version label is unchanged. They describe the resolved configuration for mapped/unsupported/no-values outcomes, and are still returned with `include_unmapped=false`. All four are null without a form. Compare the entire identity set: changing a registered readonly restriction can remove a suggestion even with unchanged request schema and mapping fingerprints. Keep schemas/mappings in Laravel's own historical storage if required: the service cannot retrieve them by fingerprint and does not migrate or reanalyze saved results. These hashes do not replace authorization, human review, or recording relevant software/model versions. Strict response schemas must allow the four nullable identity metadata fields.

Suggested status translation (owned by Laravel):

| Internal state | React status / stage |
|---|---|
| uploaded | uploaded / null |
| OCR running | processing / ocr |
| ocr_completed, AI queued/running | processing / extraction |
| validated proposal persisted | extracted / null |
| ocr_failed or analysis failure | failed / null, safe error_message |
| human confirmation persisted | confirmed / null |

The single synchronous agent call does not stream stages. Laravel may display `extraction` while waiting and `validation` while checking/persisting the returned response; it must not claim to observe Python's internal stage transitions. Python's logs have metadata stages for operators.

Assemble the existing frontend response:

```php
return [
    'document' => $documentSummary, // existing metadata + translated status
    'fields' => $proposal['fields'],
    'tables' => $proposal['tables'],
    'warnings' => $proposal['warnings'],
];
```

Keep `extracted_data`, `meta`, source OCR and schema/version references in restricted audit storage if appropriate. The service does not supply user consent or evidence that the user reviewed anything. Confirmation must reauthorize document/form access and validate edited values against the current form in Laravel. No confirmation endpoint or eGrant writer is present here.

## Exact financial values in mapped proposals

Mapped ExtractedField objects, including table cells, now contain nullable `decimal_value`. For `type="currency"`, this is the authoritative exact financial representation when a value has passed mapping safety checks. The existing numeric `value` is retained for compatibility and UI presentation; PHP float and JavaScript Number do not guarantee exact decimal arithmetic.

For BBSK, `tables[].rows[].suma_doklad` retains value=1230.0 and additionally carries decimal_value="1230.00". Scalar currency destinations behave identically. The exact string comes directly from the validated Decimal before float conversion, using a period and exactly two fractional digits, without grouping/symbols/scientific notation. Negative zero is preserved as "-0.00" without changing its arithmetic equality to zero.

Laravel should:

1. Persist mapped currency proposals from decimal_value using exact decimal-capable handling or appropriate fixed-scale database storage.
2. Keep the string exact; do not cast it to PHP float or use JavaScript Number for financial calculations.
3. Preserve null as missing/rejected, never substitute "0.00". Numeric zero is valid only when already supported by source/validation.
4. Continue server-side validation and authorization of human-confirmed amounts. Exact serialization does not prove eligibility, payment, or user consent.
5. Accept the new nullable field in strict response schemas. Old display clients may continue reading value, but it is not an authoritative financial source.

No PHP, database or frontend code is implemented here. Historical responses without an exact field require a trusted exact source/reanalysis policy upstream; formatting their old float as text cannot recover precision.

For non-currency fields (including `number`, dates, IDs and descriptions), decimal_value is null. Missing or unsafe currency suggestions, currency mismatches, conflicts and invalid precision also yield null exact values; there is no fallback from the rejected raw Fact. Rich `extracted_data.fields.*.value` retains its existing Decimal-string serialization and has no decimal_value property. `include_unmapped=false` leaves exact mapped values available even though extracted_data is null. Existing statuses, warnings, source locators, fingerprints, legacy values and requires_review are unchanged.

## Optional OCR container

The supplied specification names this image; it was not executed as part of offline service tests:

```sh
docker run -d --name ocr-api -p 127.0.0.1:9010:8000 trayvor/ocr-api-slk:v0.0.7
curl http://127.0.0.1:9010/health
curl -F "file=@synthetic-invoice.pdf" "http://127.0.0.1:9010/ocr-upload?lang=slk%2Beng"
```

The expected default language from the supplied specification is `slk+eng`; verify it against the running container. OCR is a Laravel integration dependency, not a prerequisite for this service or its tests. Excel needs its own Laravel preparation path because the current upload validation excludes XLSX.


## Dynamic mapping and XLSX

Laravel may add `form.mapping` from an administrator-approved configuration to the request above. The service validates the mapping against `form.schema` (or the registered template); arbitrary client/browser configuration must never be forwarded without authorization. Store the schema/mapping version alongside each result. The [dynamic example](examples/dynamic_request.json) runs without registering a new server-side file.

For XLSX, add the upload/MIME policy in Laravel, store the source, and dispatch a dedicated preparation/analysis job. Send the binary body to the internal preparation endpoint, then pass its returned content to the existing analysis call:

```php
$prepared = Http::withToken(config('services.agent.key'))
    ->connectTimeout(3)->timeout(20)->acceptJson()
    ->withBody($storedXlsxBytes,
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    ->post(config('services.agent.url').'/v1/prepare-xlsx')
    ->throw()->json();

$proposal = Http::withToken(config('services.agent.key'))
    ->connectTimeout(3)->timeout(110)->acceptJson()
    ->post(config('services.agent.url').'/v1/analyze', [
        'document_id' => (string) $document->id,
        'content' => $prepared['content'],
        'form' => $approvedFormContext,
    ])->throw()->json();
```

Check compressed size before loading a workbook into Laravel memory, retain original file/cell references, and propagate preparation warnings. Multi-invoice registers must be split into document requests upstream; the service deliberately maps one accounting document per analysis. The existing OCR job remains responsible only for OCR-capable inputs; do not send XLSX to OCR.

The new optional field status/reasons/locator may be ignored by existing React consumers or displayed as review hints in a later frontend change. Keep the original seven field properties. `requires_review` remains true even when all proposed fields are verified. No Laravel or React source was changed in this repair.
