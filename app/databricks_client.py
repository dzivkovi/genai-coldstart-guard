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

    Databricks states vary by endpoint type and API version. Keep this intentionally
    conservative and log the raw state in real production code.
    """

    ready = str(state.get("ready", "")).upper()
    update_state = str(state.get("update_state", "")).upper()
    config_update = str(state.get("config_update", "")).upper()

    raw_text = " ".join([ready, update_state, config_update, str(state)]).upper()

    if "STOPPED" in raw_text:
        return "stopped"

    if "UPDATING" in raw_text or "NOT_READY" in raw_text or "NOTREADY" in raw_text:
        return "updating"

    if ready == "READY":
        return "ready"

    # Unknown status: avoid sending real user workload until understood.
    return "updating"


def build_databricks_payload(request: ChatRequest) -> dict[str, Any]:
    """Build a generic OpenAI-style payload from the preserved Java chat request.

    Adjust this function at work if the actual Databricks endpoint expects a different
    schema, such as /invocations dataframe_split or a custom agent schema.
    """

    return {
        "messages": [message.model_dump() for message in request.messages],
        "max_tokens": 512,
    }


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

    if "predictions" in payload:
        return str(payload["predictions"])

    return str(payload)


async def handle_databricks_chat(request: ChatRequest) -> ChatResponsePayload:
    """Databricks-backed version of the facade.

    First implementation intentionally:
    - checks endpoint status,
    - calls inference once,
    - classifies result,
    - does not retry.
    """

    _validate_config()
    start = time.perf_counter()

    async with httpx.AsyncClient() as client:
        try:
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
