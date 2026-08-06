# 3CX API

Standalone FastAPI service exposing the 3CX Call Control "dial into queue"
workflow, ported from the main backend repo.

## What it does

`POST /api/threecx/calls/dial-into-queue` makes `source_dn`'s phone ring
first. Once answered, it is connected into `queue_dn`, which then rings
whichever agents are logged into that queue per its configured ring
strategy. As soon as one agent answers, the other still-ringing queue
members are automatically dropped.

Both `source_dn` and `queue_dn` are optional in the request body — they
default to `THREECX_SOURCE_DN` / `THREECX_QUEUE_DN` from config, so the
endpoint can be called with no body at all:

```bash
curl -X POST https://your-host/api/threecx/calls/dial-into-queue \
  -H "Authorization: Bearer $API_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_dn": "0800111222"}'
```

## Configuration

Copy `.env.example` to `.env` and fill in:

- `THREECX_PBX_BASE_URL`, `THREECX_CLIENT_ID`, `THREECX_CLIENT_SECRET` — 3CX
  Call Control API credentials.
- `THREECX_QUEUE_DN` / `THREECX_SOURCE_DN` — defaults used when the request
  body omits them.
- `API_AUTH_TOKEN` — shared bearer token required on every request to this
  service. Leave blank for local development to skip auth.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Tests

```bash
pytest
```

## Architecture

- `app/services/token_manager.py` — caches and refreshes the 3CX bearer
  token, notifying subscribers (the WebSocket watcher) on refresh.
- `app/services/client.py` — thin REST wrapper over the 3CX Call Control
  API (`makecall`, participant listing/dropping).
- `app/services/escalation.py` — `QueueAnswerWatcher` listens on 3CX's
  callcontrol WebSocket and drops the other still-ringing participants of a
  watched call as soon as one answers.
- `app/api/routes/call_control.py` — the `/calls/dial-into-queue` route.
