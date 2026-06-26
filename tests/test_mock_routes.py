from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_success_fast():
    response = client.post(
        "/agentservice/agent/chat",
        json={
            "conversation_id": "test",
            "route": "mock:success_fast",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["predictions"][0]["success"] is True


def test_cold_start_compatibility_mode():
    response = client.post(
        "/agentservice/agent/chat",
        json={
            "conversation_id": "test",
            "route": "mock:cold_start_timeout",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 503
    assert body["predictions"][0]["success"] is False
    assert "starting" in body["error"]


def _post_cold_start():
    return client.post(
        "/agentservice/agent/chat",
        json={
            "conversation_id": "test",
            "route": "mock:cold_start",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )


def test_cold_start_initially_warming(monkeypatch):
    import app.mock_backend as mb
    from app.config import settings

    # Long warm-up + fresh state: the first request must be warming.
    monkeypatch.setattr(settings, "mock_warmup_seconds", 100.0)
    monkeypatch.setattr(settings, "mock_idle_reset_seconds", 100.0)
    mb._cold_start_wake_at = None
    mb._cold_start_last_seen = None

    body = _post_cold_start().json()
    assert body["status"] == 503
    assert "starting" in body["error"]


def test_cold_start_becomes_ready_after_warmup(monkeypatch):
    import app.mock_backend as mb
    from app.config import settings

    # Instant warm-up: the endpoint is ready immediately.
    monkeypatch.setattr(settings, "mock_warmup_seconds", 0.0)
    mb._cold_start_wake_at = None
    mb._cold_start_last_seen = None

    body = _post_cold_start().json()
    assert body["status"] == 200
    assert body["predictions"][0]["success"] is True


def test_state_injection_stopped():
    response = client.post(
        "/agentservice/agent/chat",
        json={
            "conversation_id": "test",
            "route": "mock:state:NOT_READY:NOT_UPDATING",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    body = response.json()
    assert body["status"] == 503
    assert "stopped" in body["error"].lower()


def test_state_injection_ready():
    response = client.post(
        "/agentservice/agent/chat",
        json={
            "conversation_id": "test",
            "route": "mock:state:READY:NOT_UPDATING",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    body = response.json()
    assert body["status"] == 200
    assert body["predictions"][0]["success"] is True
