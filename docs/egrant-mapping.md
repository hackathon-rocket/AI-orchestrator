# Form parsing and manager configuration

`FormSchemaParser` walks recursive `children` and builds full destination paths, tracking table ownership separately. Local tags append to ancestor tags. Already dotted tags are treated as absolute; duplicate full paths are errors. Two sections may contain the same short child tag without collisions.

Supported containers are root, `SECTION`, `SUB_SECTION` and ordinary `TABLE_LIST`. Editable scalar/table string, text, number, currency, date, IBAN and select types are allowlisted. Other types remain inspectable but cannot receive suggestions. Specialized budget trees and repeated subsection structures need a dedicated integration contract; the baseline blocks those descendants rather than pretending they are ordinary tables.

Protection is inherited from ancestors: `config.is_readonly`, top-level readonly, `force_source_value`, conditional editability and readonly fixed-row values block writes. Numeric/string flags such as `0`, `"0"`, `1`, `"1"` are normalized. Non-forced `source_tag` alone does not lock an otherwise editable field. Forced sources always do. No form expression is evaluated. Tables without `add_rows` enabled are withheld because this API has no existing row identity context. Computed, static, attachment, ordinal-row-number and unknown types are never filled.

Selects use keys in `config.values`, never display labels. Dynamic roller sources cannot be resolved here and are skipped. Mapping scalar sources into table columns creates exactly one accounting-document row per request, not one row per invoice line. The universal line items preserve the latter.

Table columns must belong directly to a named table path. Tagless tables and nested or foreign column paths are not editable destinations. An explicit empty `value_map` matches no source values; omit `value_map` to retain a supported original value.

## BBSK mapping

Full table path: `rozpocet.zoznamuctovnychdokladov`.

| Universal fact | Column | Rule |
|---|---|---|
| invoice_number | c_dokladu | Grounded document number with safe validation state |
| total_gross | suma_doklad | Grounded gross invoice amount, explicitly evidenced EUR |
| delivery_date | datum | Explicit delivery/services date only |
| payment_date | datum_1 | Explicit completed payment date only |
| expense_description | nazov_vydavku | Quoted description proposal; human verifies contract/budget meaning |
| payment_method | uhrada | Exact supported `prevodom` or `v hotovosti` |
| classification | typ | Verified invoice → `FA` |

`suma_dotacia`, `druh`, `p_c` remain null. `uctovny_doklad` and `tovar` are omitted. The response warns that all five need human handling. Receipt is not automatically equated to `PPD` or `VPD`; invoice rules do not run for receipt, contract, statement or unknown classifications. A future separately approved mapping can support those types.

## Add a configuration

1. Place a sanitized authoritative form export in `configs/forms/new_schema.json` (or an administrator-controlled `FORM_CONFIG_DIR`).
2. Add a `new_mapping.json` with unique `id`, `form_id`, `schema_file`, `document_types`, `rules` and optional `reserved` destinations.
3. Each rule specifies an existing universal `source`, full `destination`, and optional explicit `value_map` dictionary for select keys. `required_currency` can restrict monetary mapping to an explicitly evidenced currency; `review_warning` adds a manager-defined review instruction. Destination identities must exist in the schema. No arbitrary code, expressions, paths or external lookups are supported.
4. Restart the service; local readiness validates all registered configurations. Send matching `form.id` and `form.mapping_id`. Add fixture assertions for the intended meanings.

See `alternate_demo_mapping.json`: it maps invoice number and gross amount into two different scalar tags without extraction-code changes. That example is synthetic. The real Sila/Magna snapshots exercise parsing and protection but do not imply that an invoice supplier/customer is the grant applicant or that invoice amounts belong in a project's aggregate budget.

In legacy file mappings, protected/unsupported destinations are skipped with diagnostics. Inline mappings reject such rules before extraction; nonexistent destinations or invalid configured select maps fail before extraction. Missing required fields remain null and generate warnings, never fabricated values. Existing table rows, row updates, schema freshness and tenant permissions remain Laravel's responsibility. The shipped service only returns proposals.

The BBSK deployment config explicitly assumes EUR amounts. A conflicting, missing or foreign currency is not converted; the universal amount is retained but withheld from `suma_doklad`. Confirm this administrator-controlled currency policy when integrating the actual form. The expense-description rule also emits a warning that its contract/budget meaning still requires human review.

A manager UI can list parser-discovered editable targets, present the existing universal fact vocabulary, validate keys and preview synthetic results before saving JSON. This UI is intentionally not implemented in the independent service.


## Request-scoped dynamic mapping

The [complete runnable example](examples/dynamic_request.json) and [saved response](examples/dynamic_response.json) use a new, unregistered form. The mapping shape is:

```json
{
  "id": "manager_v1",
  "form_id": "new_manager_form",
  "version": "1",
  "document_types": ["invoice"],
  "rules": [{"source":"invoice_number","destination":"cislo"}],
  "reserved": []
}
```

Place it at `form.mapping`, with matching `form.id`, `form.mapping_id`, and the authoritative `form.schema`. A registered form may omit the schema and use its configured template. A new form must provide it. Mapping state lasts only for this request; there is no configuration write endpoint.

Validation rejects unknown sources/destinations, duplicate destinations, mappings into reserved/readonly/forced/computed/unresolved-select targets, incompatible date/money/IBAN sources, invalid select keys, foreign/nested table columns, unsupported document types and mismatched identities. A `value_map` is only accepted for selects in inline rules. JSON expressions are never evaluated. BBSK's canonical accounting paths retain protected destinations and fixed roles for gross amount, delivery date and payment date regardless of mapping ID; its gross destination still requires EUR. Known registered reserved/readonly protections cannot be relaxed by a supplied schema.

Safe `needs_review` proposals retain their status/reasons in mapped fields. Unsupported facts are null. Invalid identifiers, conflicts, mismatched roles or unsafe target types are not suggested. `datum_1` cannot be mapped from issue/due date and `suma_dotacia` cannot be mapped from invoice totals. Classification `unknown` never activates an invoice mapping. Exactly one document row is emitted; invoice line items do not multiply it.

This validates structural compatibility and explicit protected rules; it cannot infer every organization's business meaning from arbitrary new tag names. Laravel/administrators must approve mappings and authorize the chosen form, particularly applicant/customer roles and grant eligibility. No mapping creates or confirms an eGrant write.
