# eGrant AI Agent Service

Standalone Python 3.12+ FastAPI service that returns grounded document extraction **proposals for human review**. It does not store documents, run OCR, call eGrant, or confirm values. Laravel owns those responsibilities. React receives the existing `fields`, `tables`, and `warnings` shapes after Laravel adds its `document` summary.

Version 0.2 adds contextual validation, safe review suggestions, document currency evidence, request-scoped mappings, OCR language normalization and authenticated XLSX preparation. The service implements a single Pydantic AI extraction agent, provider adapters, deterministic evidence/format/arithmetic checks, and schema-validated JSON mapping. **Mock mode is a labelled-text fixture recognizer, not a general document understanding model.** Real provider adapters are tested with simulated HTTP; no paid requests were made.

## Start locally

Install Python 3.12+ and `uv`, then run from this repository:

```sh
uv sync --frozen
```

PowerShell:

```powershell
Copy-Item .env.example .env
$env:AGENT_API_KEY = (uv run python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:LLM_PROVIDER = "mock"
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

Bash:

```sh
cp .env.example .env
export AGENT_API_KEY="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export LLM_PROVIDER=mock
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-access-log
```

Alternatively create a venv, activate it and run `python -m pip install .` for runtime dependencies. `uv sync --frozen` includes the development tools from the lockfile.

The service refuses analysis and returns readiness 503 until a service token of at least 16 characters is configured. Health, version, docs and OpenAPI stay available. For Docker put a locally generated token into `.env`; environment variables in a terminal are not automatically persisted there.

```sh
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/ready
curl http://127.0.0.1:8001/version
curl -X POST http://127.0.0.1:8001/v1/analyze \
  -H "Authorization: Bearer $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @tests/fixtures/clean_invoice.json
```

PowerShell request (in a shell where the same `AGENT_API_KEY` is set):

```powershell
Invoke-RestMethod http://127.0.0.1:8001/v1/analyze -Method Post `
  -Headers @{ Authorization = "Bearer $env:AGENT_API_KEY" } `
  -ContentType 'application/json; charset=utf-8' -InFile tests/fixtures/clean_invoice.json
```

Open [Swagger UI](http://127.0.0.1:8001/docs). The synthetic invoice maps to one row in `rozpocet.zoznamuctovnychdokladov`: invoice `FA-2026-001`, gross `1230`, delivery `2026-09-30`, type `FA`, evidence on source page 3. Payment date, grant share, expenditure class and ordinal remain null. Two invoice lines remain in `extracted_data.line_items`.

## Verify offline

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python -m compileall -q app scripts
uv run python -m scripts.evaluate
uv run python -m scripts.smoke_http
```

Tests block external socket connections, allowing loopback solely for Windows event-loop plumbing. Provider tests use `httpx.MockTransport`, never an external endpoint. The smoke command starts a temporary localhost service with a fresh ephemeral token, checks authenticated BBSK analysis, inline mapping and XLSX preparation/analysis, saves a synthetic [response](docs/examples/mock_response.json) and [OpenAPI](docs/examples/openapi.json), then stops the process.

Offline helpers always use mock defaults and ignore `.env` and process configuration, including provider credentials, resource limits and mapping paths:

```sh
uv run python -m scripts.analyze --input tests/fixtures/clean_invoice.json
uv run python -m scripts.analyze --input tests/fixtures/structured_excel.json
uv run python -m scripts.analyze --ocr-json exported-ocr.json --output work/result.json
uv run python -m scripts.analyze --xlsx synthetic.xlsx --output work/result.json
```

The OCR helper accepts `pages: [{page,text}]` or top-level `text`. With only combined text it returns `page: null`; it does not invent physical pages. Excel retains sheet/cell coordinates and formula strings, and never evaluates formulas. Laravel can also send XLSX bytes to authenticated `POST /v1/prepare-xlsx`, then pass the returned `content` to `/v1/analyze`. No uploaded files are stored. See the [API contract](docs/api-contract.md).

## Provider configuration

| Provider | Configuration | Output mode |
|---|---|---|
| `mock` | No provider key or network | Deterministic synthetic rules through Pydantic AI |
| `openai` | `LLM_MODEL`, `OPENAI_API_KEY`, `ALLOW_REMOTE_LLM=true` | Responses API, native strict schema, `store=false` |
| `openai_compatible` | `LLM_MODEL`, `LLM_BASE_URL` ending in `/v1`, optional `LLM_API_KEY` | `LLM_OUTPUT_MODE=prompted` by default |
| `openrouter` | `LLM_MODEL`, `OPENROUTER_API_KEY`, `ALLOW_REMOTE_LLM=true` | Chat Completions at the configured adapter's fixed OpenRouter URL |

Set model IDs to models approved for your environment. No production model is selected implicitly. For compatible endpoints choose `native` only when JSON Schema is supported, or `tool` only when tool output is supported; `prompted` requests JSON text without tools or JSON mode and validates it locally. An incompatible mode fails safely; there is no automatic second model or paid fallback. Unauthenticated local SDK requests use the non-secret placeholder bearer string `local-no-auth`.

Remote providers are disabled until explicitly enabled. Non-loopback compatible endpoints also require `ALLOW_REMOTE_LLM=true` (including an approved private company gateway). Endpoint URLs are server configuration, never request parameters; redirects are disabled. Configure HTTPS for remote/private gateway traffic. Readiness validates configuration without contacting any model. Capability probing is intentionally not automatic because it could incur cost. See [architecture](docs/architecture.md) and [security](docs/security.md).

## Docker

```sh
docker compose up -d --build agent
```

The image uses Python 3.12, frozen dependencies, a non-root user and a health check. Compose binds only localhost and mounts a read-only filesystem. No MongoDB, queue, OCR, Redis or model server is required. Docker must be installed separately; local verification limitations are recorded in [verification](docs/verification.md).

The Compose project name is fixed to `egrant-ai-agent`, so moving the repository does not create a second service competing for port 8001. Source code is copied into the image, not mounted from the host: after code changes run `docker compose up -d --build agent` from this repository. `docker compose restart` or restarting Docker Desktop reuses the existing image and does not load those changes. See [classification/runtime diagnostics](docs/classification-diagnostics.md) if an API response still has the old behavior.

## Integration and mapping

- [Reference inspection and mismatches](docs/reference-review.md)
- [API contract and limits](docs/api-contract.md)
- [Laravel integration](docs/laravel-integration.md)
- [eGrant schema and mapping rules](docs/egrant-mapping.md)
- [Evaluation and confidence policy](docs/evaluation.md)
- [Security and deployment](docs/security.md)

A new form can supply optional `form.mapping` plus its authoritative `form.schema` without server registration. See the [runnable request](docs/examples/dynamic_request.json) and [mapping rules](docs/egrant-mapping.md). `slk+eng` and `ces+eng` are normalized to `sk+en` and `cs+en`.

The BBSK template is a minimal sanitized subtree derived from the supplied real export. Real alternate snapshots cover parsing; an explicitly synthetic alternate mapping demonstrates changing destinations without modifying extraction code. Full proprietary archives and financial workbook contents are excluded.

Remaining integration work is Laravel's `AiClient` and queued analysis job, persistence and frontend result assembly; these repositories were not modified. Real Slovak scan accuracy, model-specific capability and production throughput need an authorized, labelled evaluation. The baseline intentionally omits RAG, training, autonomous tools, external queues and eGrant submission.

See [verification](docs/verification.md) for the latest checks and remaining limits.
