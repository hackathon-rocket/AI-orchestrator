# API v1 contract fixtures

These are fixed, reviewed expectations for the mock API, not a fixture generator. Tests only read them. No real provider data or credentials are included. Response goldens now also protect the additive `mapping_status` field: invoice/mapped_only/unpaginated=mapped; no_form/structured_excel=no_form; unsupported_receipt/unknown=unsupported_document_type. The applicable-but-empty outcome has a no_values golden plus real HTTP tests in tests/integration/test_mapping_status.py.

## Files

Each of these names has `<name>.request.json` and `<name>.response.json`:

| Name | Source / behavior |
|---|---|
| invoice | Existing clean_invoice.json, unchanged; verified BBSK invoice, FA / FA-2026-001 / 1230.0, page 3 |
| unsupported_receipt | Existing receipt.json; recognized but unsupported by configured BBSK invoice mapping |
| unknown | Existing unknown.json; no invented verified facts or mapping |
| no_form | clean_invoice.json with form omitted; universal extraction and no mapping |
| mapped_only | clean_invoice.json with include_unmapped=false; extracted_data stays explicitly null |
| structured_excel | Existing structured_excel.json; rows/cells/locators and missing-currency review status retained |
| unpaginated | clean_invoice.json with page=null; no invented page number |
| no_values | clean invoice with an inline subset of the existing BBSK mapping, requesting only missing payment_date; applicable mapping, empty table rows |

- `errors.json`: exact representative HTTP status/error messages and shapes.
- `prepare_xlsx.input.json`: workbook recipe (sheet title and rows); tests create XLSX bytes in memory. No nondeterministic ZIP timestamps are compared.
- `prepare_xlsx.response.json`: complete prepared content, including formula text with null cached value and warnings.
- `openapi.structure.json`: paths and schema/security components, excluding editorial annotations only.

## Dynamic metadata policy

Successful responses replace only `meta.request_id` with `$dynamic.request_id` and `meta.duration_ms` with 0. Before normalization tests require a canonical UUID, matching response header, finite nonnegative duration, JSON Content-Type and Cache-Control:no-store. Error goldens normalize only `error.request_id` after the same header/UUID checks. Correlation-ID passthrough/replacement also has independent tests.

Provider and model remain exact (`mock`). Evidence, amounts, dates, pages, tags, coordinates, statuses, reasons and warnings are never normalized. Full dictionary equality ignores object key ordering; array contents/order remain fixed for these fixture outputs. Consumer identity should be based on tags.

OpenAPI comparison excludes description/title/examples/example/summary/operationId annotations, not public fields that happen to have one of those names. Constraints, defaults, refs, required flags, enums, nullability and Bearer declarations remain compared.

## Review/update policy

The initial fixtures were authored once against the existing application, then inspected for expected business behavior. No generation code runs in the test suite. To intentionally update a contract, review the endpoint/model change and every semantic fixture diff independently; do not regenerate expected data as a test setup step. Existing docs/examples smoke outputs are demonstrations and are not these goldens.

Run `uv run pytest tests/integration/test_api_contract_v1.py -q`. If the system temporary directory is unavailable on Windows, use `--basetemp work/contract-v1-target`. See `docs/api-contract.md` for all contract semantics and known integration limitations.
