import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import call_control


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(call_control.router, prefix="/api/threecx")
    monkeypatch.setattr(call_control.settings, "threecx_queue_dn", "8002")
    monkeypatch.setattr(call_control.token_manager, "is_configured", lambda: True)
    monkeypatch.setattr(call_control.queue_answer_watcher, "watch", lambda call_id: None)
    return TestClient(app)


def test_dial_into_queue_rings_source_then_connects_queue(client, monkeypatch):
    calls = []

    async def fake_make_call(self, dn, destination):
        calls.append((dn, destination))
        return {"CallId": 50}

    monkeypatch.setattr(call_control.ThreeCXClient, "make_call", fake_make_call)

    response = client.post(
        "/api/threecx/calls/dial-into-queue",
        json={"source_dn": "0800111222"},
    )

    assert response.status_code == 200
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8002"}
    assert calls == [("0800111222", "8002")]


def test_dial_into_queue_uses_given_queue_dn_override(client, monkeypatch):
    calls = []

    async def fake_make_call(self, dn, destination):
        calls.append((dn, destination))
        return {"CallId": 51}

    monkeypatch.setattr(call_control.ThreeCXClient, "make_call", fake_make_call)

    response = client.post(
        "/api/threecx/calls/dial-into-queue",
        json={"source_dn": "0800111222", "queue_dn": "8005"},
    )

    assert response.status_code == 200
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8005"}
    assert calls == [("0800111222", "8005")]


def test_dial_into_queue_uses_configured_source_dn_when_body_omitted(client, monkeypatch):
    calls = []

    async def fake_make_call(self, dn, destination):
        calls.append((dn, destination))
        return {"CallId": 52}

    monkeypatch.setattr(call_control.settings, "threecx_source_dn", "0800111222")
    monkeypatch.setattr(call_control.ThreeCXClient, "make_call", fake_make_call)

    response = client.post("/api/threecx/calls/dial-into-queue")

    assert response.status_code == 200
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8002"}
    assert calls == [("0800111222", "8002")]


def test_dial_into_queue_400_when_source_dn_missing_everywhere(client):
    response = client.post("/api/threecx/calls/dial-into-queue", json={})

    assert response.status_code == 400
    assert "source_dn" in response.json()["detail"]


def test_dial_into_queue_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(call_control.token_manager, "is_configured", lambda: False)

    response = client.post(
        "/api/threecx/calls/dial-into-queue",
        json={"source_dn": "0800111222"},
    )

    assert response.status_code == 503
