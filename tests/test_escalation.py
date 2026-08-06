import json

import pytest

from app.services.escalation import QueueAnswerWatcher


class FakeClient:
    def __init__(self, entities=None, participants=None):
        self.base_url = "https://pbx.example.com:5001"
        self.entities = entities or {}
        self.participants = participants or {}
        self.dropped = []

    async def get_entity(self, path):
        return self.entities.get(path)

    async def get_participants(self, dn):
        return self.participants.get(dn, [])

    async def drop_participant(self, dn, participant_id):
        self.dropped.append((dn, participant_id))


class FakeTokenManager:
    def is_configured(self):
        return True

    def on_refresh(self, callback):
        pass


def make_watcher(client):
    return QueueAnswerWatcher(client=client, token_manager=FakeTokenManager())


@pytest.mark.asyncio
async def test_ignores_message_without_nested_event_entity():
    client = FakeClient()
    watcher = make_watcher(client)

    # Flat/legacy shape with no "event" wrapper should be ignored, not crash.
    await watcher._handle_message(json.dumps({"entity": "/callcontrol/1004/participants/7"}))

    assert watcher.get_captured_dtmf(1) == []


@pytest.mark.asyncio
async def test_captures_dtmf_from_nested_event_payload():
    entity_path = "/callcontrol/1004/participants/7"
    client = FakeClient(entities={entity_path: {"CallId": 60, "Status": "Ringing", "Id": 7}})
    watcher = make_watcher(client)

    message = json.dumps(
        {
            "sequence": 1,
            "event": {
                "event_type": 2,
                "entity": entity_path,
                "attached_data": {"dtmf_input": "1234"},
            },
        }
    )
    await watcher._handle_message(message)

    assert watcher.get_captured_dtmf(60) == ["1234"]


@pytest.mark.asyncio
async def test_captures_multiple_dtmf_digits_in_order():
    entity_path = "/callcontrol/1004/participants/7"
    client = FakeClient(entities={entity_path: {"CallId": 60, "Status": "Talking", "Id": 7}})
    watcher = make_watcher(client)

    for digit in ["1", "2", "3"]:
        message = json.dumps(
            {
                "event": {
                    "event_type": 2,
                    "entity": entity_path,
                    "attached_data": {"dtmf_input": digit},
                }
            }
        )
        await watcher._handle_message(message)

    assert watcher.get_captured_dtmf(60) == ["1", "2", "3"]


@pytest.mark.asyncio
async def test_dtmf_still_captured_after_call_already_dropped_from_watch_set():
    entity_path = "/callcontrol/1004/participants/7"
    client = FakeClient(
        entities={entity_path: {"CallId": 60, "Status": "Connected", "Id": 7}},
        participants={"1004": []},
    )
    watcher = make_watcher(client)
    watcher.watch(60)

    # First message answers the call — escalation logic drops it from the watch set.
    await watcher._handle_message(
        json.dumps({"event": {"event_type": 0, "entity": entity_path, "attached_data": {}}})
    )
    assert 60 not in watcher._watched_call_ids

    # A DTMF event on the same (now-unwatched) call should still be captured.
    await watcher._handle_message(
        json.dumps(
            {
                "event": {
                    "event_type": 2,
                    "entity": entity_path,
                    "attached_data": {"dtmf_input": "9"},
                }
            }
        )
    )

    assert watcher.get_captured_dtmf(60) == ["9"]


@pytest.mark.asyncio
async def test_drops_other_participants_on_answer_using_nested_entity_path():
    entity_path = "/callcontrol/1004/participants/7"
    client = FakeClient(
        entities={entity_path: {"CallId": 60, "Status": "Connected", "Id": 7}},
        participants={
            "1004": [
                {"Id": 7, "CallId": 60, "Status": "Connected"},
                {"Id": 8, "CallId": 60, "Status": "Ringing"},
            ]
        },
    )
    watcher = make_watcher(client)
    watcher.watch(60)

    await watcher._handle_message(
        json.dumps({"event": {"event_type": 0, "entity": entity_path, "attached_data": {}}})
    )

    assert client.dropped == [("1004", 8)]
