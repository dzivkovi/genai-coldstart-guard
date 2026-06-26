# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A small FastAPI proof-of-concept (`genai-coldstart-guard`) that sits in front of a Databricks GenAI serving endpoint and **honestly classifies upstream behaviour** instead of collapsing everything into a generic `500`/"system is down". The motivating problem is scale-to-zero cold starts: the first request after inactivity is slow while the endpoint wakes, and a generic error misleads the user. The guiding principle (from [ADR-0001](docs/adr/ADR-0001-cold-start-facade-poc.md)) is: *"Less orchestration. More honest classification."*

Deliberately out of scope for the first release: retries, polling, SSE/WebSockets, async job tables, scheduled warm-up, production auth. Do not add these without a deliberate decision - the simplicity is the point. FastAPI is the POC language; the eventual production target may remain Java, so the API contract matters more than the implementation.

## Commands

```bash
pip install -e ".[dev]"           # install with dev deps (pytest, ruff)
uvicorn app.main:app --reload --port 8080   # run locally; Swagger at /docs
pytest                            # run all tests
pytest tests/test_mock_routes.py::test_success_fast   # single test
ruff check .                      # lint (line-length 100)
bash scripts/curl_examples.sh     # exercise every mock route against a running server

# Regenerate the README lifecycle diagram after editing docs/endpoint-lifecycle.mmd
mmdc -i docs/endpoint-lifecycle.mmd -o images/endpoint-lifecycle.png -b white -w 760
```

## Architecture

The system is a **classification facade** with two interchangeable backends that both funnel into one shared set of response builders.

- [app/main.py](app/main.py) - the only two API routes (`POST /agentservice/agent/chat`, `POST /agentservice/agent/feedback`) plus `/health`. Dispatches to the mock or databricks backend based on `BACKEND_MODE`. `to_http_response()` is the key compatibility shim (see below).
- [app/responses.py](app/responses.py) - the canonical outcome vocabulary: `ok_response`, `error_response`, `warming_response`, `stopped_response`, `updating_response`. **All classifications across both backends must go through these** so mock and real behaviour stay identical in shape. Add new outcome types here, not inline.
- [app/mock_backend.py](app/mock_backend.py) - simulates outcomes by switching on `request.route` (e.g. `mock:cold_start_timeout`, `mock:databricks_stopped`). Mock mode is the safe stand-in for the real endpoint's failure taxonomy; keep the route list in sync with README and `curl_examples.sh`. Two routes are special: `mock:cold_start` is **stateful** (warms up over `MOCK_WARMUP_SECONDS`, then succeeds, then re-cools after `MOCK_IDLE_RESET_SECONDS` idle - emulates the real scale-to-zero lifecycle); `mock:state:<ready>:<config_update>` runs a fake state through the **real** `classify_state()`, the only mock path that exercises the classifier.
- [app/databricks_client.py](app/databricks_client.py) - real backend. Flow: check endpoint state API -> `classify_state()` -> short-circuit if stopped/updating -> call `/invocations` **once** (no retry) -> map timeouts/transient 5xx to `warming`, auth/4xx to controlled messages. `classify_state()` keys off the real `state.ready` and `state.config_update` fields (there is no `STOPPED` enum - see below). `build_databricks_payload()` and `invoke_databricks()` are the two functions to edit if the work endpoint expects a different schema/route.
- [app/models.py](app/models.py) - Pydantic request/response shapes. These mirror an existing Java AI Agent contract and must be preserved (`predictions[]` with `answer`/`citations`/`latency`/`success`/`error_message`, top-level `status`).
- [app/config.py](app/config.py) - `pydantic-settings`, loads from env / `.env`.

### Two behaviours that are easy to get wrong

1. **HTTP-200 compatibility mode.** `COMPATIBILITY_HTTP_200=true` (default) means every response goes out as HTTP `200` with the real status in the payload's `status` field (because the legacy Java client expects 200s). Set `false` to emit real status codes. The wire code and the semantic `status` are intentionally decoupled in `to_http_response()` - a warming response carries payload `status: 503` regardless of the HTTP code, and adds `Retry-After`/`Cache-Control` headers when payload status is 503.

2. **Conservative state classification.** The Databricks `state` object has only two fields, `ready` (READY|NOT_READY) and `config_update` (NOT_UPDATING|IN_PROGRESS|UPDATE_FAILED|UPDATE_CANCELED, older name `update_state`). There is **no `STOPPED` enum**: a stopped endpoint is `ready:NOT_READY` with `config_update` not IN_PROGRESS, and inference returns HTTP 400. `classify_state()` keys off these fields and maps unknown shapes to `updating` (never `ready`), so unrecognised states never send real user workload to a possibly-not-ready endpoint. The full validated model, the per-state table, and diagrams are in [docs/databricks-endpoint-states.md](docs/databricks-endpoint-states.md); the one open item is confirming the real stopped-endpoint status JSON against a live endpoint.

## Conventions

- New upstream outcomes -> add a builder in `responses.py`, then wire it into both backends and the mock route table.
- Never expose raw Databricks errors to users; never log tokens; never commit `.env`.
- Two backends must stay behaviourally parallel: a new mock route should correspond to a real classification path and vice versa.
- Decisions of consequence are recorded as ADRs in [docs/adr/](docs/adr/) (see [template.md](docs/adr/template.md)); broader context lives in [docs/research/](docs/research/).
