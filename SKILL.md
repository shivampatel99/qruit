---
name: qruit-dev
description: Build and maintain the QRUIT AI recruitment agent — connector pipeline, Reqruit.ai integration, and approval-gated alerting. Use for any work in this repo: adding/changing a channel connector, the ingestion pipeline, checkpoint/approval logic, or alert templates.
---

# QRUIT development skill

QRUIT is an AI recruitment agent that pulls job descriptions and CVs from connected
channels, screens candidates via Reqruit.ai, runs an AI interview, and keeps a client
contact in the loop by email — with a human approval required before anything is sent.

Reference documents (read before making changes):
- `FRD.md` — full functional requirements and end-to-end flow.
- `CLIENT_REQUIREMENTS.md` — the client-facing summary; keep this in sync if scope changes.
- `TEST_CASES.md` — test cases per flow; write/extend tests here before implementing.
- `QRUIT_Connector_Dependencies.html` — the original detailed connector spec (credentials,
  scopes, API calls, per-provider setup). Treat this as the source of truth for exact
  API endpoints, scopes, and env var names.

## Non-negotiable rules (violating these is a bug, not a style choice)

1. **Pull, never push — except the one named WhatsApp webhook.** No connector may open
   a subscription, poll loop, or webhook listener, with exactly one exception: the
   WhatsApp inbound webhook (`/webhooks/whatsapp`), because Meta's Cloud API has no
   endpoint to list/search received messages. That handler must stay minimal — verify
   the request, extract the message, enqueue it into the same inbound pipeline every
   other connector's `pull()` feeds — and must not become a precedent for adding
   webhooks to any other connector without an explicit, separate request.
2. **Send only after approval.** No code path may call a connector's `send()` without
   a valid approval token already existing for that specific outbound message. If you
   are adding a new alert type, it must go through the same checkpoint mechanism as the
   existing ones (`checkpoint_request`, `gap_question`, etc.) unless it is purely
   informational (e.g. `status_update`).
3. **Read-only scopes only.** When adding or touching OAuth scope requests, never add a
   write/delete/modify scope for mail, drive, or sites. The only mutating action any
   connector performs is sending an already-approved message.
4. **One shape for every source.** New connectors must implement the same five
   operations as existing ones — `auth_status()`, `pull()`, `fetch()`, `browse()` (storage
   connectors), `send()` — returning the shared `InboundItem` / `OutboundMessage` /
   `BrowseResult` pydantic models. Don't special-case a connector's shape into the
   ingestion pipeline. WhatsApp is the sanctioned exception (no `browse()`, and its
   `pull()`-equivalent is webhook-fed rather than request-driven) — don't use it as
   justification to loosen this rule for any other connector.
5. **Everything external must be mockable.** Any new integration (a new connector, a new
   Reqruit.ai endpoint) needs a mock/dev mode analogous to `QRUIT_EMAIL_MOCK`,
   `QRUIT_MOCK_API`, `QRUIT_INTERVIEW_MOCK` so the pipeline can be exercised without live
   credentials or real sends.

## Workflow for any change

1. **Re-read the relevant section of `FRD.md`** for the flow you're touching. If the
   change isn't covered by the FRD, stop and clarify scope before writing code — don't
   silently expand scope (e.g. don't add a new webhook connector or an autonomous mode
   because it seems convenient).
2. **Write or extend test cases in `TEST_CASES.md` first**, covering the happy path and
   at least one failure/edge case (missing scope, missing client contact, revoked token,
   duplicate CV, etc.). Do not write implementation code before the test case exists.
3. **Implement**, keeping connectors thin — a connector talks to one external service and
   returns shared models; classification, dedup, screening, and approval logic live in
   the pipeline/services layer, not inside a connector.
4. **Verify manually** using the "Verify" steps pattern from the connector doc (§3–§9):
   connect → pull → confirm classification → send test → confirm receipt/audit trail.
5. **Update `CLIENT_REQUIREMENTS.md`** if the change is client-visible (a new alert, a
   new supported source, a changed approval command).

## Things to double-check before considering a task done

- Does every new outbound alert have a mock/outbox mode?
- Does `auth_status()` distinguish `needs_auth` (fixable by reconnecting) from `error`
  (misconfigured credentials) with a human-readable reason?
- Is the new code path reachable only via an explicit pull/trigger, never a background
  loop?
- If a client contact is unset, does the flow skip client alerts gracefully instead of
  erroring?
- Are secrets read only from environment configuration, never hardcoded or logged?
