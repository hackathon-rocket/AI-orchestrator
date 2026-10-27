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
