from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import settings
from app.models import ChatRequest, ChatResponsePayload
from app.responses import error_response, ok_response, stopped_response, updating_response, warming_response


class DatabricksConfigError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.databricks_token}",
        "Content-Type": "application/json",
    }


def _validate_config() -> None:
    missing = [
        name
        for name, value in {
            "DATABRICKS_HOST": settings.databricks_host,
            "DATABRICKS_TOKEN": settings.databricks_token,
            "DATABRICKS_ENDPOINT_NAME": settings.databricks_endpoint_name,
        }.items()
        if not value
    ]
    if missing:
        raise DatabricksConfigError(f"Missing required settings: {', '.join(missing)}")


async def get_endpoint_state(client: httpx.AsyncClient) -> dict[str, Any]:
    """Call Databricks serving endpoint management status API."""

    url = (
        f"{settings.databricks_host.rstrip('/')}"
        f"/api/2.0/serving-endpoints/{settings.databricks_endpoint_name}"
    )
    response = await client.get(url, headers=_headers(), timeout=15.0)
    response.raise_for_status()
    payload = response.json()
    return payload.get("state", payload)


def classify_state(state: dict[str, Any]) -> str:
    """Map Databricks endpoint state into a simplified facade classification.

    The state object only carries two fields: `ready` (READY | NOT_READY) and
    `config_update` (NOT_UPDATING | IN_PROGRESS | UPDATE_FAILED | UPDATE_CANCELED).
    The older API name for the latter is `update_state`. There is NO "STOPPED"
    enum: a stopped endpoint reports ready=NOT_READY with config_update not
    IN_PROGRESS, and an inference call returns HTTP 400. Scale-to-zero keeps
    ready=READY, so a cold start is only visible at inference time, never here.
    See docs/databricks-endpoint-states.md for the validated model.
    """

    ready = str(state.get("ready", "")).upper()
    # config_update is the current field; update_state is the older name.
    config_update = str(state.get("config_update", state.get("update_state", ""))).upper()

    if config_update == "IN_PROGRESS":
        return "updating"

    if config_update in {"UPDATE_FAILED", "UPDATE_CANCELED"}:
        return "updating"

    if ready == "NOT_READY":
        # NOT_READY with no in-progress update means the endpoint is stopped.
        return "stopped"

    if ready == "READY":
        return "ready"

    # Unknown shape: avoid sending real user workload until understood.
    return "updating"


def build_databricks_payload(request: ChatRequest) -> dict[str, Any]:
    """Build an MLflow scoring-envelope payload for a custom pyfunc endpoint.

    Custom Model Serving endpoints (the echo harness, or a request-router endpoint) take
    the `dataframe_records` envelope with model-specific columns - here `prompt`, the
    latest user message. For an OpenAI-compatible chat / Foundation Model endpoint, send
    `messages` + `max_tokens` instead (that is the other family - see git history).

    Note the intentional asymmetry: the OUTBOUND payload here is pyfunc-first, but
    `extract_answer` below stays backward-compatible with BOTH the pyfunc `predictions`
    shape and the OpenAI `choices` shape, so a switch of endpoint family only needs this
    function changed, not the extractor.
    """

    prompt = ""
    for message in reversed(request.messages):
        if message.role == "user":
            prompt = message.content
            break
    # Fallback: if there is no user turn (only system/assistant), use the last message.
    # Intentional - the facade forwards whatever it was given rather than rejecting it.
    if not prompt and request.messages:
        prompt = request.messages[-1].content

    return {"dataframe_records": [{"prompt": prompt}]}


async def invoke_databricks(client: httpx.AsyncClient, request: ChatRequest) -> dict[str, Any]:
    """Call Databricks serving endpoint invocation API.

    This default uses /invocations. If the work endpoint is OpenAI-compatible with a
    different route, update only this function.
    """

    url = (
        f"{settings.databricks_host.rstrip('/')}"
        f"/serving-endpoints/{settings.databricks_endpoint_name}/invocations"
    )
    response = await client.post(
        url,
        headers=_headers(),
        json=build_databricks_payload(request),
        timeout=settings.databricks_timeout_seconds,
    )

    if response.status_code in {408, 429, 500, 502, 503, 504}:
        raise httpx.HTTPStatusError(
            f"Transient Databricks status: {response.status_code}",
            request=response.request,
            response=response,
        )

    response.raise_for_status()
    return response.json()


def extract_answer(payload: dict[str, Any]) -> str:
    """Best-effort extraction from common Databricks/OpenAI-like response shapes."""

    if "choices" in payload and payload["choices"]:
        choice = payload["choices"][0]
        message = choice.get("message") or {}
        if "content" in message:
            return str(message["content"])
        if "text" in choice:
            return str(choice["text"])

    # MLflow pyfunc scoring shape: {"predictions": [{...}]}. The echo harness returns
    # {"echo": ..., "fortune": ...}; render those when present, else stringify.
    predictions = payload.get("predictions")
    if isinstance(predictions, list) and predictions:
        first = predictions[0]
        if isinstance(first, dict):
            parts = [str(first[k]) for k in ("echo", "fortune") if first.get(k)]
            if parts:
                return " - ".join(parts)
        return str(first)
    if predictions is not None:
        return str(predictions)

    return str(payload)


async def handle_databricks_chat(request: ChatRequest) -> ChatResponsePayload:
    """Databricks-backed version of the facade.

    First implementation intentionally:
    - checks endpoint status,
    - calls inference once,
    - classifies result,
    - does not retry.
    """

    start = time.perf_counter()

    async with httpx.AsyncClient() as client:
        try:
            _validate_config()  # inside the try so DatabricksConfigError is caught below
            state = await get_endpoint_state(client)
            state_class = classify_state(state)

            if state_class == "stopped":
                return stopped_response(request, latency=time.perf_counter() - start)

            if state_class == "updating":
                return updating_response(request, latency=time.perf_counter() - start)

            payload = await invoke_databricks(client, request)
            return ok_response(
                request,
                answer=extract_answer(payload),
                latency=time.perf_counter() - start,
            )

        except DatabricksConfigError as exc:
            return error_response(
                request,
                status_code=503,
                latency=time.perf_counter() - start,
                message=str(exc),
            )

        except httpx.TimeoutException:
            return warming_response(request, latency=time.perf_counter() - start)

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else 503

            if status_code in {408, 429, 500, 502, 503, 504}:
                return warming_response(request, latency=time.perf_counter() - start)

            if status_code in {401, 403}:
                return error_response(
                    request,
                    status_code=503,
                    latency=time.perf_counter() - start,
                    message="The AI service is temporarily unavailable.",
                )

            if status_code == 404:
                # Wrong/deleted endpoint: config error internally, safe message out.
                return error_response(
                    request,
                    status_code=503,
                    latency=time.perf_counter() - start,
                    message="The AI service is temporarily unavailable.",
                )

            if status_code == 400:
                return error_response(
                    request,
                    status_code=400,
                    latency=time.perf_counter() - start,
                    message="The request could not be processed. Please check the input and try again.",
                )

            return error_response(
                request,
                status_code=502,
                latency=time.perf_counter() - start,
                message="The AI service is temporarily unavailable. Please try again later.",
            )

        except Exception:
            return error_response(
                request,
                status_code=502,
                latency=time.perf_counter() - start,
                message="The AI service is temporarily unavailable. Please try again later.",
            )
