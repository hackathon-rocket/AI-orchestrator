# Architecture

`HTTP/auth/body guard → input preparation → mapping configuration validation → one extraction agent → evidence and semantic verification → normalization and checks → mapping → review response`

`AnalysisOrchestrator` owns deterministic sequencing and the process-local concurrency gate. It checks content limits and resolves form/mapping destinations **before** calling the provider. Original pages and cell positions are immutable inputs; comparison normalization does not replace them. One request represents one source document, potentially with multiple pages. Split registers or bundles of independent invoices upstream.

The `LLMProvider` protocol exposes `extract(Content) -> ExtractionData` and `close()`. `MockProvider`, `OpenAIProvider`, and `CompatibleProvider` implement it. OpenRouter is a factory-selected compatible adapter. Routes, schemas, validators and the mapper contain no provider API logic.

All providers use the same single Pydantic AI agent and universal output schema. The mock supplies a `FunctionModel` with deterministic labelled-text results. Real providers use one classification/extraction call per attempt, no external function tools, no browsing and no conversation memory. The configured `tool` output mode uses only the typed output envelope; it exposes no action tools. Form exports and mapping labels are never included in model prompts.

The sole retry loop is `BoundedProvider.extract`: at most `1 + LLM_MAX_RETRIES` attempts, default three. SDK retries are zero; Pydantic AI retries are zero; each agent run is limited to one request. It retries timeouts, connection errors, 429, 5xx and invalid structured output, with bounded exponential delay. Other HTTP errors fail immediately. No provider switching or second model occurs. `LLM_TIMEOUT_SECONDS` bounds an attempt; `ANALYSIS_TIMEOUT_SECONDS` bounds the entire analysis. Admission waits up to `QUEUE_TIMEOUT_SECONDS` and returns 429 if full. Cancellation releases model work immediately; if a bounded validation worker is still running, its admission slot stays held until completion. Provider clients initialize lazily, close on initialization failures and are closed in lifespan cleanup.

The process is stateless and has no database or external queue. Multiple Uvicorn workers each have their own concurrency limit; size the deployment accordingly. Configure an ingress request/rate limit and Uvicorn's `--limit-concurrency` to bound simultaneous request-body buffering. Laravel owns durable retry policy and prevents duplicate persistence.

Deployment settings are loaded during application startup, so importing `app.main` does not read `.env`. Shutdown drains both worker services and closes the provider even when another cleanup fails. Offline helpers use explicit mock settings without dotenv or process-environment configuration.

## Provider API references

Implementation was checked against installed APIs and transport-tested without paid requests:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses): native schema output, still requiring semantic verification.
- [Pydantic AI OpenAI models](https://ai.pydantic.dev/models/openai/): Responses versus Chat model adapters.
- [Pydantic AI output modes](https://ai.pydantic.dev/output/): native, prompted and tool output contracts.

The repository's `uv.lock` pins the versions actually installed. Compatible endpoint capabilities are operator-declared, not discovered by potentially billable calls. `GET /ready` proves configuration is usable locally; it does not prove credentials, model availability or endpoint reachability.


## Validation stages

`EvidenceIndex` builds a request-local source index. Literal matching proves that the proposed value exists on the stated page/cell without changing digits. Local label/value associations, multiline labels, party sections, aligned columns and validated sheet headers then establish semantic roles. Classification requires a real heading, and conflicting document headings result in `unknown` without erasing independent facts.

Format checks normalize supported money/date/identifier forms and validate applicable checksums. Ambiguous grounded values remain `needs_review`; wrong known money/date roles are rejected. Conflicting values and totals reduce confidence and prohibit mapping. Currency resolution examines explicit document currency, gross-total currency and relevant amount/header evidence; unrelated bank-account currency is not assumed to describe invoice totals. Missing/conflicting currency prevents a verified amount and prevents EUR-restricted mapping.

Currency resolution reuses the request's evidence index. Workbook hints cannot associate an unrelated currency cell with an amount: separate unit cells must be adjacent or within the same structural amount column. Automatically discovered citations from long source lines retain bounded original snippets for every currency, preserving conflicts and coordinates.

Each returned fact includes machine-readable reasons. Mapper eligibility is private server state rebuilt on every validation pass, distinct from the provider's status/confidence. Safe grounded review proposals can be mapped while sensitive ambiguous roles and serious conflicts cannot. Mapping then checks target editability, type, select keys, currency restrictions and BBSK invariants. The API always requires human review.

CPU validation/mapping runs in bounded worker threads so the event loop can enforce response deadlines. Workers cannot be forcibly killed, but remain counted against admission after cancellation and are drained at shutdown. Input/output cardinalities and per-request indexing bound the work. XLSX preparation has its own bounded worker gate and cooperative parser deadline plus ZIP/XML resource checks; it has no model dependency.
