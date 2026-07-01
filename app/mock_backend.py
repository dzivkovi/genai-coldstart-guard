from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.config import settings
from app.models import ChatRequest, ChatResponsePayload
from app.responses import error_response, ok_response, stopped_response, updating_response, warming_response

# Module-level state for the stateful scale-to-zero simulation. A real endpoint
# wakes on the first request after idle, takes a few seconds to become ready, then
# scales back to zero after inactivity. Tracking these two timestamps lets the
# mock:cold_start route show warming -> ready over time, then cold again after idle.
_cold_start_wake_at: Optional[float] = None
_cold_start_last_seen: Optional[float] = None


def _cold_start_is_ready(now: float) -> bool:
    """Advance the scale-to-zero simulation and report whether the endpoint is warm.

    Returns True once the warm-up window has elapsed since wake. A gap longer than
    mock_idle_reset_seconds since the last request scales the endpoint back to zero,
    so the next request starts a fresh cold start.
    """

    global _cold_start_wake_at, _cold_start_last_seen

    if (
        _cold_start_wake_at is None
        or _cold_start_last_seen is None
        or (now - _cold_start_last_seen) > settings.mock_idle_reset_seconds
    ):
        _cold_start_wake_at = now

    _cold_start_last_seen = now
    return (now - _cold_start_wake_at) >= settings.mock_warmup_seconds


async def handle_mock_chat(request: ChatRequest) -> ChatResponsePayload:
    """Simulate Databricks and facade outcomes using the request.route field."""

    start = time.perf_counter()

    if settings.mock_sleep_seconds > 0:
        await asyncio.sleep(settings.mock_sleep_seconds)

    # The dispatcher only routes explicit mock:* requests here.
    route = request.route or ""

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

    if route == "mock:cold_start":
        # Stateful scale-to-zero: poll this and watch warming flip to success after
        # mock_warmup_seconds, then cold again after mock_idle_reset_seconds idle.
        if _cold_start_is_ready(time.monotonic()):
            return ok_response(
                request,
                answer="The AI service has finished warming up and is ready.",
                latency=time.perf_counter() - start,
            )
        return warming_response(request, latency=time.perf_counter() - start)

    if route.startswith("mock:state:"):
        # Emulate any endpoint state by running a fake state object through the REAL
        # classifier. Format: mock:state:<ready>:<config_update>, e.g.
        #   mock:state:READY:NOT_UPDATING     -> ready (real backend would call inference)
        #   mock:state:NOT_READY:NOT_UPDATING -> stopped
        #   mock:state:READY:IN_PROGRESS      -> updating
        from app.databricks_client import classify_state

        parts = route.split(":")
        ready = parts[2].upper() if len(parts) > 2 else ""
        config_update = parts[3].upper() if len(parts) > 3 else "NOT_UPDATING"
        classification = classify_state({"ready": ready, "config_update": config_update})
        latency = time.perf_counter() - start

        if classification == "ready":
            return ok_response(
                request,
                answer="Endpoint READY: the real backend would call inference here.",
                latency=latency,
            )
        if classification == "stopped":
            return stopped_response(request, latency=latency)
        return updating_response(request, latency=latency)

    return error_response(
        request,
        status_code=400,
        latency=time.perf_counter() - start,
        message=f"Unknown mock route: {route}",
    )
