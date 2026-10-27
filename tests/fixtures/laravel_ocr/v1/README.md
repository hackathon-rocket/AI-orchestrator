# Shared Laravel OCR → AI fixtures (v1)

**All examples are synthetic. None is an authentic OCR provider response.** They use the existing `clean_invoice.json` text and the public AnalyzeRequest contract. They do not establish a vendor `raw` schema, prove page extraction by Laravel, or implement the Laravel adapter.

## Verified versus unknown

The independently inspected Laravel job stores these known Extraction fields: `engine`, `language`, `filename`, `source`, `text`, `raw`. The provider response is stored as raw; its exact nested structure is **unverified**. `stored_extraction.synthetic.json` is an explicitly synthetic storage example. Its `raw:null` is a deliberate redaction placeholder, not a claim that the actual job stores null or that a provider returns this shape. `source:null` is also an unspecified synthetic value. No provider raw fields are invented.

When only a stored text string is reliable, send it unchanged as one `page:null`, using a validated stored/configured OCR language hint. Do not use the filename, source field, array offset or model existence to invent page numbers. `combined_text.request.json` is the corresponding normalized request. The storage example and its provenance wrapper must never be posted as AnalyzeRequest/content.

## Files

| File | Purpose |
|---|---|
| `stored_extraction.synthetic.json` | Known Laravel storage fields, synthetic/redacted raw |
| `combined_text.request.json` | Combined text, page=null, slk+eng |
| `numbered_pages.request.json` | Synthetic verified normalized page IDs 3/7/11; amount on page 7, line items on page 11 |
| `blank_page_gap.request.json` | Blank original page 2 retained; meaningful invoice on page 9 |
| `mixed_sources.request.json` | OCR page 3 plus a separately identified synthetic sheet and original cell coordinates |
| `sheet_only.request.json` | Structured cells with no PDF pages: sheet Doklad, amount B10, header A10 |
| `unknown_text.request.json` | Meaningful non-financial text; ces+eng hint, conservative unknown result |
| `cases.json` | Synthetic provenance and stable selected expectations for the six requests |
| `invalid_cases.json` | Seventeen synthetic invalid requests with expected HTTP/code; none is a provider response |

Request files can be POSTed directly to `/v1/analyze` with a local synthetic service token and mock provider. Their expected extraction/mapping values describe these labelled mock examples, not generic OCR/model accuracy. No response UUID, duration or provider-dependent text is frozen here. Existing full response/OpenAPI goldens remain in `tests/fixtures/contracts/v1`.

## Reusing these cases in Laravel

1. Load each request JSON without changing its text, page IDs or cell coordinates; send UTF-8 application/json with the server-side service token.
2. For each case in `cases.json`, assert the selected response properties. Its language expectation describes the normalized provider input (the service does not echo content.language as a new response property).
3. Preserve response evidence, original/null pages, source locators, mapping_status, requires_review and configuration identity metadata.
4. Run each `invalid_cases.json` request expecting its exact status/code and the safe ErrorResponse envelope. Do not depend on provider text or silently repair contradictory input.
5. Keep filename/source/raw in Laravel storage. Obtain an authentic sanitized provider response before implementing any vendor-specific raw-to-pages logic. These normalized examples are not evidence of `raw.pages`, blocks, segments or page-number field names.

Tests read fixed JSON and never regenerate expectations. `test_laravel_ocr_compatibility.py` exercises the real FastAPI app and deterministic validation/mapping with an observed mock provider. It checks that invalid input never reaches extraction and that accepted text, page order and sheet content reach the provider intact (only language is normalized). A separate proposal with a wrong evidence page proves that text on another page cannot prove a fact.

## Limits and execution

Run:

```sh
uv sync --frozen
uv run pytest tests/integration/test_laravel_ocr_compatibility.py -q --basetemp work/task4-ocr-target
```

The boundary tests generate large payloads in memory instead of storing megabytes of fixture padding. The existing default total content limit is 150,000 characters, below the hard 200,000-character page ceiling; the exact page-ceiling test explicitly raises the configurable total limit. It uses short OCR lines because the existing fixture-only mock constructs Evidence per line, capped at 4000 characters. A longer line can return provider_invalid_output even when the HTTP input itself is valid; no truncation or provider change is introduced by this task.

The repository still needs an authentic sanitized OCR response, confirmed page-index semantics and a Laravel-owned adapter/job integration test before claiming live OCR-to-AI compatibility. These are explicit integration dependencies.
