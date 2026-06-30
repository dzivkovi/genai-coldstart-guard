# Cold-start handling: options and experiment prompts

A reusable playbook for handling Databricks scale-to-zero cold starts across a public upstream POC and a private downstream fork. Generic by design: no environment-specific names, endpoints, schemas, or credentials belong in this file.

## Two-repository workflow (upstream + private fork)

- **Upstream POC (this repo):** the language-neutral reference and sandbox. It holds the mock backend, the response vocabulary, the state model, and the experiment scaffolding. Safe to keep public.
- **Private downstream fork:** carries environment-specific config (auth, endpoint names, secrets) and the production wiring. Some organizations block pushes from private forks to public repos, so changes flow upstream -> fork (copy / cherry-pick), and the fork is the source of truth for anything environment-specific.
- **Rule:** private data never lands in the upstream. Chat exports, credentials, schema names, and endpoint URLs stay in the fork (and are gitignored even there). See the note on the consolidated chat export at the bottom.

## What a live Databricks scale-to-zero endpoint taught us

Observed against a live, self-deployed LLM serving endpoint (not hypothetical):

1. **HTTP 429 is the cold-start signal.** After idle, the first 2-3 invocations return `429` ("starting"), then succeed. On this platform the cold case surfaces as a **fast 429**, not a slow timeout. So classify **429 -> warming** (with `Retry-After`) as the primary cold signal; keep an invocation timeout only as a fallback. This lets the "starting" state be detected in about a second instead of after a long wait.
2. **Idle scale-down is fast (~5 minutes).** The endpoint scales to zero after only a few minutes of inactivity. Good for testing (cold is easy to reproduce) and relevant to any "assume cold if idle longer than the window" heuristic.
3. **Auth may be OAuth M2M, not a PAT.** Some workspaces use a service principal (client ID + secret, sometimes called "app ID / secret") instead of a personal access token. The app generates a short-lived OAuth token from those credentials at runtime. Config arrives via `.databrickscfg` / the Databricks CLI rather than a `.env` file; shell scripts export the environment before launching the app. This is orthogonal to the facade logic - the classification calls just reuse the token-generation helper.
4. **Status still reports READY while cold.** The management status API cannot see scale-to-zero; a cold endpoint reads `READY`. The cold signal is the 429/timeout on the actual invocation, never the status field.

## Options

| Option | What | Status |
| --- | --- | --- |
| A | Accurate classification: status pre-hook + `429` -> warming + timeout fallback | Core - implement first |
| B | Bounded server-side retry on 429 (hide the cold start behind a short retry) | Test against A (data-driven) |
| E | Triggered warm-up (page-load / pre-hook poke of the endpoint) | Layer on if A/B leave a gap |
| Logging | Latency + outcome instrumentation | Prerequisite - do with A |
| Guardrails | Surface input/output breach as a clean "blocked" outcome | Optional |
| C / F | Async + poll / streaming | Deferred (complexity) |
| G | Always-warm (provisioned concurrency) | Rejected (cost) |

## Design

- **Status pre-hook** (instant) catches stopped / updating / deleted (GET fails or `NOT_READY` / `IN_PROGRESS`). Treat `READY + UPDATE_FAILED` as still serving - do not fail it.
- **Real call** with a read timeout above the warm-latency ceiling (calibrate from logs).
- **Classify:** `200` -> ok; **`429` -> warming**; timeout -> warming; `400` -> request_error; `401/403` -> auth; other `5xx` -> warming/unavailable.
- **The "hide vs show" debate is an A/B test, not an argument.** Some stakeholders prefer users never see a "starting" message. That preference maps to Option B (retry silently behind a spinner). Build A and B both behind a flag, measure with real users and the latency logs, and let the data decide how much complexity is worth it.

## Refactor prompts (for an in-IDE coding assistant)

Each is self-contained. Adjust file/function names to the fork's actual structure.

### Prompt 1 - Latency + outcome instrumentation (do first)

> In the FastAPI app's real Databricks pass-through path, add timing instrumentation. Wrap the outbound call to the serving endpoint: record a monotonic timer before and after, and log elapsed milliseconds, the HTTP status code, and a classification label (ok / warming / stopped / updating / error). Emit one summary line per request: `[LATENCY] endpoint=<name> status=<code> outcome=<label> elapsed_ms=<n>`. Use the existing logger at DEBUG. Do not change behavior, only add logging. Goal: from a single cold hit, read the cold-start duration, the 429 count, and the warm baseline.

### Prompt 2 - Option A: accurate classification on the real call

> In the real Databricks pass-through, classify outcomes into the existing response builders (ok/warming/stopped/updating/error), in this order: (1) Optional pre-hook: GET the endpoint state via the management API using the OAuth token helper; if the GET fails/404 or state is NOT_READY with config_update IN_PROGRESS, return updating/stopped immediately; treat READY + UPDATE_FAILED as still serving. (2) Call /invocations once with a read timeout well above the warm latency. (3) Classify: HTTP 200 -> ok; HTTP 429 -> warming ("The AI service is starting, try again shortly" + Retry-After); timeout -> warming; 400 -> request_error; 401/403 -> auth (safe message); other 5xx -> warming/unavailable. Gotcha: a scaled-to-zero endpoint reports READY, so the cold case is detected by the 429/timeout on the call, not by the status check. Preserve the existing response shape.

### Prompt 3 - Option B: bounded retry (the "hide it" variant, to A/B test)

> Add a config flag COLD_START_RETRY_ENABLED. When on, if /invocations returns 429 or times out (the warming signals), retry server-side with exponential backoff (e.g., 2s, 4s, 8s) for a bounded total of ~30s. If a retry succeeds, return the answer normally (no "starting" message shown). If the budget is exhausted, fall back to the Option A warming response. Log each attempt and total elapsed. Keep it flag-controlled so "show starting message" (flag off) vs "retry silently" (flag on) can be A/B tested for UX and latency.

### Prompt 4 - Option E: triggered warm-up endpoint

> Add a lightweight POST /warmup route that fires a minimal request to the serving endpoint(s) to trigger scale-up, returns immediately (fire-and-forget, do not block on the result), and is throttled (skip if warmed within the last ~4 minutes, tracked in memory). Use the OAuth token helper for auth. Purpose: a front-end can call this on page load so the cold start overlaps with the user's read/type time. Log when a warm-up is fired vs skipped.

### Prompt 5 (optional) - guardrail outcome

> When the downstream response indicates an input/output guardrail breach, classify it as a distinct `blocked` outcome with a safe user message ("This request can't be answered as it's outside the allowed scope"), separate from errors and from warming. Do not expose internal breach details to the user.

## Outstanding

- Latency measurements are not yet wired (Prompt 1 closes this; everything else depends on the numbers).
- Calibrate the timeout and `Retry-After` from the first cold hit's logs.
- Port the chosen options from the FastAPI POC into the production tiers (e.g., a Java/Spring Boot middle tier and a web front-end) once validated. The classification logic is the part that transfers; auth and config are environment-specific.

## Note on chat exports

Raw assistant/chat exports of private code (e.g., `docs/research/consolidated_chat-*.md`) can contain credentials, schema names, and endpoint URLs. They are gitignored so they never reach the upstream. Keep them local, and rotate any credential that appears in one.
