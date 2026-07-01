# Cold-start facade POC for Databricks GenAI serving endpoints

**Status:** proposed

**Date:** 2026-06-25

**Decision Maker(s):** Daniel Zivkovic

## Context

Some Databricks GenAI serving endpoints may use custom model-serving infrastructure with scale-to-zero enabled to reduce cost in lower-volume environments.

That cost optimization creates a user-experience problem. When a scaled-to-zero endpoint receives the first request after inactivity, the endpoint may need to start serving capacity, load the model, and become ready before returning a response. If the backend, gateway, browser, or HTTP client times out first, the user may see a generic failure such as:

```text
Sorry, the system is down.
```

That message is often misleading. In this case, the service may not be down; it may be starting after inactivity.

The project needs a small proof-of-concept that can validate this behaviour safely without first changing the production Java backend. The POC should preserve the existing client-facing API shape as much as possible so that behaviour can be tested locally and later pointed at a real Databricks serving endpoint.

The initial implementation is intentionally narrow. It is not a full GenAI gateway, retry framework, observability platform, or guardrail orchestration system.

## Decision

Build a small FastAPI POC named `genai-coldstart-guard`.

The POC will preserve the existing AI Agent API contract:

```text
POST /agentservice/agent/chat
POST /agentservice/agent/feedback
```

The first implementation will support two backend modes:

```text
BACKEND_MODE=mock
BACKEND_MODE=databricks
```

> Superseded: the two-mode `BACKEND_MODE` switch was later collapsed to route-driven dispatch with a single `MOCK_ENABLED` gate (mocks off by default, so a bare production deploy cannot be tricked into faking success). Dispatch is now driven by the request `route` field: no route hits the real endpoint, a `mock:*` route runs a simulation when `MOCK_ENABLED=true`, and any other route returns a controlled 400. The mock behaviours below still apply, now selected per-request by a `mock:*` route rather than by a server-wide mode.

In mock mode, the POC simulates common behaviours:

- warm success
- slow success
- cold-start timeout / warming
- Databricks endpoint stopped
- Databricks endpoint updating or not ready
- bad request
- auth/config error
- upstream 5xx
- guardrail block
- no grounding

In Databricks mode, the POC will:

1. call the Databricks serving endpoint status API,
2. avoid inference when the endpoint is stopped, updating, or not ready,
3. call inference once when the endpoint is ready,
4. classify timeout or transient failures as `warming` or `unavailable`,
5. return a controlled response using the preserved API shape,
6. log enough technical detail to validate real behaviour.

The first implementation will not add automatic retries, polling, SSE, WebSockets, async job tables, scheduled warm-up, or production-grade authentication.

## Consequences

### Positive Consequences

- Keeps the first experiment small and understandable.
- Preserves the current client-facing API contract.
- Allows local development without depending on Databricks availability.
- Allows office testing against a real Databricks endpoint using environment variables.
- Provides safer user-facing messages for cold-start-like failures.
- Helps distinguish `scaled to zero`, `stopped`, `updating`, `bad request`, `auth error`, and `upstream unavailable`.
- Creates a practical implementation of the research and ADR seed without turning it into a large framework.

### Negative Consequences

- Does not eliminate Databricks cold-start latency.
- Does not guarantee that the first user request succeeds.
- Does not yet implement retries or async UX.
- Does not yet provide end-to-end tracing.
- Databricks state classification may need refinement after observing real endpoint responses.
- FastAPI is used for the POC, but the final production implementation may remain Java.

## Alternatives Considered

### Option A — Minimal facade classification

- **Pros:** Simple, low-risk, preserves frontend/API shape, good first POC.
- **Cons:** User may still need to retry manually; does not hide cold start.
- **Status:** selected.

### Option B — Bounded backend retry

- **Pros:** May allow the first cold request to succeed without user retry.
- **Cons:** Ties up backend request resources, may conflict with gateway/browser timeouts, adds complexity before measurement.
- **Status:** deferred.

### Option C — Async request plus polling

- **Pros:** Better for long cold starts and avoids browser timeout limits.
- **Cons:** Requires frontend changes, request state storage, and more moving parts.
- **Status:** future option.

### Option D — Developer/status diagnostic endpoint

- **Pros:** Helps inspect sanitized Databricks endpoint state during POC validation.
- **Cons:** Extra endpoint; should not become a public client-facing contract by accident.
- **Status:** optional developer-only extension.

### Option E — Manual or scheduled warm-up endpoint

- **Pros:** Useful before demos or expected usage windows.
- **Cons:** Partially reduces cost savings and is not the core fix.
- **Status:** optional operational extension.

### Option F — SSE or WebSockets

- **Pros:** Better real-time progress UX.
- **Cons:** Often restricted in enterprise environments and unnecessary for first validation.
- **Status:** deferred.

### Option G — Disable scale-to-zero or keep minimum capacity warm

- **Pros:** Best latency and simplest runtime behaviour.
- **Cons:** Expensive and contrary to lower-environment cost goals.
- **Status:** rejected for the first POC.

## Affects

Initial source areas:

- `README.md`
- `.env.example`
- `pyproject.toml`
- `app/main.py`
- `app/models.py`
- `app/config.py`
- `app/mock_backend.py`
- `app/databricks_client.py`
- `app/responses.py`
- `tests/test_mock_routes.py`
- `scripts/curl_examples.sh`

## Related Debt

Follow-up work created by this decision:

- Validate real Databricks status payloads for stopped, updating, ready, and scaled-to-zero states.
- Measure first-request latency after idle and second-request latency after wake-up.
- Decide whether backend retries are worth adding after measurement.
- Decide whether diagnostic endpoints should remain local/dev-only or be removed.
- Add structured logging with request IDs.
- Consider OpenTelemetry spans once the basic behaviour is validated.
- Confirm whether the current Java frontend expects HTTP 200 with application-level status, or supports proper non-2xx responses.
- Add explicit documentation for Databricks endpoint types: custom model serving, Foundation Model API, and external model endpoints.

## Research References

Supporting research document to add separately:

- `docs/research/databricks-cold-start-facade-research.md`

Key external references captured in the research document include:

- Databricks custom model serving and scale-to-zero
- Databricks serving endpoint management and status APIs
- Databricks model serving timeout behaviour
- Databricks model serving limits
- HTTP 503 Service Unavailable
- HTTP Retry-After header
- HTTP 202 Accepted for possible future async option

## Notes

This ADR intentionally makes a narrow decision: build a small POC that implements the selected first approach from the broader research document.

The broader research document may contain more options and platform details than this ADR. That is expected. The ADR records the first implementation decision; the research document preserves the wider context and evidence.

The guiding principle for the first version is:

```text
Less orchestration. More honest classification.
```
