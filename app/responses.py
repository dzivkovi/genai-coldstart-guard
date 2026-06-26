from __future__ import annotations

from uuid import uuid4

from app.config import settings
from app.models import ChatPrediction, ChatRequest, ChatResponsePayload, Citation


def response_id() -> str:
    return f"resp-{uuid4().hex[:12]}"


def ok_response(request: ChatRequest, answer: str, latency: float) -> ChatResponsePayload:
    return ChatResponsePayload(
        conversation_id=request.conversation_id,
        conversation_response_id=response_id(),
        predictions=[
            ChatPrediction(
                answer=answer,
                citations=[
                    Citation(
                        topic_title="Mock source",
                        topic_source="mock://source/1",
                    )
                ],
                image_paths=[],
                latency=round(latency, 3),
                success=True,
                error_message=None,
            )
        ],
        user_feedback=None,
        error=None,
        status=200,
    )


def error_response(
    request: ChatRequest,
    *,
    status_code: int,
    message: str,
    latency: float = 0.0,
) -> ChatResponsePayload:
    return ChatResponsePayload(
        conversation_id=request.conversation_id,
        conversation_response_id=response_id(),
        predictions=[
            ChatPrediction(
                answer="",
                citations=[],
                image_paths=[],
                latency=round(latency, 3),
                success=False,
                error_message=message,
            )
        ],
        user_feedback=None,
        error=message,
        status=status_code,
    )


def warming_response(request: ChatRequest, latency: float = 0.0) -> ChatResponsePayload:
    return error_response(
        request,
        status_code=503,
        latency=latency,
        message=(
            "The AI service is starting after a period of inactivity. "
            f"Please try again in about {settings.retry_after_seconds} seconds."
        ),
    )


def stopped_response(request: ChatRequest, latency: float = 0.0) -> ChatResponsePayload:
    return error_response(
        request,
        status_code=503,
        latency=latency,
        message="The AI service is currently stopped. Please contact support or try again later.",
    )


def updating_response(request: ChatRequest, latency: float = 0.0) -> ChatResponsePayload:
    return error_response(
        request,
        status_code=503,
        latency=latency,
        message="The AI service is being updated. Please try again shortly.",
    )
