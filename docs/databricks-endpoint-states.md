# Databricks serving endpoint states: what client code actually experiences

Validated against Databricks docs on 2026-06-26 (see sources at bottom). Target deployment is the **AWS flavor** of Databricks. The serving-endpoints REST API, the state enums, and the stopped/scale-to-zero behaviour described here are identical across AWS, Azure, and GCP; only the docs URLs differ. This is the behavioural reference for the facade and a visual scratchpad to check the middle-tier API against while wiring the real endpoint.

## The one thing to internalize

The serving endpoint `state` object has exactly **two** fields:

| Field | Values |
|---|---|
| `state.ready` | `READY`, `NOT_READY` |
| `state.config_update` (older name: `state.update_state`) | `NOT_UPDATING`, `IN_PROGRESS`, `UPDATE_FAILED`, `UPDATE_CANCELED` |

There is **no `STOPPED` value**. The UI label "Not ready (Stopped)" is a presentation-layer composition, not an API enum. This is the single most important correction to the original research: you cannot detect "stopped" by looking for the word "STOPPED" in the status payload.

`scale_to_zero_enabled` is a per-served-entity config flag (`config.served_entities[].scale_to_zero_enabled`), not a state. Scaling to zero does **not** change `state.ready` - the endpoint keeps reporting `READY` while running zero replicas.

## How each real-world condition maps to observable signals

| Condition | `state.ready` | `state.config_update` | Inference (`POST /invocations`) | Facade classification |
|---|---|---|---|---|
| Warm and serving | `READY` | `NOT_UPDATING` | 200, fast | `ok` |
| Scaled to zero (idle) | `READY` | `NOT_UPDATING` | slow / may time out on first call | `ok` if it returns in time, else `warming` |
| Deploying / config update | `NOT_READY` or `READY` | `IN_PROGRESS` | may fail | `updating` |
| Update failed | `READY` or `NOT_READY` | `UPDATE_FAILED` | old config may still serve | `updating` (or `unavailable`) |
| Manually stopped | `NOT_READY` | `NOT_UPDATING` | **400** | `stopped` |
| Auth/permission problem | n/a (status call 401/403) | n/a | n/a | `auth_error` (safe 503 to user) |
| Wrong/deleted endpoint | n/a (status call 404) | n/a | n/a | `config_error` (safe 503 to user) |
| Throttled | `READY` | `NOT_UPDATING` | 429 | `throttled` |

Key consequence: **stopped and scaled-to-zero are only cleanly distinguishable by behaviour, not by status.** Stopped shows `ready:NOT_READY` and inference 400; scaled-to-zero shows `ready:READY` and inference is just slow. The status API alone cannot tell you whether the next inference call will be fast.

Caveat to verify on the real endpoint tomorrow: the `400`-on-stopped behaviour is documented, but Databricks docs do not publish a JSON example proving a stopped endpoint reports `config_update: NOT_UPDATING`. Treat `config_update: NOT_UPDATING` for stopped as expected-but-unconfirmed. The reliable signals are: `ready: NOT_READY` (with `config_update` not `IN_PROGRESS`) plus the inference `400`. Capture the actual stopped-endpoint status JSON during validation and update this row.

## Diagram 1: Endpoint lifecycle (Databricks side)

```mermaid
stateDiagram-v2
    [*] --> Creating
    Creating --> Ready: deploy ok
    Creating --> UpdateFailed: deploy failed

    Ready --> Updating: config update
    Updating --> Ready: update ok
    Updating --> UpdateFailed: update failed
    UpdateFailed --> Updating: retry / fix

    Ready --> Stopped: admin stop
    Stopped --> Updating: admin start, new version

    Ready --> [*]: delete
    Stopped --> [*]: delete

    note right of Ready
        state.ready = READY
        config_update = NOT_UPDATING
        Scaled-to-zero lives HERE:
        0 replicas, but still READY.
        Cold start is invisible to the
        status API; it only shows up
        as latency at inference time.
    end note

    note right of Updating
        config_update = IN_PROGRESS
        (also the state right after Start)
    end note

    note right of Stopped
        state.ready = NOT_READY
        config_update = NOT_UPDATING
        No STOPPED enum exists.
        Inference returns HTTP 400.
    end note
```

## Diagram 2: Facade decision flow (what the middle tier does per request)

```mermaid
flowchart TD
    REQ([Client chat request]) --> ST["GET /api/2.0/serving-endpoints/{name}"]

    ST -->|"401 / 403"| AUTH["auth_error - 503 safe message"]
    ST -->|"404"| CFG["config_error - 503 safe message"]
    ST -->|"network err / 5xx / timeout"| WARM["warming - 503 + Retry-After"]
    ST --> CLS{classify state}

    CLS -->|"config_update = IN_PROGRESS"| UPD["updating - 503"]
    CLS -->|"config_update = UPDATE_FAILED / UPDATE_CANCELED"| UPD
    CLS -->|"ready = NOT_READY"| STOP["stopped - 503"]
    CLS -->|"ready = READY"| INV["POST /invocations - ONE attempt"]

    INV -->|"200"| OK["ok - 200"]
    INV -->|"timeout (scaled-to-zero cold start)"| WARM
    INV -->|"429"| THR["throttled - 503"]
    INV -->|"500 / 502 / 503 / 504"| WARM
    INV -->|"400"| BR["request_error - 400"]
```

## Traceability: dropdown example -> state -> source

Every request example in the Swagger dropdown traces to a facade outcome and, for the endpoint-lifecycle cases, to a node in Diagram 1 and a real Databricks signal. The mapping is executable, not just prose: the state -> outcome step is proven by `tests/test_classify_state.py`, and the route -> response step by `tests/test_mock_routes.py`.

**Endpoint-lifecycle states** (these correspond to nodes in Diagram 1):

| Swagger example (route) | Facade outcome (payload status) | Diagram 1 node | Real Databricks signal | Source |
| --- | --- | --- | --- | --- |
| Warm success (`mock:success_fast`) | `ok` / 200 | Ready | `ready=READY`, `config_update=NOT_UPDATING`, inference 200 | [manage], [score] |
| Slow success (`mock:success_slow`) | `ok` / 200 | Ready (scaled-to-zero, woke in time) | `ready=READY`, slow first inference | [timeouts] |
| Cold start / warming (`mock:cold_start_timeout`) | `warming` / 503 | Ready (scaled-to-zero) | `ready=READY`, inference times out warming up | [timeouts] |
| Cold start lifecycle (`mock:cold_start`) | `warming` then `ok` | Ready (scale-to-zero wake) | same; warm-up only visible at inference time | [timeouts] |
| Endpoint stopped (`mock:databricks_stopped`) | `stopped` / 503 | Stopped | `ready=NOT_READY`, `config_update=NOT_UPDATING`, inference 400 | [manage] |
| Endpoint updating (`mock:databricks_updating`) | `updating` / 503 | Updating | `config_update=IN_PROGRESS` | [state], [sdk] |
| State -> ready (`mock:state:READY:NOT_UPDATING`) | `ready` / 200 | Ready | literal state through `classify_state()` | [sdk] |
| State -> stopped (`mock:state:NOT_READY:NOT_UPDATING`) | `stopped` / 503 | Stopped | literal state | [sdk] |
| State -> updating (`mock:state:READY:IN_PROGRESS`) | `updating` / 503 | Updating | literal state | [sdk] |

**Request-level outcomes** (the endpoint is READY; these are not lifecycle states, so they have no Diagram 1 node):

| Swagger example (route) | Facade outcome (payload status) | Where it originates | Source |
| --- | --- | --- | --- |
| Bad request (`mock:bad_request`) | `request_error` / 400 | inference returns 400 on a READY endpoint (bad payload/schema) | [score] |
| Guardrail blocked (`mock:guardrail_blocked`) | `request_error` / 400 | application guardrail, not a Databricks endpoint state | application-level |
| No grounding (`mock:no_grounding`) | `ok` / 200 | model answers "not enough information"; endpoint is healthy | application-level |
| Auth/config error (`mock:auth_error`) | `unavailable` / 503 | status or inference returns 401/403 (missing CAN_QUERY) | [manage] |
| Upstream unavailable (`mock:upstream_503`) | `warming` / 503 | transient 502/503/504 (and 429) from serving or gateway | [limits], [timeouts] |

[manage]: https://docs.databricks.com/aws/en/machine-learning/model-serving/manage-serving-endpoints
[state]: https://docs.databricks.com/api/workspace/servingendpoints/get
[sdk]: https://databricks-sdk-py.readthedocs.io/en/latest/dbdataclasses/serving.html
[timeouts]: https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-timeouts
[score]: https://docs.databricks.com/aws/en/machine-learning/model-serving/score-custom-model-endpoints
[limits]: https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-limits

## How classify_state works (and the bug that was fixed)

The original `app/databricks_client.py` `classify_state()` detected stopped by string-matching:

```python
raw_text = " ".join([ready, update_state, config_update, str(state)]).upper()
if "STOPPED" in raw_text:
    return "stopped"
if "UPDATING" in raw_text or "NOT_READY" in raw_text:
    return "updating"
if ready == "READY":
    return "ready"
return "updating"
```

Because the API never emits "STOPPED", a stopped endpoint (`ready:NOT_READY`, `config_update:NOT_UPDATING`) skipped the first check, matched `NOT_READY` in the second, and returned `"updating"` - the wrong label (though still a safe 503). This is now **fixed**: `classify_state()` keys off the real fields, in this order:

```python
if config_update == "IN_PROGRESS":
    return "updating"
if config_update in {"UPDATE_FAILED", "UPDATE_CANCELED"}:
    return "updating"          # or a distinct "unavailable"
if ready == "NOT_READY":
    return "stopped"           # NOT_READY + not updating == stopped
if ready == "READY":
    return "ready"
return "updating"              # unknown: stay conservative, never serve blindly
```

The inference 400 path remains the runtime confirmation of stopped: if the status read was stale and we call a stopped endpoint, the 400 still routes to `request_error`; logs capture the truth.

### Code-vs-target status

The classification table above is the **target** model. Status of the three known deltas:

1. **Stopped detection** - FIXED. `classify_state()` now keys off `ready`/`config_update` (code above); a stopped endpoint is labeled `stopped`. Covered by `tests/test_classify_state.py`.
2. **429 throttled** - still folded into `warming_response()` (the table targets a distinct `throttled`). Acceptable for now; split it out only if rate-limiting becomes real.
3. **404 not-found** - FIXED. `handle_databricks_chat` now has an explicit `404` branch returning a safe 503 (config error in logs).

## Stop/start REST endpoints (for test fixtures tomorrow)

```
POST /api/2.0/serving-endpoints/{name}/config:stop
POST /api/2.0/serving-endpoints/{name}/config:start
```

Start creates a **new config version**, so immediately after Start you will observe `config_update: IN_PROGRESS` (-> facade `updating`) before it reaches `READY`. Plan your latency measurements around that.

## Sources

- [Manage model serving endpoints, AWS](https://docs.databricks.com/aws/en/machine-learning/model-serving/manage-serving-endpoints) (stop/start, 400 on stopped, statuses)
- [Serving endpoints REST API, GET state schema](https://docs.databricks.com/api/workspace/servingendpoints/get) (cloud-agnostic)
- [Databricks SDK serving dataclasses](https://databricks-sdk-py.readthedocs.io/en/latest/dbdataclasses/serving.html) (exact enum values)
- [Debug model serving timeouts](https://docs.databricks.com/aws/en/machine-learning/model-serving/model-serving-timeouts)
