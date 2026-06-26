# Databricks GenAI Serving Cold Starts Behaviour Research

## 1. Purpose

This document frames a cost-aware approach for Databricks GenAI serving endpoints that are allowed to scale to zero.

The goal is not to hide failures. The goal is to classify them honestly.

Instead of showing users:

```text
Sorry, the system is down.
```

the application should distinguish between:

```text
The AI service is starting after inactivity.
```

and true failure states such as stopped, unauthorized, misconfigured, updating, overloaded, or unavailable.

This document is intended as a knowledge-transfer brief and ADR starter for a coding assistant or engineering team.

---

## 2. Context

Current simplified flow:

```text
Browser
  → existing Java backend / proxy
    → Databricks GenAI serving endpoint
```

The Databricks endpoint may expose an OpenAI-compatible API shape, but the application should not assume that the endpoint exposes a universal OpenAI-style health or readiness route.

The Java backend currently acts as a server-side facade between the browser and Databricks. This is the right place to improve classification and user messaging because:

```text
- Databricks credentials remain server-side.
- The browser does not need Databricks-specific logic.
- The existing frontend can stay mostly unchanged.
- Backend logs can capture the real technical cause.
```

A Python FastAPI prototype may be used to validate behaviour, but the design should not be tied to FastAPI. The POC should prove Databricks behaviour and response classification, not prescribe the final implementation language.

---

## 3. Core Problem

For cost reasons, lower environments may keep Databricks serving endpoints configured with scale-to-zero.

That is reasonable for development, test, staging, demos, and low-volume environments.

The problem is user experience.

When a scaled-to-zero endpoint receives the first request after inactivity, the request may experience cold-start latency. If the Java backend, gateway, browser, or HTTP client times out first, the user may see a generic 500-style error.

That message is misleading. In many cases, the service is not down. It is starting.

---

## 4. Databricks Behaviour to Understand and Validate

There are two different “off” states.

### 4.1 Scaled to zero

This is automatic idle scale-down.

```text
Endpoint exists.
Endpoint configuration is active.
Serving capacity may be zero.
First inference request wakes it up.
```

Expected behaviour:

```text
1. User sends request.
2. Backend calls Databricks inference endpoint.
3. Databricks starts serving capacity / container / model.
4. The same request waits.
5. If warm-up finishes before timeout, the request may return successfully.
6. If backend/client/gateway timeout happens first, the backend sees timeout or transient error.
```

Important distinction:

```text
Scaled to zero is not the same as down.
```

### 4.2 Manually stopped

This is explicit administrative stop.

```text
Endpoint exists.
Endpoint is intentionally stopped.
Inference requests do not wake it up.
Databricks returns 400 for queries to stopped endpoints.
Endpoint must be started explicitly.
```

Important distinction:

```text
Stopped = hard off.
Scaled to zero = soft off.
```

---

## 5. Status API vs Inference API

These answer different questions.

### 5.1 Databricks status API

```http
GET /api/2.0/serving-endpoints/{endpoint_name}
```

Useful for:

```text
- Is the endpoint stopped?
- Is the endpoint updating?
- Is the endpoint generally ready to receive queries?
- Did an endpoint update fail?
```

Not sufficient for:

```text
- Is a warm model replica already loaded?
- Will the next inference call return quickly?
```

### 5.2 Databricks inference API

Example shape, depending on endpoint type:

```http
POST /serving-endpoints/{endpoint_name}/invocations
```

or an OpenAI-compatible serving route.

Useful for:

```text
- Actually invoking the model.
- Waking a scaled-to-zero endpoint.
- Proving data-plane readiness.
```

Important rule:

```text
The management status API can say READY while the next inference call still experiences cold-start latency.
```

---

## 6. Selected Design Principle

Do not over-engineer the first fix.

Initial target:

```text
status check
→ single inference attempt
→ classify result
→ return controlled response
→ log technical detail
```

Avoid initially:

```text
- automatic retry loops
- frontend polling
- SSE
- WebSockets
- async job tables
- callback orchestration
```

Those can be added later after measuring real cold-start behaviour.

---

## 7. Recommended First Implementation

Implement a thin backend facade that classifies Databricks serving states and errors.

The facade may be:

```text
- existing Java backend enhancement
- Python FastAPI POC
- another lightweight backend service
```

But the contract should be designed around the existing application flow.

If the goal is a drop-in proof, the POC should try to preserve or emulate the current request/response shape, adding only minimal metadata where useful.

---

## 8. Minimal Runtime Flow

```text
1. Browser sends normal AI request to backend.

2. Backend checks Databricks endpoint status.

3. If endpoint is STOPPED:
   Do not call inference.
   Return controlled "service stopped" response.

4. If endpoint is UPDATING / NOT_READY:
   Do not call inference.
   Return controlled "service starting or updating" response.

5. If endpoint is READY:
   Call inference once.

6. If inference succeeds:
   Return normal AI response.

7. If inference times out or returns transient infrastructure-style failure:
   Return controlled "AI service is starting" response.

8. Log the real cause internally.
```

---

## 9. Recommended User-Facing Messages

Keep the browser-facing message simple.

Default cold-start message:

```text
The AI service is starting after a period of inactivity. Please try again in about a minute.
```

Stopped endpoint:

```text
The AI service is currently stopped. Please contact support or try again later.
```

Updating endpoint:

```text
The AI service is being updated. Please try again shortly.
```

Unknown backend failure:

```text
The AI service is temporarily unavailable. Please try again later.
```

Avoid exposing raw Databricks messages, stack traces, endpoint IDs, workspace details, or internal exception text to the browser.

---

## 10. HTTP Response Recommendation

For cold start / warming:

```http
HTTP/1.1 503 Service Unavailable
Retry-After: 60
Content-Type: application/json
Cache-Control: no-store
```

Example body:

```json
{
  "status": "warming",
  "message": "The AI service is starting after a period of inactivity. Please try again in about a minute.",
  "retryAfterSeconds": 60
}
```

Important:

```text
Retry-After does not require the backend to retry.
It is simply a standard hint to the client or user.
```

For success:

```http
HTTP/1.1 200 OK
Content-Type: application/json
```

For stopped/updating/unavailable, use a controlled non-2xx response and a safe message.

---

## 11. Internal Classification Table

| Condition                        | Likely meaning                                      | User-facing status                        | Retry from facade?   |
| -------------------------------- | --------------------------------------------------- | ----------------------------------------- | -------------------- |
| Status READY + inference 200     | Normal                                              | ok                                        | No                   |
| Status READY + inference timeout | Cold start, slow model, network, or gateway timeout | warming                                   | Not in first version |
| Status READY + 502/503/504       | Transient serving/gateway issue                     | warming or unavailable                    | Not in first version |
| Status UPDATING / NOT_READY      | Endpoint update or deployment state                 | updating                                  | No                   |
| Status STOPPED                   | Admin/API stopped endpoint                          | stopped                                   | No                   |
| Inference 400 + status STOPPED   | Stopped endpoint                                    | stopped                                   | No                   |
| Inference 400 + status READY     | Bad request/payload/schema issue                    | request_error                             | No                   |
| 401/403                          | Auth/permission issue                               | unavailable to user; auth error in logs   | No                   |
| 404                              | Wrong endpoint/path/deleted endpoint                | unavailable to user; config error in logs | No                   |
| 429                              | Rate/capacity throttling                            | throttled/unavailable                     | Later                |
| Java/client timeout              | Cold start or long-running model likely             | warming                                   | Not in first version |

---

## 12. ADR Options

### Option A — Minimal facade classification

Backend checks endpoint status, calls inference once, and maps outcomes into clean application states.

Pros:

```text
- Simple
- Defensible
- Low-risk
- No frontend rewrite
- Improves user message immediately
- Good first POC
```

Cons:

```text
- User may need to manually retry
- Does not hide cold start
- Does not guarantee first request succeeds
```

Decision:

```text
Recommended first implementation.
```

---

### Option B — Bounded backend retry

Backend retries internally for a limited window, such as 30–90 seconds.

Pros:

```text
- Better UX if cold start is short
- First request may succeed without user retry
- Frontend can stay simple
```

Cons:

```text
- Ties up backend request resources
- May conflict with gateway/browser timeouts
- Needs careful timeout tuning
- Adds complexity before measurement
```

Decision:

```text
Consider later after measuring real cold-start latency.
```

---

### Option C — Async request + polling

Backend accepts request, returns request ID, and browser polls for status.

Example:

```text
POST /chat
→ 202 Accepted + requestId

GET /chat/status/{requestId}
→ warming | complete | failed
```

Pros:

```text
- Best for long cold starts
- Avoids browser/gateway timeout
- Gives clear status
```

Cons:

```text
- Requires frontend change
- Requires request state storage
- More moving parts
```

Decision:

```text
Good future option, not first fix.
```

---

### Option D — Developer/status diagnostic endpoint

Optional internal or dev-only facade endpoint:

```http
GET /databricks/status
```

This would be implemented by the facade and internally call:

```http
GET /api/2.0/serving-endpoints/{endpoint_name}
```

Purpose:

```text
- Help developers inspect sanitized Databricks endpoint state.
- Support POC validation.
- Avoid exposing raw Databricks credentials or sensitive details.
```

This is not required for the selected first implementation.

It should not become a public client-facing API unless explicitly approved.

Decision:

```text
Optional POC/developer diagnostic endpoint only.
```

---

### Option E — Manual or scheduled warm-up endpoint

Optional internal or dev-only facade endpoint:

```http
POST /databricks/warmup
```

This would be implemented by the facade and internally send a small real inference request to Databricks.

Purpose:

```text
- Wake the endpoint before demos.
- Validate cold-start behaviour.
- Support scheduled warm-up before known usage windows.
```

Important:

```text
The Databricks status API alone is not expected to warm the model.
A warm-up action must be a real inference call.
```

This option partially reduces cost savings if used frequently.

Decision:

```text
Useful operational add-on, not part of the core first fix.
```

---

### Option F — SSE / WebSockets

Backend streams progress to browser.

Pros:

```text
- Best real-time UX
```

Cons:

```text
- Often restricted in enterprise environments
- More infrastructure and security review
- Not needed for first implementation
```

Decision:

```text
Avoid initially.
```

---

### Option G — Disable scale-to-zero / minimum capacity

Endpoint stays warm.

Pros:

```text
- Best latency
- Simplest serving behaviour
```

Cons:

```text
- Expensive
- Not acceptable for low-volume lower environments
```

Decision:

```text
Use only where latency requirements justify cost.
```

---

## 13. Recommended Initial Architecture Decision

Adopt Option A.

```text
Implement minimal facade classification.

The backend should:
1. call Databricks endpoint status API,
2. avoid inference when endpoint is stopped/updating/not ready,
3. call inference once when endpoint is ready,
4. classify timeout/transient failures as warming/unavailable,
5. return safe user-facing messages,
6. log technical detail for developers,
7. avoid frontend changes in the first version.
```

Optional developer endpoints such as status inspection or warm-up belong under Options D and E, not under the core recommended path.

---

## 14. POC Guidance for Coding Agent

The POC may be implemented in Python, FastAPI, Java, or another backend framework.

Do not overfit the POC to FastAPI if the final target is Java.

The coding agent should focus on:

```text
- reproducing the current client-facing request shape where practical,
- wrapping the Databricks call,
- classifying outcomes,
- returning controlled responses,
- logging enough evidence to validate real behaviour,
- generating OpenAPI/Swagger only as a convenience, not as the design driver.
```

Minimum POC:

```text
Wrap the existing AI request path.
Do not add new public endpoints unless needed for validation.
```

Optional internal/dev-only endpoints:

```text
- facade health
- sanitized Databricks status inspection
- manual warm-up trigger
```

These optional endpoints are not part of the selected architecture unless explicitly approved.

---

## 15. POC Discovery Goals

The POC should not only implement a workaround. It should uncover actual client-environment behaviour.

Validate:

```text
1. What does the current Java/backend layer return when Databricks is scaled to zero?
2. What is the backend timeout?
3. What is the gateway/load-balancer timeout?
4. Does the first cold request eventually succeed or time out?
5. How long does the first request take after idle?
6. How long does the second request take after the first cold start?
7. What does Databricks return when endpoint is manually stopped?
8. What does Databricks return for bad payloads?
9. What does Databricks return for auth/permission failures?
10. Which current errors are being incorrectly collapsed into generic 500?
```

This behaviour should be measured before adding retries or more complex async UX.

---

## 16. POC Success Criteria

Validate these behaviours:

```text
1. Warm endpoint returns successful model response.

2. Scaled-to-zero endpoint either:
   - eventually returns success, or
   - returns a controlled warming response if timeout happens first.

3. Second request after cold start usually succeeds faster.

4. Manually stopped endpoint does not get misclassified as generic 500.

5. Bad input does not get misclassified as cold start.

6. Auth/config errors are logged clearly but shown safely to the user.

7. Browser does not see raw Databricks errors or stack traces.

8. Optional diagnostic endpoints, if created, remain internal/dev-only.
```

---

## 17. Observability Requirements

Log these fields:

```text
request_id
timestamp
endpoint_name
databricks_status_ready
databricks_status_update_state
databricks_http_status
facade_status
http_status_returned_to_client
databricks_latency_ms
total_facade_latency_ms
exception_class
timeout_seconds
retry_after_seconds
```

Useful cold-start measurements:

```text
first_request_after_idle_latency_ms
second_request_latency_ms
time_until_success_after_first_timeout_ms
```

Do not log:

```text
access tokens
full sensitive prompts
full sensitive responses
raw stack traces in client response
workspace secrets
```

---

## 18. Guidance to Future Coding Agent

Build the smallest useful facade.

Do not assume that `READY` means warm.

Do not assume that all `400` responses mean stopped.

Do not blindly convert all upstream failures to `500`.

Do not implement retries before measuring timeout behaviour.

Preserve the current frontend contract where possible.

If creating a POC with OpenAPI/Swagger, keep the schema generic enough that the final Java implementation can reuse the response classification model.

Recommended internal classification states:

```text
ok
warming
stopped
updating
throttled
request_error
auth_error
config_error
upstream_error
unavailable
```

Recommended first release:

```text
No retries.
No polling.
No SSE.
No async job table.
Status check + single invocation + classification + logs.
```

Optional capabilities can be added later as separate decisions:

```text
bounded backend retry
developer status endpoint
manual/scheduled warm-up endpoint
frontend polling
SSE/WebSocket progress
minimum warm capacity
```

---

## 19. Client Explanation

Suggested explanation:

```text
For cost reasons, lower environments can keep Databricks serving endpoints configured with scale-to-zero. In that mode, the first request after inactivity may wake the endpoint and experience cold-start latency. This is expected serverless behaviour, not necessarily an outage.

The proposed facade does not hide failures. It classifies them more accurately. Instead of returning a generic 500 and telling users the system is down, the facade distinguishes stopped, updating, warming, timeout, authorization, request, and upstream errors.

The first implementation keeps the frontend unchanged and avoids complex retries or streaming. It simply returns a controlled response such as: “The AI service is starting after inactivity. Please try again in about a minute.”
```

---

## 20. Reference URLs

Databricks custom model serving and scale-to-zero:
https://docs.databricks.com/aws/en/machine-learning/model-serving/custom-models

Create custom model serving endpoints:
https://docs.databricks.com/aws/en/machine-learning/model-serving/create-manage-serving-endpoints

Manage model serving endpoints, status, stop/start, stopped endpoint behaviour:
https://docs.databricks.com/aws/en/machine-learning/model-serving/manage-serving-endpoints

Debug model serving timeouts:
https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-timeouts

Model Serving limits and regions:
https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-limits

Databricks model serving glossary:
https://docs.databricks.com/aws/en/machine-learning/model-serving/glossary

Query custom model serving endpoints:
https://docs.databricks.com/aws/en/machine-learning/model-serving/score-custom-model-endpoints

Serve custom LLMs with custom model serving:
https://docs.databricks.com/aws/en/machine-learning/model-serving/serve-custom-llms

Foundation Model APIs limits and quotas:
https://docs.databricks.com/aws/en/machine-learning/foundation-model-apis/limits

Databricks Vector Search performance guide, cold-start note:
https://docs.databricks.com/aws/en/vector-search/vector-search-best-practices

Databricks Vector Search load testing, cold-start note:
https://docs.databricks.com/aws/en/vector-search/vector-search-endpoint-load-test

Databricks REST API: create serving endpoint:
https://docs.databricks.com/api/workspace/servingendpoints/create

Databricks REST API: update serving endpoint config:
https://docs.databricks.com/api/workspace/servingendpoints/updateconfig

Databricks CLI serving endpoints command group:
https://docs.databricks.com/aws/en/dev-tools/cli/reference/serving-endpoints-commands

HTTP 503 Service Unavailable:
https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/503

HTTP 202 Accepted, for possible future async option:
https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/202

HTTP Retry-After header:
https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After
