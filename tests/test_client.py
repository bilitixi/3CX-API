import pytest

from app.services.client import ThreeCXClient


class FakeTokenManager:
    async def get_valid_token(self, force_refresh: bool = False) -> str:
        return "fake-token"


def make_client(monkeypatch, response_body):
    client = ThreeCXClient(base_url="https://pbx.example.com:5001", token_manager=FakeTokenManager())

    async def fake_request(self, method, path, force_refresh=False, **kwargs):
        return response_body

    monkeypatch.setattr(ThreeCXClient, "_request", fake_request)
    return client


@pytest.mark.asyncio
async def test_get_entity_normalizes_documented_snake_case_participant_fields(monkeypatch):
    # Shape taken from 3CX's documented GET /callcontrol/{dnnumber}/participants/{id} response.
    documented_response = {
        "id": 164,
        "status": "Connected",
        "dn": "1003",
        "party_caller_name": "Jane",
        "party_dn": "1004",
        "callid": 60,
        "legid": 1,
    }
    client = make_client(monkeypatch, documented_response)

    entity = await client.get_entity("/callcontrol/1003/participants/164")

    assert entity["Id"] == 164
    assert entity["Status"] == "Connected"
    assert entity["CallId"] == 60
    # Original documented fields are preserved alongside the normalized ones.
    assert entity["callid"] == 60
    assert entity["status"] == "Connected"


@pytest.mark.asyncio
async def test_get_entity_prefers_existing_pascal_case_fields_if_present(monkeypatch):
    # In case some endpoint/version does send PascalCase, don't clobber it.
    client = make_client(monkeypatch, {"Id": 7, "Status": "Talking", "CallId": 99, "id": 999})

    entity = await client.get_entity("/callcontrol/1003/participants/7")

    assert entity["Id"] == 7
    assert entity["Status"] == "Talking"
    assert entity["CallId"] == 99


@pytest.mark.asyncio
async def test_get_participants_normalizes_each_participant_in_the_list(monkeypatch):
    client = make_client(
        monkeypatch,
        {
            "dn": "1004",
            "participants": [
                {"id": 7, "status": "Connected", "callid": 60},
                {"id": 8, "status": "Ringing", "callid": 60},
            ],
        },
    )

    participants = await client.get_participants("1004")

    assert [p["Id"] for p in participants] == [7, 8]
    assert [p["Status"] for p in participants] == ["Connected", "Ringing"]
    assert [p["CallId"] for p in participants] == [60, 60]


@pytest.mark.asyncio
async def test_make_call_normalizes_call_id_from_snake_case_response(monkeypatch):
    client = make_client(monkeypatch, {"callid": 42, "status": "Ringing"})

    result = await client.make_call("1003", "1004")

    assert result["CallId"] == 42


@pytest.mark.asyncio
async def test_get_entity_returns_none_for_empty_response(monkeypatch):
    client = make_client(monkeypatch, None)

    entity = await client.get_entity("/callcontrol/1003/participants/164")

    assert entity is None
