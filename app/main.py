from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse

from app.config import settings
from app.databricks_client import handle_databricks_chat
from app.mock_backend import handle_mock_chat
from app.models import ChatRequest, ChatResponsePayload, UserFeedback

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
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "backend_mode": settings.backend_mode,
    }


@app.post(
    "/agentservice/agent/chat",
    tags=["AI Agents"],
    summary="Chat with an AI Agent",
    description="Send messages to an AI agent and receive predictions with sources",
    operation_id="chat",
    response_model=ChatResponsePayload,
)
async def chat(request: ChatRequest) -> Response:
    if settings.backend_mode.lower() == "databricks":
        payload = await handle_databricks_chat(request)
    else:
        payload = await handle_mock_chat(request)

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
