# genai-coldstart-guard

Making a Databricks GenAI endpoint say "I'm warming up" instead of "the system is down."

## The problem this solves

A client's chat application was telling users **"Sorry, the system is down"** when the system was not down. Their Databricks GenAI serving endpoint runs with scale-to-zero enabled to save cost in lower environments, so the first request after an idle period has to wake the endpoint first. That cold start is slow, something upstream times out, and the user-facing layer collapses the timeout into a generic server-down error.

The message is simply wrong, and it erodes trust. The endpoint is starting, not broken. The honest message is closer to:

> The AI service is starting after a period of inactivity. Please try again in about a minute.

This repository is where that bug was investigated and a fix was prototyped safely, before touching the production Java backend.

## What this repository is (and is not)

This is a behaviour-validation harness plus a thin facade prototype. It does two jobs:

1. Reproduce and classify every state a Databricks serving endpoint can be in (warm, scaled-to-zero/cold, stopped, updating, errored) so the real backend behaviour is understood rather than guessed.
2. Prototype a facade that turns those states into honest, safe user messages while preserving the existing API contract.

It is not a product, a framework, or a general GenAI gateway. It is a focused sandbox for one specific production bug. The eventual fix may live in the client's Java backend; this POC exists to measure the real behaviour and prove the classification first.

## Why the cold start is invisible until you hit it

This is the heart of the bug, validated against Databricks docs: a scaled-to-zero endpoint still reports `state.ready = READY`. The status API cannot tell you that the next call will be slow. The cold start only surfaces as latency at inference time, which is exactly why a naive caller mistakes it for an outage. Meanwhile a genuinely stopped endpoint reports `NOT_READY` and returns HTTP 400, and an updating one reports `config_update = IN_PROGRESS`. Same "no answer right now", three very different causes.

![Databricks serving endpoint lifecycle](images/endpoint-lifecycle.png)

The per-state classification table and the facade decision flow are in [docs/databricks-endpoint-states.md](docs/databricks-endpoint-states.md).

## Solution space: what we considered and what we shipped

Improving the cold-start experience was one point on a spectrum of options, from "just classify honestly" to "re-architect for streaming". Mapping the whole space first made the trade-offs explicit and kept the first fix small. This release ships **Option A**; the rest are the upgrade path, taken only if measured behaviour justifies the extra moving parts.

| Option | Approach | Status |
| --- | --- | --- |
| **A** | Classification facade: check status, call inference once, return an honest message. No retry. | **Shipped (this release)** |
| B | Bounded backend retry (~30-90s) so a short cold start succeeds without the user retrying. | Deferred until real latency is measured |
| C | Async + polling: return `202 + requestId`, client polls for completion. | Future |
| D | Dev-only status endpoint to inspect sanitized endpoint state. | Optional |
| E | Warm-up endpoint that pre-wakes the endpoint before known usage windows. | Optional ops add-on |
| F | SSE / WebSockets streaming of warm-up progress. | Deferred (often restricted in enterprise) |
| G | Disable scale-to-zero / keep minimum capacity warm. | Rejected (defeats the cost goal) |

Option A is the only one strictly required to fix the bug (an honest message instead of "system is down"); B-G add machinery and are deferred by design. The decision and full trade-offs are recorded in [ADR-0001](docs/adr/ADR-0001-cold-start-facade-poc.md); the original investigation (the two "off" states, the discovery goals, and the full detail behind each option) is in [the research document](docs/research/databricks-cold-start-facade-research.md).

## Quick start (mock mode, no Databricks needed)

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload --port 8080
```

Open the Swagger UI at [localhost:8080/docs](http://localhost:8080/docs) to explore the contract interactively.

Mock mode simulates every endpoint state without a real Databricks connection. For example, watch a cold start get classified honestly:

```bash
curl -s -X POST http://localhost:8080/agentservice/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"demo","route":"mock:cold_start_timeout","messages":[{"role":"user","content":"Hello"}]}'
```

To exercise all simulated states at once, run `bash scripts/curl_examples.sh` (the full route list lives in that script).

## Pointing at a real Databricks endpoint

Set `BACKEND_MODE=databricks` and the `DATABRICKS_*` variables in `.env`, then call the same path. The facade reads endpoint status, calls inference once, and classifies the outcome into a safe message. All variables are documented in [.env.example](.env.example); what each classified response means is in [docs/databricks-endpoint-states.md](docs/databricks-endpoint-states.md).

## Safety

Do not commit `.env`. Do not log tokens. Do not expose raw Databricks errors to users.
