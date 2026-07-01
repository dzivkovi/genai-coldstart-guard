# Measuring where a Databricks serving request spends its time

"You cannot manage what you cannot measure." Before optimising a slow serving endpoint, put a number on each part of a request so people optimise in priority order instead of guessing. This is the measurement layer under [databricks-latency-axes.md](databricks-latency-axes.md) (which names the axes) - here we get the numbers.

Generic and Databricks-specific; no project or environment details.

## The two layers (measure both)

- Outside (the caller's view): wall-clock from the client, including network, the serving queue, and any cold start. Measure it at the edge - `time` on a CLI call, or a timer in the calling app. Example, a cold hit measured from a laptop was ~28s versus ~0.9s warm; that ~27s gap is invisible from inside the model.
- Inside (the model's view): where the WARM time goes, phase by phase (identity read, history read, retrieval, generation, writes). This is where the latency axes live.
- Outside total minus the inside wall-clock time (add up non-overlapping phases only, or measure one enclosing span) is approximately the overhead you cannot see from within: network, queue, cold start. If your inside phases overlap or double-count, this subtraction is not meaningful.

## Tier 1: lightweight timing with the standard library (governance-safe default)

Zero third-party dependencies - only `logging`, `time`, `contextlib`. It captures DURATIONS ONLY, never request/response payloads, so there is nothing sensitive to govern. This is the safe default where external libraries or data capture are restricted.

```python
import time, logging, contextlib
log = logging.getLogger("router")

@contextlib.contextmanager
def timed(label, spans):
    t = time.perf_counter()
    try:
        yield
    finally:
        spans[label] = (time.perf_counter() - t) * 1000.0  # ms
```

Wrap each phase and emit one summary line per request with each phase's share of the total:

```python
spans = {}
with timed("resolve_user", spans):  ...
with timed("history_read", spans):  ...
with timed("generate", spans):      ...
total = sum(spans.values())
# Log at DEBUG so it is off by default and turns on with LOG_LEVEL=DEBUG (see the gotchas below).
log.debug("[TIMING] total=%.0fms | " + " ".join(f"{k}=%.0fms(%.0f%%)" for k in spans),
          total, *[x for k in spans for x in (spans[k], 100 * spans[k] / total)])
```

The PERCENTAGE is the point: "history read = 6% of the request" changes a decision; "history read = 40ms" does not. A reusable helper implementing this lives in the companion `coldstart-echo-mlflow` repo (`timing.py`).

### Two gotchas that silently swallow your timing logs

This is standard Python `logging` - the only Databricks-specific parts are WHERE the level is set and WHICH logger is captured. Both bite if missed, and you get no output with no error, so people wrongly conclude "instrumentation does not work."

1. Route through the ROOT logger. The serving container captures the root logger - you will see lines like `WARNING:root:...` and `INFO : ...` in the endpoint Logs. A separate logger with `propagate=False` and its own handler writes AROUND that capture and shows nothing. Let your records propagate to root (the default); do not set `propagate=False`.
2. Set the level - it is not on by default. Nothing logs at DEBUG unless you lower the level. Set it from an endpoint environment variable so it is switchable without editing code:

```python
import logging, os
level = getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO)
root = logging.getLogger()
root.setLevel(level)
for handler in root.handlers:
    handler.setLevel(level)
```

Then to turn timing on, set `LOG_LEVEL=DEBUG` in the endpoint's Environment variables (the same screen as `ENABLE_MLFLOW_TRACING`); leave it unset or `INFO` in production. Note `LOG_LEVEL` is a name YOUR code reads (not a reserved Databricks variable), and changing any endpoint env var triggers a redeploy - it is a config change, not a live toggle.

### Detecting the cold-started request (there is no built-in signal)

Databricks gives you NO per-request cold-start marker - not in inference tables, not a response header, not the endpoint state API (`execution_time_ms` does not even include wake time). So to know a slow request was the one that woke a scaled-to-zero replica, you print your own. The reliable hook is the pyfunc `load_context`, which MLflow runs once when the model loads (i.e. when a serving process boots):

```python
def load_context(self, context):
    self._boot = time.monotonic()          # monotonic, not wall-clock
    self._n = 0
    self._lock = threading.Lock()
    logging.getLogger().warning("[COLDSTART] worker_boot pid=%d", os.getpid())

def predict(self, context, model_input, params=None):
    with self._lock:
        self._n += 1
        n = self._n
    logging.getLogger().debug("[COLDSTART] pid=%d cold_first=%s secs_since_boot=%.1f",
                              os.getpid(), n == 1, time.monotonic() - self._boot)
    ...
```

CAVEAT: this is "first request per WORKER," not per replica - a serving container runs several gunicorn workers and each one loads the model and fires `load_context`, so expect one `worker_boot` (and one `cold_first`) per worker per boot; log `os.getpid()` to reconcile. Keep it log-only for a small service (do not add response-schema fields or trace plumbing). A runnable example is in the sample repo https://github.com/dzivkovi/coldstart-echo-mlflow (`register_byvalue.py`). If MLflow Tracing is on (Tier 2), also stamp `mlflow.update_current_trace(tags={"cold_first": ...})` to make the cold request filterable in the trace UI.

### Logging slow calls (log by exception)

Same idea as the cold-start marker, applied to latency: measure each request's duration and emit ONE line only when the call is interesting - the cold-first request, an error, or slower than a threshold (`SLOW_REQUEST_LOG_THRESHOLD_SECONDS`). A healthy fast call logs nothing, so the signal stays quiet until something is worth reading; a slow call still succeeds and leaves a breadcrumb with its phase breakdown. The threshold is LOG-ONLY - it never times out or interrupts a request.

Set the threshold to your team's business-approved SLA. That SLA is one of two different things - time-to-first-token or time-to-full-response ([Axis 7](databricks-latency-axes.md)) - and they are not interchangeable. Honest limit: a synchronous pyfunc endpoint can only measure time-to-FULL-response (there is no first byte to time), so here "slow" means the full response crossed the threshold (the sample uses 8s, the old web "8-second rule"). Time-to-first-token only becomes measurable on a streaming-capable endpoint.

## Tier 2: MLflow Tracing (built-in, richest, but captures data)

MLflow Tracing is Databricks-native observability. Enabling it (`ENABLE_MLFLOW_TRACING=true` on the endpoint) records the inputs, outputs, and timing of instrumented steps as a "trace" - a tree of timed "spans" - and writes it to a configured Delta Inference Table, with a trace UI to inspect individual requests. For a custom pyfunc you mark the steps with `@mlflow.trace` or `with mlflow.start_span(...)`.

- Strength: the per-step timing breakdown AND the request/response payloads, queryable in a table, with a UI, without writing logging code. It is the native version of Tier 1 plus payload capture.
- Dependency: it requires an Inference Table (a Delta table) to write to.
- GOVERNANCE CAVEAT (important for regulated data): tracing captures request/response PAYLOADS into that table. In a regulated environment that is sensitive data landing in storage - only enable it with a masking/retention plan, or restrict which spans capture inputs/outputs. Tier 1 captures no payloads and avoids this entirely.
- Reference: https://docs.databricks.com/aws/en/mlflow3/genai/tracing

Rule of thumb: Tier 1 (logging) is the safe, always-available default for "where does the warm time go, in %." Tier 2 (tracing) is for deep, per-request observability when you have a governance plan for the captured payloads.

## Tier 3: cProfile - the deepest look (dev-only scalpel)

The three tiers form a ladder of timing depth: Tier 1 times the phases you mark, Tier 2 times spans (and captures payloads), Tier 3 times every function call.

`cProfile` (standard library) gives a full FUNCTION-LEVEL call tree - deeper than a hand-marked phase or a span. The trade-off, and the one thing that sets it apart from Tiers 1 and 2: it is heavy, so you do NOT run it on the production endpoint. Use it for a one-off "why is this ONE function slow" dive, locally or on a dev endpoint, one request at a time. Tiers 1 and 2 tell you WHICH phase is slow; Tier 3 tells you WHY inside that phase.

## Turning numbers into priorities

Once each axis has a number, prioritise by its share of p50/p95, in order. A migration or rewrite has defensible ROI only if the axis it targets is a meaningful share of the total (e.g. do not move storage off a warehouse if storage is under ~15% of p50 and generation dominates). Measure first; then the recommendation is evidence, not a hunch.
