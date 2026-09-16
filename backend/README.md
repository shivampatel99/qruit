# QRUIT backend

The backend for QRUIT, an AI recruitment agent that pulls job descriptions and
CVs from connected channels (Gmail, Outlook, Drive, SharePoint/OneDrive, local
folders, manual uploads, WhatsApp), screens candidates via Reqruit.ai, runs an
AI interview, and keeps a client contact in the loop — with every non-trivial
outbound message gated behind an explicit human approval.

See `../FRD.md`, `../SKILL.md`, and `../TEST_CASES.md` at the repo root for
the full functional spec and the test cases this code satisfies, and
`../QRUIT_Connector_Dependencies.html` for exact per-provider API/scope
details.

## Setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The defaults in `.env.example` run everything in mock mode
(`QRUIT_EMAIL_MOCK`, `QRUIT_MOCK_API`, `QRUIT_INTERVIEW_MOCK`,
`WHATSAPP_MOCK` all `true`) — no live credentials are required to exercise
the full pipeline.

## Running in mock mode

```bash
uvicorn app.main:app --reload --port 8787
```

Then, for example:

```bash
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/api/channels
curl -X POST http://127.0.0.1:8787/api/roles \
  -H "Content-Type: application/json" \
  -d '{"title": "Senior Backend Engineer", "client_contact": "client@example.com"}'
```

Outbound alerts in mock mode are written to `backend/data/outbox/` instead of
being sent, so the whole checkpoint/approval flow can be driven end to end
without touching a real mailbox, WhatsApp number, or Reqruit.ai token.

## Running tests

```bash
pytest
```

All tests run against mock connectors/services — no network access or live
credentials are needed.

## Load & chaos testing

```bash
python -m loadtest.run
```

Runs the loop-doc's §4 scenarios (ingestion throughput, queue backlog drain,
end-to-end pipeline, a short soak) against the real local Postgres/Redis in
mock mode, printing throughput/latency/error-rate numbers — see
`loadtest/run.py`. The §5 chaos checklist (DB/Redis down, corrupted files)
is covered as regular assertions in `tests/test_chaos.py`, run by `pytest`
like everything else.

See `RUNBOOK.md` for what to do about the failure modes these tests exercise
(secret rotation, token expiry, `needs_auth` vs `error`).

## Going live

Nothing here needs real credentials to run today. When they're available,
fill in `backend/.env` per the comments in `.env.example` and flip the
relevant `*_MOCK` flag(s) to `false`. At a high level, going live for each
piece needs:

- **Gmail / Drive**: a Google Cloud project with the Gmail + Drive APIs
  enabled, an Internal OAuth consent screen, and a Web OAuth client
  (`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`).
- **Outlook / SharePoint / OneDrive**: an Entra ID app registration with
  admin-consented Graph permissions (`MS_CLIENT_ID`/`MS_CLIENT_SECRET`/`MS_TENANT`).
- **WhatsApp**: a verified Meta Business Manager, a WhatsApp Business Account,
  a permanent system-user token, and approved message templates
  (`WHATSAPP_TOKEN`/`WHATSAPP_PHONE_ID`/`WHATSAPP_APP_SECRET`), plus the
  webhook callback URL registered with Meta.
- **Reqruit.ai**: a deep-screening API token and (separately) interview API
  access (`REQRUIT_AUTH_TOKEN`, base URLs).
- **Public host**: a stable HTTPS host (`QRUIT_PUBLIC_BASE_URL`) so Reqruit.ai
  can fetch QRUIT-hosted files, candidates can reach interview links, OAuth
  redirects resolve, and the WhatsApp webhook is reachable.

`QRUIT_Connector_Dependencies.html` is the authoritative source for exact
scopes, endpoints, and setup steps for each of the above.

## Architecture notes

- Every connector (`app/connectors/`) implements the same shape —
  `auth_status()`, `pull()`, `fetch()`, `send()`, and `browse()` for storage
  connectors — returning the shared `InboundItem` / `OutboundMessage` /
  `BrowseResult` models from `app/models.py`. WhatsApp is the one sanctioned
  exception: it has no `browse()`, and inbound arrives via the one webhook in
  the system (`POST /webhooks/whatsapp`) instead of a request-driven `pull()`.
- Classification, deduplication, screening, and approval logic all live in
  `app/pipeline/` and `app/services/`, never inside a connector.
- No `send()` executes without a cryptographically signed approval token
  (`app/crypto.py`); connectors reject an unapproved send before making any
  network call (see `app/connectors/base.py::verify_approval`).
- OAuth tokens are encrypted at rest with a Fernet key derived from
  `QRUIT_APPROVAL_SECRET` (`app/crypto.py::TokenCipher`).
- Every external HTTP call retries transient 429/5xx with backoff
  (`app/services/resilience.py::with_backoff`); Reqruit.ai's client is also
  wrapped in a Redis-backed circuit breaker so a sustained outage fails fast
  instead of retrying forever (`ReqruitClient`/`ReqruitInterviewProvider` in
  `app/services/reqruit_client.py`).
