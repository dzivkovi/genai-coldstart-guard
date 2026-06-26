# Cold-start aware facade for cost-aware Databricks GenAI serving

This project preserves a simple existing Java-style AI Agent API contract while helping test and classify Databricks serving behaviours such as:

- warm success
- slow success
- scale-to-zero cold start / timeout
- stopped endpoint
- updating / not ready endpoint
- upstream 5xx
- auth/config errors
- bad request
- guardrail block
- no grounding

The first goal is not to build a smarter orchestration engine. The first goal is to stop collapsing every upstream issue into a generic `500` or "system is down" message.

## Why this exists

Some GenAI systems use custom Databricks model-serving endpoints with scale-to-zero enabled to reduce cost. That is sensible for low-volume or lower environments, but the first request after inactivity may be slow while the serving endpoint wakes up.

A generic error message is misleading in that case. The user-facing message should be closer to:

> The AI service is starting after a period of inactivity. Please try again in about a minute.

This repo lets you test those behaviours safely before changing a production Java backend.

## API contract preserved

The service exposes the same two paths:

```text
POST /agentservice/agent/chat
POST /agentservice/agent/feedback
```

Primary request shape:

```json
{
  "conversation_id": "demo-1",
  "route": "mock:success_fast",
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    }
  ]
}
```

Primary response shape:

```json
{
  "conversation_id": "demo-1",
  "conversation_response_id": "resp-...",
  "predictions": [
    {
      "answer": "This is a successful mock response.",
      "citations": [],
      "image_paths": [],
      "latency": 0.42,
      "success": true,
      "error_message": null
    }
  ],
  "user_feedback": null,
  "error": null,
  "status": 200
}
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -e ".[dev]"

cp .env.example .env

uvicorn app.main:app --reload --port 8080
```

Open Swagger UI:

```text
http://localhost:8080/docs
```

## Mock mode

Default mode is mock mode:

```bash
export BACKEND_MODE=mock
```

Test:

```bash
curl -s -X POST http://localhost:8080/agentservice/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "demo-1",
    "route": "mock:success_fast",
    "messages": [{"role": "user", "content": "Hello"}]
  }' | jq
```

Supported mock routes:

```text
mock:success_fast
mock:success_slow
mock:cold_start_timeout
mock:databricks_stopped
mock:databricks_updating
mock:bad_request
mock:auth_error
mock:upstream_503
mock:guardrail_blocked
mock:no_grounding
```

## Compatibility mode

Some Java APIs return HTTP 200 even when the payload contains an application-level error status.

This project supports both behaviours.

Default:

```bash
export COMPATIBILITY_HTTP_200=true
```

In compatibility mode, even a warming response returns HTTP `200`, but the payload contains:

```json
{
  "status": 503,
  "error": "The AI service is starting after a period of inactivity. Please try again in about a minute."
}
```

To use proper HTTP status codes:

```bash
export COMPATIBILITY_HTTP_200=false
```

Then warming returns HTTP `503`.

## Databricks mode

To call a real Databricks endpoint:

```bash
export BACKEND_MODE=databricks
export DATABRICKS_HOST="https://adb-xxxx.azuredatabricks.net"
export DATABRICKS_TOKEN="dapi..."
export DATABRICKS_ENDPOINT_NAME="your-serving-endpoint"
export DATABRICKS_TIMEOUT_SECONDS=30
export COMPATIBILITY_HTTP_200=true
```

Then call the same app endpoint:

```bash
curl -s -X POST http://localhost:8080/agentservice/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "real-test-1",
    "messages": [{"role": "user", "content": "Reply with one short sentence."}]
  }' | jq
```

The facade will:

1. Check Databricks endpoint status.
2. If stopped/updating/not ready, return a controlled message.
3. If ready, call inference once.
4. If inference times out or returns transient failure, classify as warming/unavailable.
5. Return the preserved AI Agent response shape.

## Important scope

This is intentionally simple.

First release does **not** implement:

- automatic retries
- polling
- SSE
- WebSockets
- async job table
- scheduled warm-up
- production auth

Those can be added later after real latency behaviour is measured.

## Useful environment variables

| Variable | Default | Purpose |
|---|---:|---|
| `BACKEND_MODE` | `mock` | `mock` or `databricks` |
| `COMPATIBILITY_HTTP_200` | `true` | Preserve Java-style HTTP 200 responses |
| `DATABRICKS_HOST` | empty | Databricks workspace URL |
| `DATABRICKS_TOKEN` | empty | Databricks PAT/token |
| `DATABRICKS_ENDPOINT_NAME` | empty | Serving endpoint name |
| `DATABRICKS_TIMEOUT_SECONDS` | `30` | Inference request timeout |
| `RETRY_AFTER_SECONDS` | `60` | Hint returned to client |
| `MOCK_SLEEP_SECONDS` | `0` | Optional global mock delay |

## Safety

Do not commit `.env`.

Do not log tokens.

Do not expose raw Databricks errors to users.

## Recommended first work test

1. Run in mock mode locally.
2. Confirm the Swagger/OpenAPI contract.
3. Clone at work.
4. Set Databricks env vars.
5. Call against a lower-environment Databricks serving endpoint.
6. Capture:
   - endpoint status
   - first request latency
   - timeout behaviour
   - second request latency
   - response classification
