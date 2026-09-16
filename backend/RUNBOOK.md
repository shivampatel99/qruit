# Runbook

Operational notes for the failure modes the loop protocol (`QRUIT_CLAUDE_CODE_LOOP.md`
§6 Definition of Done) requires a runbook for. See `README.md` for setup/architecture.

## Rotating `QRUIT_APPROVAL_SECRET`

This one secret drives two things (`app/crypto.py`): encrypting stored OAuth tokens
(`TokenCipher`) and signing approval-checkpoint tokens (`ApprovalTokenSigner`).
Rotating it invalidates both, predictably:

- **Stored OAuth tokens** (Gmail/Outlook/Drive/SharePoint/OneDrive) fail to decrypt.
  `OAuthChannelBase.load_token()` catches this and returns `None`, so every connector's
  `auth_status()` reports `needs_auth: "not yet connected — click Connect"` — as if
  nothing were ever connected. **Fix:** reconnect each connector via `/oauth/connect`
  (or the console UI). No data is lost; only the token needs re-granting.
- **Outstanding approval tokens** (any unresolved `checkpoint_request` / `gap_question`
  link already emailed to a client contact) stop verifying — `ApprovalTokenSigner.verify()`
  returns `None`, so a PROCEED/PAUSE reply against an old link is silently ignored, not
  applied incorrectly. **Fix:** re-trigger the checkpoint (e.g. `POST /api/roles/{id}/status_update`
  or re-run the step that issued it) so a freshly signed link goes out.

Always generate the new secret the same way the `.env.example` comment does
(`python -c "import secrets; print(secrets.token_urlsafe(48))"`), roll it out, then
walk through the two fixes above rather than waiting for a client to report a broken link.

## Token/credential expiry per connector

| Doc ref | Connector | What expires | How it surfaces | Fix |
|---|---|---|---|---|
| MS-2 | Outlook/SharePoint/OneDrive | Refresh token invalidated by Entra Conditional Access | `auth_status()` → `needs_auth` (missing scope or dead token look identical on the Graph side — the `reason` string names the connector, not the root cause) | Reconnect via `/oauth/connect`; if it recurs immediately, check the Conditional Access policy in Entra, not the app registration |
| WA-4 | WhatsApp | System-user permanent token expires/is revoked in Meta Business Manager | **Not** detected by `auth_status()` — it only checks that `WHATSAPP_TOKEN`/`WHATSAPP_PHONE_ID` are *configured*, not valid. An expired token surfaces as failed sends (`status: failed` in the audit log / a failed `send()` job result), same shape as any other delivery failure | Generate a new system-user token in Meta Business Manager, update `WHATSAPP_TOKEN` in `.env`, restart the process (no per-role reconnect needed) |
| RQ-1 | Reqruit.ai (deep-screening + interview) | `REQRUIT_AUTH_TOKEN` revoked/expired | Not a connector `auth_status` at all — it's a single API token in `.env`. A 401/403 raises `ReqruitAuthError` (`app/services/reqruit_client.py`), which propagates out of the arq job — visible as `GET /api/jobs/{id}` → `status: failed` with that error text | Rotate `REQRUIT_AUTH_TOKEN`, restart; re-trigger the failed job (`run_screening`/`pull`) — it's safe to re-run, see idempotency note below |

## What `needs_auth` vs `error` means, operationally

Every connector's `auth_status()` returns one of (`app/models.py::AuthState`):

- **`ready`** — usable now.
- **`needs_auth`** — fixable by reconnecting: no token stored yet, the stored token is
  missing a required scope, or (Google/Microsoft) the app registration exists but the
  user hasn't granted consent. Send the client/operator to `/oauth/connect` for that
  provider.
- **`error`** — misconfigured at the app level, not the user level: `GOOGLE_CLIENT_ID`/
  `MS_CLIENT_ID`/`WHATSAPP_TOKEN`/etc. missing from `.env` entirely. No amount of
  reconnecting fixes this — it needs a config/deploy change. The `reason` field always
  names the specific missing variable (see `Settings.validate_for_boot` and each
  connector's `auth_status`), never a generic "not configured".

## Re-running a failed or interrupted job is safe

Every ingestion/screening/send path is keyed by an idempotency/dedup key
(`app/storage.py::claim_dedup_key`, `compare_and_swap_role_status`) — re-enqueuing or
manually retrying a job that failed partway (a crashed worker, a Reqruit.ai timeout, a
transient DB error) will not double-ingest a CV, double-run screening, or double-send an
alert. See `tests/test_concurrency.py` and `tests/test_chaos.py` for the guarantees this
runbook is describing, not just asserting.
