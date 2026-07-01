# Latency axes: where a request-serving app loses time on Databricks (and the native fix)

Generic performance patterns for a request-serving web or agent app on Databricks - a thin router / API / orchestration layer that calls models, a vector index, and a state store. Each item is a common engineering anti-pattern and its Databricks-native fix, written as a generalizable pattern, not anyone's specific code.

Think of total request latency as a sum of independent **axes**. Most axes below are overhead a thin serving layer adds *around* generation - mostly avoidable. The last two (delivery mode, release gate) are different: they address the generation wait itself - not by making it shorter, but by changing how it is delivered and how long it *feels*. Measure each axis in isolation before fixing.

Pair this with [Databricks Apps vs Model Serving](databricks-apps-vs-model-serving.md) (where to host) and [the endpoint state model](databricks-endpoint-states.md) (cold-start classification).

## Axis 1: synchronous reads on the hot path that the response doesn't need

A request often does lookups whose result isn't used to produce the answer - resolving an identity, fetching a record needed only for logging. If that read is synchronous and on the request path, it blocks the user for nothing. **Fix:** before responding, do only the reads the response actually depends on; defer the rest to the same background task that persists the result. This is usually the single biggest free win, because it removes a round-trip from every request.

## Axis 2: per-request point reads against a SQL Warehouse

A SQL Warehouse is an analytical engine - JVM startup, query planning, no connection pooling, and a 2-6s serverless cold start. Using it for per-request OLTP point reads (identity, history, settings) puts that cost in the hot path. The naming makes this easy to miss: a "Delta table" or "lakehouse" sounds like a general-purpose database but is analytical, and its ACID transactions are about batch-write consistency, not low-latency single-row operations. **Fix:** put per-request state on Lakebase (serverless Postgres - pooled, sub-second, scales to zero). Background on why the names hide this: [Databricks storage: analytical vs transactional](databricks-storage-analytical-vs-transactional.md). Detail and sources: [Databricks Apps vs Model Serving](databricks-apps-vs-model-serving.md).

## Axis 3: a liveness check on every connection checkout

Validating a pooled connection with a `SELECT 1` on every checkout adds a full round-trip before the real query - negligible on a local DB, not on a serverless warehouse. **Fix:** validate only after idle, or rely on a proper connection pooler for liveness.

## Axis 4: outbound HTTP without connection reuse or a timeout

Calling a downstream model/endpoint with a fresh HTTP connection each time re-does TCP + TLS per request; and no timeout turns a downstream cold start into an open-ended hang. **Fix:** a shared, pooled HTTP client/session (keep-alive) plus an explicit timeout - and classify the timeout as "warming" rather than surfacing a failure.

## Axis 5: background work on a small fixed pool with long retries

Fire-and-forget writes on a small thread pool, each retrying with multi-second backoff, can saturate the pool under load - tasks queue in memory and writes fall behind. It doesn't slow the individual user, but it is a throughput/reliability axis. **Fix:** a bounded queue and a durable, low-latency write path.

## Axis 6: a web app wrapped as a model (packaging mismatch)

Running a web framework (Flask/FastAPI) inside an MLflow pyfunc just to get an endpoint means an extra in-process dispatch hop and the full Model Serving cold start (image pull + weight load) for something that loads no weights. **Fix:** host the web app on Databricks Apps. Detail and sources: [Databricks Apps vs Model Serving](databricks-apps-vs-model-serving.md).

## Axis 7: delivery mode - a buffered response where a streamed one would do

This is usually the largest *perceived* win, and it is separate from every axis above: those trim time the request wastes before generation; this one changes how the generation itself is delivered.

Total generation time for an LLM turn is often 15-20s and is bounded by the model, not the serving layer - you generally cannot engineer it under 10s without changing the model, the prompt, or the retrieval. But the number the user actually feels is not total time; it is **time to first token (TTFT)** - how long the screen stays empty. A buffered endpoint returns the whole answer at once, so TTFT equals total time and the user watches a spinner for the full 15-20s. A streamed endpoint emits tokens as they are produced, so the user starts reading in 1-2s while the rest arrives underneath.

Why this changes complaints even though the system is no faster: an occupied wait feels shorter than an unoccupied one (Maister, "The Psychology of Waiting Lines"), and a system that visibly shows it is working is perceived as more valuable even when it is slower - the "labor illusion" (Buell and Norton). Streaming is operational transparency: the user sees work happening for them and stops experiencing the wait as dead time. In practice, switching a slow-but-buffered turn to streaming can end user complaints without changing total latency at all.

**Recommendation, and the thresholds to use.** Treat TTFT, not total time, as the latency target for interactive chat. A good streamed experience shows first output in 1-2s. The outer bound of *tolerable* time-to-first-byte is about **8-10s** - the old web "8-second rule," consistent with the ~10s limit for holding a user's attention (Nielsen's response-time limits). Use 8-10s as the starting "slow first byte" alarm threshold for logging, treated as a hypothesis to validate against real measurements, not as a proven SLO.

**The caveat that bounds the claim:** streaming does nothing for a cold start. On the first request after the endpoint has scaled to zero, there is no running process to emit a first token, so TTFT is dominated by the replica wake, not generation. Streaming improves the warm path; the cold path needs the honest "warming" classification instead (see [the endpoint state model](databricks-endpoint-states.md)). Scope any TTFT target to warm requests, and keep a separate policy for the cold-start first byte.

Which serving options can stream, and how each one bills when idle. Streaming is one column; the other is the real reason scale-to-zero gets asked for in the first place - **cost management**. Scaling a container to zero is only one way to stop paying for idle capacity; paying per token to a hosted or external model is another, and at low volume per-token math is often cheaper than keeping any container warm. So read the last column as "how you avoid paying for nothing," not as a pass/fail on scale-to-zero:

| Serving option | Streams? | Cost when idle |
| --- | --- | --- |
| Foundation Model APIs, pay-per-token - Databricks-hosted LLMs (e.g. Llama) | Yes (OpenAI-compatible SSE) | Per token - no idle cost; usually the best math at low volume |
| External models - governed access to a third-party LLM (e.g. GPT/Claude) | Yes (upstream-dependent) | Per token, billed by the third party - no idle cost |
| Foundation Model APIs, provisioned throughput | Yes | Normally reserved: billed while up, guaranteed capacity, no cold start. Scale-to-zero is available but not recommended - it drops the guarantee and reintroduces cold starts |
| Custom model serving, streaming interface (a `predict_stream` / agent generator) | Yes (SSE) | Scales to zero - no idle cost, but pays a cold start on wake |
| Custom model serving, plain single-return `predict()` | No (synchronous only) | Scales to zero - no idle cost, but pays a cold start on wake |
| Databricks Apps - host your own web app (FastAPI/Flask/Streamlit) | Yes, if you implement SSE yourself | Billed per compute-hour while deployed - no automatic scale-to-zero on idle |

Names follow the Databricks products: rows 1-5 are all endpoint types of one platform, Mosaic AI Model Serving (a single unified API) - Foundation Model APIs in either pay-per-token or provisioned-throughput mode, External models, and Custom models. Databricks Apps (row 6) is a separate product for hosting your own web app, not a Model Serving endpoint type.

Two points fall out of the table. First, on streaming: it does *not* force you off the managed serving platform. A custom model that exposes a streaming generator interface streams over the same platform and still scales to zero; a plain single-return predict cannot stream regardless of platform. The blocker is the model's **interface**, not the hosting platform - and changing the interface is a smaller move than changing where you host. Second, on cost: if the driver is purely low-volume cost, a pay-per-token hosted or external model sidesteps both the idle-cost question and the cold start entirely - no container to keep warm and nothing to wake - trading a fixed idle bill for a per-request cost that rises with volume and gives up some control over the model. Scale-to-zero and pay-per-token are two answers to the same cost question; pick by expected volume, not by habit.

**Fix:** deliver the answer as a token stream end to end. This is all-or-nothing: if any hop on the path buffers the full response (a router that reads the entire body before returning), it silently converts a streaming backend back into a synchronous one. Every hop - model, router/facade, client - must pass tokens through incrementally. Note that a stream also changes the response contract (an event stream, not a single JSON envelope), so a client built around "one body, read the status field" needs a deliberate contract change to consume it.

## Axis 8: release gate - a safety check that holds the whole response

Independently of delivery mode, where an output safety/guardrail check sits decides whether streaming is even possible. A guardrail that must inspect the *complete* generated response before releasing any of it defeats streaming by construction - you cannot stream tokens you have not yet cleared - so the endpoint falls back to buffered delivery no matter what interface the model exposes. If streaming looks impossible, this is often the real reason, separate from the model interface in Axis 7.

This is a genuine safety-versus-latency tradeoff, not a defect. Holding the full response to screen it (sensitive-content, leakage, or compliance review) is a legitimate, conservative choice; incremental chunk-level moderation can stream but inspects less context at once, which is strictly weaker. So "make the guardrail streaming-compatible" is a risk decision to take deliberately, not a free optimization. Grounding or hallucination checks that need the whole answer are a separate concern again, and can sit off the hot path.

**Fix (a decision, not a default):** if perceived latency matters and the safety posture allows it, move to incremental / chunk-level moderation or an optimistic-stream-with-retraction pattern so tokens can flow. If the safety posture requires a full-response gate, accept buffered delivery on that path and set TTFT expectations accordingly - do not promise a streamed experience the gate has removed.

## How to use this

Before generation even starts, a request can pay several of these in series (a hot-path read + a warehouse round-trip + a liveness check). The highest-leverage *actual*-latency moves are usually **Axis 1 + Axis 2**: stop blocking the response on reads it doesn't need, and move the reads it does need off the analytical warehouse.

The highest-leverage *perceived*-latency move is **Axis 7**: stream the response. It does not reduce total time, but it collapses the wait the user actually feels, and it is often the difference between a system users complain about and the same-speed system they are content with. **Axis 8** governs whether streaming is even reachable. Together they say the honest thing to promise on an 18-20s turn is not a sub-10s total - it is first output in a couple of seconds on the warm path, with an honest "warming" message on the cold one.
