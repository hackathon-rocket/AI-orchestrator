# Verification

After the readability and dead-code cleanup, all 547 tests, Ruff lint and formatting checks, and the localhost HTTP smoke test were rerun successfully on Windows. Three formatting differences were corrected. The other checks below were completed during the earlier review. Hosted CI and a Docker image build were not run locally. CI covers Python 3.12 and 3.13 and includes the localhost HTTP smoke test.

## Executed checks

| Command / check | Result |
|---|---|
| `uv run pytest` | 547 passed |
| `uv run ruff check .` | All checks passed |
| `uv run ruff format --check .` | 86 files already formatted |
| `uv run python -m compileall -q app scripts` | Passed |
| `uv run python -m scripts.evaluate` | Two mock cases and 12 scripted proposal cases passed |
| `uv run python -m scripts.smoke_http` | Authenticated analysis, inline mapping and XLSX round trips passed |
| JSON fixtures/configuration/examples | 27 files parsed successfully |
| Local Markdown links | No broken targets |

The initial test run passed 516 cases; one setup failed because Windows denied access to the existing system pytest temporary directory. The complete final run used `TEMP` and `TMP` pointing to a new directory under ignored `work/`, without changing system permissions. No application test remained failing.

## Review changes

- Removed unused preparation state and evidence wrappers, repeated normalization/index construction, redundant exception handlers and unused mapping state.
- Shared the OpenAI-compatible client lifecycle while retaining one bounded retry loop and one extraction agent.
- Fixed authentication and body-limit bypasses when the application runs under a URL prefix. Added regression coverage for both POST endpoints.
- Preserved registered readonly protections against supplied schemas; rejected ambiguous table-column ownership and corrected empty `value_map` behavior.
- Required structural support for workbook currency hints and kept conflicting currencies visible when long source citations need shortening.
- Deferred deployment settings until startup, isolated offline tools from dotenv/environment settings, and ensured resource cleanup continues after a cleanup failure.
- Fixed body replay after disconnect, case-insensitive media-type handling, zero-valued cell limits and malformed provider error metadata.

Response envelopes, React fields/tables/warnings, source coordinates and `requires_review=true` remain intact. OpenAPI now also documents existing body-timeout and XLSX server-error responses. Relevant behavior and limitations are described in [architecture](architecture.md), [mapping](egrant-mapping.md) and [security](security.md).

## HTTP results

The smoke command started and stopped its own temporary Uvicorn process with an ephemeral token and isolated mock settings:

- Health, readiness, version, docs and OpenAPI: 200.
- Unauthenticated analysis: 401.
- Authenticated analysis, dynamic mapping, XLSX preparation and subsequent analysis: 200.
- Payment date and grant-funded amount remain null; human review remains required.

Synthetic [response examples](examples/mock_response.json) and the [OpenAPI snapshot](examples/openapi.json) were refreshed. Console output contains test metadata; the token is neither printed nor persisted.

## Limits

No real keys, paid provider requests, real-document accuracy evaluation, external repository changes, publication or push were involved. Mock/scripted metrics check deterministic regressions; they do not establish LLM accuracy. Docker execution and hosted CI remain unverified locally. Laravel still owns OCR, storage, queues, authorization and final confirmation.
