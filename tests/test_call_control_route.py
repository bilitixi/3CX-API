import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import call_control
from app.services.call_sequence import SequenceState


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
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8002", "call_id": 50}
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
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8005", "call_id": 51}
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
    assert response.json() == {"source_dn": "0800111222", "queue_dn": "8002", "call_id": 52}
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


def test_get_captured_dtmf_returns_empty_when_nothing_captured(client):
    response = client.get("/api/threecx/calls/999/dtmf")

    assert response.status_code == 200
    assert response.json() == {"call_id": "999", "digits": []}


def test_get_captured_dtmf_returns_digits_captured_for_call(client, monkeypatch):
    monkeypatch.setattr(
        call_control.queue_answer_watcher,
        "get_captured_dtmf",
        lambda call_id: ["1234"] if call_id in (60, "60") else [],
    )

    response = client.get("/api/threecx/calls/60/dtmf")

    assert response.status_code == 200
    assert response.json() == {"call_id": "60", "digits": ["1234"]}


def test_start_call_sequence_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(call_control.token_manager, "is_configured", lambda: False)

    response = client.post(
        "/api/threecx/calls/sequence/start",
        json={"dns": ["1003", "1005"]},
    )

    assert response.status_code == 503


def test_start_call_sequence_400_when_queue_dn_missing(client, monkeypatch):
    monkeypatch.setattr(call_control.settings, "threecx_queue_dn", "")

    response = client.post(
        "/api/threecx/calls/sequence/start",
        json={"dns": ["1003", "1005"]},
    )

    assert response.status_code == 400
    assert "queue_dn" in response.json()["detail"]


def test_start_call_sequence_returns_initial_state(client, monkeypatch):
    captured = {}

    def fake_start(dns, queue_dn, interval_seconds):
        captured["args"] = (dns, queue_dn, interval_seconds)
        return SequenceState(id="seq-1", dns=dns, queue_dn=queue_dn, interval_seconds=interval_seconds)

    monkeypatch.setattr(call_control.call_sequence_manager, "start", fake_start)

    response = client.post(
        "/api/threecx/calls/sequence/start",
        json={"dns": ["1003", "1005"], "interval_seconds": 60},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sequence_id"] == "seq-1"
    assert body["status"] == "running"
    assert body["dns"] == ["1003", "1005"]
    assert body["queue_dn"] == "8002"
    assert body["interval_seconds"] == 60
    assert captured["args"] == (["1003", "1005"], "8002", 60)


def test_get_call_sequence_404_when_unknown(client, monkeypatch):
    monkeypatch.setattr(call_control.call_sequence_manager, "get", lambda sequence_id: None)

    response = client.get("/api/threecx/calls/sequence/does-not-exist")

    assert response.status_code == 404


def test_get_call_sequence_returns_state(client, monkeypatch):
    state = SequenceState(id="seq-1", dns=["1003"], queue_dn="8003", interval_seconds=120)
    state.status = "answered"
    state.answered_dn = "1003"
    state.current_index = 0
    state.current_dn = "1003"
    monkeypatch.setattr(
        call_control.call_sequence_manager,
        "get",
        lambda sequence_id: state if sequence_id == "seq-1" else None,
    )

    response = client.get("/api/threecx/calls/sequence/seq-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "answered"
    assert body["answered_dn"] == "1003"


async def _fake_cancel_not_found(sequence_id):
    return None


def test_cancel_call_sequence_404_when_unknown(client, monkeypatch):
    monkeypatch.setattr(call_control.call_sequence_manager, "cancel", _fake_cancel_not_found)

    response = client.post("/api/threecx/calls/sequence/does-not-exist/cancel")

    assert response.status_code == 404


def test_cancel_call_sequence_returns_cancelled_state(client, monkeypatch):
    state = SequenceState(id="seq-1", dns=["1003"], queue_dn="8003", interval_seconds=120)
    state.status = "cancelled"

    async def fake_cancel(sequence_id):
        return state if sequence_id == "seq-1" else None

    monkeypatch.setattr(call_control.call_sequence_manager, "cancel", fake_cancel)

    response = client.post("/api/threecx/calls/sequence/seq-1/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


async def _fake_cancel_latest_none():
    return None


def test_cancel_latest_call_sequence_404_when_none_started(client, monkeypatch):
    monkeypatch.setattr(call_control.call_sequence_manager, "cancel_latest", _fake_cancel_latest_none)

    response = client.post("/api/threecx/calls/sequence/cancel")

    assert response.status_code == 404


def test_cancel_latest_call_sequence_returns_cancelled_state(client, monkeypatch):
    state = SequenceState(id="seq-2", dns=["1005"], queue_dn="8003", interval_seconds=120)
    state.status = "cancelled"

    async def fake_cancel_latest():
        return state

    monkeypatch.setattr(call_control.call_sequence_manager, "cancel_latest", fake_cancel_latest)

    response = client.post("/api/threecx/calls/sequence/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["sequence_id"] == "seq-2"
    assert body["status"] == "cancelled"
