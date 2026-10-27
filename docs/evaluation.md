# Quality and evaluation

Run `uv run pytest` for unit and integration checks, and `uv run python -m scripts.evaluate` for the explicitly labelled synthetic mock report. No provider keys or OCR server are required. External socket connections are blocked in tests. Windows loopback sockets are allowed because asyncio uses them internally.

Fixtures cover clean Slovak invoices, absent due/payment dates, bad OCR, invalid IČO, inconsistent totals, receipts, contracts, bank statements, prompt injection, and structured Excel. XLSX tests create a small reproducible synthetic workbook in pytest's temporary directory and verify cell and formula preservation. Tests assert exact values, evidence and mapping constraints, not just HTTP 200. Real form metadata is minimized and document contents are synthetic.

Provider integration tests use in-memory HTTP transports to inspect the official Responses request and native/prompted/tool compatible requests. They cover output validation retries, transient failures, exhaustion, timeouts, concurrency admission and cancellation cleanup. They verify code/SDK compatibility, not availability of a real endpoint or actual model extraction quality.

## Metrics

`tests/fixtures/ground_truth.json` explicitly annotates a small regression subset. The evaluation harness compares these annotations with validated/mapped output and reports:

- Extraction accuracy: correct non-null expected values / annotated non-null expected values.
- Missing-value accuracy: correctly null output / annotated absent values.
- Hallucination rate: output populated on an annotated absent field / all annotated predicted non-null fields (zero if no predictions). Wrong values on present fields lower extraction accuracy separately.
- Mapping accuracy: correct existing destination/value pairs / annotated destination/value pairs; an omitted null destination is not counted as correct.
- Median and maximum complete orchestration latency, measured locally.

The report includes denominators and marks `mode=synthetic_mock_only`. Perfect mock scores show regression consistency only; they are not measured real LLM accuracy, scan robustness or confidence calibration. Unannotated fields are excluded rather than silently counted as correct.

## Verification and confidence policy

Quotes must exist on the cited original page or exact cell. Comparison tolerates diacritics/case/whitespace; it cannot truncate numeric/identifier tokens or borrow text from another page. Small punctuation spacing changes trigger review. Values additionally need field semantics: explicit due-date evidence cannot verify payment/delivery, and supplier/customer identifiers remain distinct. Unrecognized semantics remain reviewable when literally grounded. Sensitive roles must be supported before mapping; safe text/otherwise supported review proposals may be mapped with status/reasons.

Confidence never exceeds the model signal: verified cap 0.85, semantic/OCR review cap 0.5, invalid-format/conflict cap 0.3, arithmetic conflict cap 0.4. Nulls always have zero confidence and null evidence. These values prioritize review; none are calibrated statistical probabilities. DIČ and Slovak VAT ID checks are format-only, IČO and IBAN also have checksum checks. An identifier check does not verify registration, bank ownership or tax status. Net + VAT = gross is checked only when all three source facts pass validation, using Decimal and configured tolerance; no absent amount is synthesized and line items are not double-counted into totals.

## Next evaluation stage

With explicit authorization, assemble labelled real Slovak documents across text PDFs, photos, poor OCR, multi-page layouts, receipts, contracts and statements. Keep source text, gold values, party roles, date meanings and page/cell evidence with independent annotation review. Run an approved model on a held-out set, score abstention and false filling, and calibrate UI priorities. The conservative label vocabulary will need expansion for real layouts; do so from labelled errors and preserve adversarial tests. No real scan/model quality claim is made by this baseline.


## Independent scripted-proposal suite

`tests/fixtures/scripted_cases.json` contains 12 synthetic raw structured proposals independent of the mock recognizer. They include a realistic multiline invoice with party sections, ambiguous/wrong-party identifiers, invented digits, due-versus-payment and net-versus-gross substitutions, missing/conflicting currency, unknown documents retaining facts, unrelated bank currency and safe review mapping. The annotated synthetic invoice is in `realistic_invoice.txt`; its identifiers are invented checksum-valid test values, not registry claims.

`uv run python -m scripts.evaluate` now includes `scripted_validation`, also runnable alone with `uv run python -m scripts.evaluate_scripted`. It scores only explicitly annotated fields and reports verified-correct, reviewable-correct, incorrectly extracted, false positives on expected-null fields, false rejections of expected-present values, correctly missing, status accuracy, mapping accuracy, correct-retention coverage and latency. Incorrect populated values count as incorrect predictions even when the expected field is present. Null output on an expected-present field is a false rejection. Correct-retention coverage includes verified and reviewable correct values; it does not reward always returning null.

The current saved report has 24 annotated fields: 14 verified correct, six reviewable correct, four correctly missing, zero incorrect predictions and zero false rejections. Six annotated mapped destinations are correct. These are deterministic regression checks of supplied synthetic proposals, not model recall, scan accuracy or production confidence. A held-out real-document benchmark remains necessary with explicit approval before paid execution.

Additional tests cover columns, source ambiguity, formula/header misuse, dynamic mapping rejection, schema protections, malformed/oversized XML/ZIP, dates and multi-sheet preparation, real HTTP round trips, provider permanent/transient errors, lazy-client cleanup, cancellation and deadlines. The initial 15 regression tests were red before fixes. Older React assertions now require the seven original keys while permitting the requested additive review metadata; the prior no-currency Excel assertion now requires a review status rather than a verified amount.
