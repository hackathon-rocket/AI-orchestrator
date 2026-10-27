# Reference inspection and contract decisions

Inspected the supplied master specification in full before implementation. Inspected these references read-only from their existing locations; no backend or frontend source was modified:

| Reference | Inspected portion | Finding |
|---|---|---|
| `frontend.zip` | `frontend/src/api/types.ts` | Field/table shapes match the master. Most API operations are explicitly proposals awaiting backend agreement. |
| `backend-api.zip` | `DocumentController.php`, `ProcessDocumentOcr.php`, `OcrClient.php`, `routes/api.php` | Upload stores PDFs/images, dispatches OCR and saves `text` plus `raw` in extractions. The job marks `ocr_completed` or `ocr_failed`. |
| `eGrant_detail_Zadanie.pdf` | Technical context and Zadanie 1, especially pages 2–6 | Preserve tags, extract diverse documents, manager-configurable mapping, human review and honest missing values. Other hackathon tasks are outside scope. |
| `eGrant_Hackathon_Zadanie1_v2.docx` | Planning text | Useful context but older than backend source. Its statement about accepted upload MIME types is outdated. Its page-storage/Excel/AI flow is a proposal, not implemented backend behavior. |
| `eGrant MCP.pdf` | Introduction and operation catalog | Optional integration reference. No MCP access or eGrant write code is included. |
| `eGrant tagove funkcie.pdf` | Function catalog excerpts | Form expressions can reference external application state. They must not be evaluated by this service. |
| BBSK form 11824 JSON | Full structure and accounting-document subtree | Exact path, child types, readonly flags and select keys match the master. |
| Sila komunity form 10318 JSON | Full structural traversal | Forced source fields and a specialized `BUDGET`/`BUDGET_SECTION` tree. This is not an ordinary accounting-document `TABLE_LIST`. |
| Magna form 8812 JSON | Full structural traversal | Different root sections, standard activity tables and specialized multivalue financial sections; no justified direct invoice mapping is assumed. |
| `vyuctovanie (2).xlsx` | Sheet dimensions, headers and first rows | Structured accounting register with 640 rows and 17 columns, including payment/source and attachment links. It is not evidence for an unrelated invoice. No links were followed and no workbook data copied into fixtures. |

## Mismatches and safe choices

1. Laravel's GET endpoint returns its raw model with relations, not React's `ExtractionResult`. Laravel must construct `document` and copy this service's `fields`, `tables`, `warnings` into the frontend response.
2. Internal Laravel statuses `ocr_completed` and `ocr_failed` are outside React's status union. Translate them in Laravel, as described in the integration guide. Python never updates those states.
3. Laravel currently accepts PDFs/images but not XLSX. Structured Excel is supported here, but Laravel still needs an authorized upload/preparation path. No `AnalyzeDocument` integration is presumed to exist.
4. OCR `raw` is retained by Laravel, but no guaranteed page-level OCR response schema was established from the source. Request page indices must come from actual OCR metadata. Combined `text` uses a null page.
5. React has no select or attachment `FieldType`. Editable select suggestions use `type=string` with exact option keys. Attachments are omitted. Internal provenance/status remain in `extracted_data`; mapped fields now also include optional status/reasons/cell-locator metadata. The original React keys and warning arrays remain unchanged.
6. BBSK's file name and some body text refer to different regional/program contexts (including TSK). This service uses verified structural tags, not filename-derived grant eligibility or legal interpretation. Labels are preserved, including the source table name's spelling.
7. BBSK `datum_1` means payment, `datum` means delivery/services, and `suma_dotacia` is grant-funded share. None are inferred from issue/due dates or the invoice total.
8. The expense heading refers to contract-authorized expenditure. A document description is only a reviewable description proposal, not verification of budget eligibility.

Only minimal relevant schema metadata is retained. BBSK's one table and ancestor section are in `configs/forms`; minimal real alternate snapshots are in `tests/fixtures/forms`. `alternate_demo` is synthetic and is never presented as an approved mapping for Sila or Magna.
