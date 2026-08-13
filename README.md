# 3CX API

Standalone FastAPI service exposing a 3CX Call Control "call sequence"
workflow, ported from the main backend repo.

## What it does

`POST /api/threecx/calls/sequence/start` rings each DN in an ordered list
into a queue, one at a time. It waits `interval_seconds` for an answer
before dropping that call and trying the next DN, and stops as soon as one
answers. Only one call is ever ringing at a time — the previous one is
dropped before the next starts.

`dns`, `queue_dn`, and `interval_seconds` are all optional in the request
body — they default to `THREECX_SEQUENCE_DNS` / `THREECX_QUEUE_DN` /
`THREECX_SEQUENCE_INTERVAL_SECONDS` from config, so the endpoint can be
called with no body at all:

```bash
curl -X POST https://your-host/api/threecx/calls/sequence/start \
  -H "Authorization: Bearer $API_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dns": ["1003", "1005", "1006"], "queue_dn": "8003"}'
```

The response returns immediately with the sequence's id and initial status
— the dialing itself happens in the background:

```json
{
  "sequence_id": "a1b2c3...",
  "status": "running",
  "dns": ["1003", "1005", "1006"],
  "queue_dn": "8003",
  "interval_seconds": 120,
  "current_index": 0,
  "current_dn": "1003",
  "answered_dn": null
}
```

`status` is one of `running` / `answered` / `exhausted` / `cancelled` /
`error`.

### Checking progress

```bash
curl -H "Authorization: Bearer $API_AUTH_TOKEN" \
  https://your-host/api/threecx/calls/sequence/a1b2c3...
```

### Cancelling

```bash
# a specific sequence
curl -X POST -H "Authorization: Bearer $API_AUTH_TOKEN" \
  https://your-host/api/threecx/calls/sequence/a1b2c3.../cancel

# the most recently started sequence, no id needed
curl -X POST -H "Authorization: Bearer $API_AUTH_TOKEN" \
  https://your-host/api/threecx/calls/sequence/cancel
```

Either drops whichever DN is currently ringing and stops the sequence.

## Configuration

Copy `.env.example` to `.env` and fill in:

- `THREECX_PBX_BASE_URL`, `THREECX_CLIENT_ID`, `THREECX_CLIENT_SECRET` — 3CX
  Call Control API credentials.
- `THREECX_QUEUE_DN` / `THREECX_SEQUENCE_DNS` / `THREECX_SEQUENCE_INTERVAL_SECONDS`
  — defaults used when the request body omits them.
- `API_AUTH_TOKEN` — shared bearer token required on every request to this
  service. Leave blank for local development to skip auth.

Every DN passed to `dns` (or set in `THREECX_SEQUENCE_DNS`) must be in the
3CX Call Control API app's allowed-extensions list in 3CX admin, and must
not be the queue itself — a queue can't originate a call.

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
  token.
- `app/services/client.py` — thin REST wrapper over the 3CX Call Control
  API (`makecall`, participant status lookup/dropping).
- `app/services/call_sequence.py` — `CallSequenceManager` runs each
  sequence as a background task: dials the next DN, polls it for an
  answer, drops it on timeout, and stops as soon as one answers. A separate
  background sweep task prunes finished sequences older than an hour every
  5 minutes, so a long-running process doesn't accumulate history forever.
- `app/api/routes/call_control.py` — the `/calls/sequence/*` routes.
