# Latency axes: where a request-serving app loses time on Databricks (and the native fix)

Generic performance patterns for a request-serving web or agent app on Databricks - a thin router / API / orchestration layer that calls models, a vector index, and a state store. Each item is a common engineering anti-pattern and its Databricks-native fix, written as a generalizable pattern, not anyone's specific code.

Think of total request latency as a sum of independent **axes**. The model/generation call usually dominates and is its own problem; the axes below are the ones a thin serving layer adds *around* it - mostly avoidable. Measure each in isolation before fixing.

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

## How to use this

Before generation even starts, a request can pay several of these in series (a hot-path read + a warehouse round-trip + a liveness check). The highest-leverage moves are usually **Axis 1 + Axis 2**: stop blocking the response on reads it doesn't need, and move the reads it does need off the analytical warehouse. The model/generation latency itself is a separate concern - measure it on its own.
