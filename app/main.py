from __future__ import annotations

from typing import Annotated

from fastapi import Body, FastAPI, Response
from fastapi.responses import JSONResponse

from app.config import settings
from app.databricks_client import handle_databricks_chat
from app.mock_backend import handle_mock_chat
from app.models import ChatRequest, ChatResponsePayload, UserFeedback
from app.responses import error_response

app = FastAPI(
    title="genai-coldstart-guard",
    version="1.0.1",
    description="Cold-start aware facade/mock for Databricks GenAI serving.",
    openapi_tags=[
        {
            "name": "AI Agents",
            "description": "API for interacting with AI agents",
        }
    ],
)


def to_http_response(payload: ChatResponsePayload) -> JSONResponse:
    """Preserve Java compatibility by default.

    If COMPATIBILITY_HTTP_200=true, always return HTTP 200 and put the real status
    in the payload. If false, return the payload status as the HTTP status code.
    """

    http_status = 200 if settings.compatibility_http_200 else (payload.status or 200)

    headers = {}
    if payload.status == 503:
        headers["Retry-After"] = str(settings.retry_after_seconds)
        headers["Cache-Control"] = "no-store"

    return JSONResponse(
        status_code=http_status,
        content=payload.model_dump(),
        headers=headers,
    )


@app.get("/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "mock_enabled": settings.mock_enabled,
    }


def _example(route: str) -> dict:
    """Build a chat request body that targets a given mock route."""
    return {
        "messages": [{"role": "user", "content": "Hello"}],
        "conversation_id": "demo-1",
        "route": route,
    }


# Named request examples rendered as a dropdown in Swagger. Documentation only
# (OpenAPI metadata). Dispatch is driven by the `route` field: no route hits the
# real endpoint, a `mock:*` route runs a simulation (when MOCK_ENABLED=true).
CHAT_EXAMPLES = {
    "real_passthrough": {
        "summary": "REAL passthrough (pick this to hit the live endpoint)",
        "description": (
            "No `route` field, so this body is forwarded to the real serving endpoint and you "
            "get its actual answer (and real latency, including any cold start). Every OTHER "
            "example below carries a `mock:*` route and runs a simulation instead - those work "
            "only when the server was started with MOCK_ENABLED=true."
        ),
        "value": {"messages": [{"role": "user", "content": "hello"}], "conversation_id": "demo-1"},
    },
    "warm_success": {
        "summary": "Warm success (payload 200)",
        "description": "Endpoint is warm; returns a normal answer.",
        "value": _example("mock:success_fast"),
    },
    "slow_success": {
        "summary": "Slow success (payload 200, ~5s)",
        "description": "Warm but slow response.",
        "value": _example("mock:success_slow"),
    },
    "cold_start_warming": {
        "summary": "Cold start / warming (payload 503)",
        "description": "First request after idle times out warming up; returns the 'service is starting' message.",
        "value": _example("mock:cold_start_timeout"),
    },
    "cold_start_lifecycle": {
        "summary": "Cold start lifecycle (stateful)",
        "description": "Warming for the first few calls, then succeeds; re-cools after idle. Send repeatedly to watch the transition.",
        "value": _example("mock:cold_start"),
    },
    "stopped": {
        "summary": "Endpoint stopped (payload 503)",
        "description": "Admin-stopped endpoint; returns the 'currently stopped' message.",
        "value": _example("mock:databricks_stopped"),
    },
    "updating": {
        "summary": "Endpoint updating (payload 503)",
        "description": "Endpoint is updating / not ready.",
        "value": _example("mock:databricks_updating"),
    },
    "bad_request": {
        "summary": "Bad request (payload 400)",
        "description": "Invalid input; returns a controlled 400.",
        "value": _example("mock:bad_request"),
    },
    "guardrail_blocked": {
        "summary": "Guardrail blocked (payload 400)",
        "description": "Request outside the allowed scope.",
        "value": _example("mock:guardrail_blocked"),
    },
    "no_grounding": {
        "summary": "No grounding (payload 200)",
        "description": "Answers that there is not enough information in approved documents.",
        "value": _example("mock:no_grounding"),
    },
    "auth_error": {
        "summary": "Auth/config error (payload 503)",
        "description": "Auth failure mapped to a safe 'temporarily unavailable' message.",
        "value": _example("mock:auth_error"),
    },
    "upstream_503": {
        "summary": "Upstream unavailable (payload 503)",
        "description": "Transient upstream 5xx mapped to 'temporarily unavailable'.",
        "value": _example("mock:upstream_503"),
    },
    "state_ready": {
        "summary": "State -> ready (real classifier)",
        "description": "Runs a fake state (READY / NOT_UPDATING) through the real classify_state().",
        "value": _example("mock:state:READY:NOT_UPDATING"),
    },
    "state_stopped": {
        "summary": "State -> stopped (real classifier)",
        "description": "Fake state NOT_READY / NOT_UPDATING, classified as stopped.",
        "value": _example("mock:state:NOT_READY:NOT_UPDATING"),
    },
    "state_updating": {
        "summary": "State -> updating (real classifier)",
        "description": "Fake state READY / IN_PROGRESS, classified as updating.",
        "value": _example("mock:state:READY:IN_PROGRESS"),
    },
}


@app.post(
    "/agentservice/agent/chat",
    tags=["AI Agents"],
    summary="Chat with an AI Agent",
    description=(
        "Send messages to an AI agent and receive predictions with sources. "
        "**The request `route` field drives dispatch.** With no `route` the body is "
        "forwarded to the real endpoint (pick the **REAL passthrough** example). A `mock:*` "
        "route simulates an outcome and requires the server to run with `MOCK_ENABLED=true`; "
        "each mock example maps to a Databricks endpoint state in the "
        "[traceability table](https://github.com/dzivkovi/genai-coldstart-guard/blob/main/docs/databricks-endpoint-states.md). "
        "Any other (non-`mock:`) route returns a controlled 400."
    ),
    operation_id="chat",
    response_model=ChatResponsePayload,
)
async def chat(request: Annotated[ChatRequest, Body(openapi_examples=CHAT_EXAMPLES)]) -> Response:
    route = request.route or ""
    if not route:
        # No route -> reality: forward to the real serving endpoint.
        payload = await handle_databricks_chat(request)
    elif route.startswith("mock:"):
        # Simulation route -> only honoured when mocks are enabled for this server.
        if settings.mock_enabled:
            payload = await handle_mock_chat(request)
        else:
            payload = error_response(request, status_code=400, message="Mock routes are disabled here.")
    else:
        # Unknown non-mock route -> fail loud rather than silently treat it as real.
        payload = error_response(request, status_code=400, message=f"Unknown route: {route}")

    return to_http_response(payload)


@app.post(
    "/agentservice/agent/feedback",
    tags=["AI Agents"],
    summary="Provide feedback on an AI Agent's response",
    description="Submit user feedback for an AI agent's response",
    operation_id="giveFeedback",
)
async def give_feedback(feedback: UserFeedback) -> dict[str, str]:
    # The mock intentionally accepts feedback without persistence.
    return {
        "status": "ok",
        "conversation_response_id": feedback.conversation_response_id or "",
    }
