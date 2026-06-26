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
