from __future__ import annotations

import asyncio
import time

from app.config import settings
from app.models import ChatRequest, ChatResponsePayload
from app.responses import error_response, ok_response, stopped_response, updating_response, warming_response


async def handle_mock_chat(request: ChatRequest) -> ChatResponsePayload:
    """Simulate Databricks and facade outcomes using the request.route field."""

    start = time.perf_counter()

    if settings.mock_sleep_seconds > 0:
        await asyncio.sleep(settings.mock_sleep_seconds)

    route = request.route or "mock:success_fast"

    if route == "mock:success_fast":
        await asyncio.sleep(0.2)
        return ok_response(
            request,
            answer="This is a successful mock response.",
            latency=time.perf_counter() - start,
        )

    if route == "mock:success_slow":
        await asyncio.sleep(5.0)
        return ok_response(
            request,
            answer="This is a slow but successful mock response.",
            latency=time.perf_counter() - start,
        )

    if route == "mock:cold_start_timeout":
        await asyncio.sleep(2.0)
        return warming_response(request, latency=time.perf_counter() - start)

    if route == "mock:databricks_stopped":
        return stopped_response(request, latency=time.perf_counter() - start)

    if route == "mock:databricks_updating":
        return updating_response(request, latency=time.perf_counter() - start)

    if route == "mock:bad_request":
        return error_response(
            request,
            status_code=400,
            latency=time.perf_counter() - start,
            message="The request could not be processed. Please check the input and try again.",
        )

    if route == "mock:auth_error":
        return error_response(
            request,
            status_code=503,
            latency=time.perf_counter() - start,
            message="The AI service is temporarily unavailable.",
        )

    if route == "mock:upstream_503":
        return error_response(
            request,
            status_code=503,
            latency=time.perf_counter() - start,
            message="The AI service is temporarily unavailable. Please try again later.",
        )

    if route == "mock:guardrail_blocked":
        return error_response(
            request,
            status_code=400,
            latency=time.perf_counter() - start,
            message="This request cannot be answered because it is outside the allowed scope.",
        )

    if route == "mock:no_grounding":
        return ok_response(
            request,
            answer="I do not have enough information in the approved documents to answer that.",
            latency=time.perf_counter() - start,
        )

    return error_response(
        request,
        status_code=400,
        latency=time.perf_counter() - start,
        message=f"Unknown mock route: {route}",
    )
