# QRUIT Production Hardening — Claude Code Loop Prompt

Paste this whole file into Claude Code as your instruction (or save it as `CLAUDE.md` /
`CLAUDE_LOOP.md` in the repo root and reference it). It is written as a **loop protocol**:
Claude Code should keep cycling through build → test → break → fix → re-test until the
Definition of Done at the bottom is fully green, not just "looks done."

Read this note before you run it: no instruction set makes software "100% bug-free."
What this prompt does instead is force a real quality bar — concrete tests, load numbers,
and edge cases — so gaps get caught by CI instead of by a client in production. Don't let
Claude Code (or yourself) declare victory just because it stopped throwing errors in a
manual smoke test.

---

## 0. Ground truth for this project

The attached document `QRUIT · AI recruitment agent · Integration guide` (v1 Sept 2026) is
the spec of record. Treat it as authoritative for: which connectors exist (Gmail, Google
Drive, Outlook, SharePoint/OneDrive, e-mail alerts, WhatsApp Business, Reqruit.ai APIs,
local/shared folders), what each one's `auth_status / pull / fetch / browse / send`
functions must do, which scopes/permissions are required, and the two hard architectural
rules: **pull-only** (no persistent subscriptions/webhooks except the optional WhatsApp
inbound path) and **send only after human approval**.

Claude Code must not invent new architecture that contradicts this doc (e.g. don't add a
webhook listener for Gmail, don't auto-send without an approval token) unless I explicitly
tell it a requirement changed.

---

## 1. Non-negotiable production requirements

These apply to every connector and every request path, not just the "happy path":

1. **Concurrency correctness.** Multiple workers/requests can pull the same channel, the
   same role, or process the same inbound item at the same time. There must be no double
   ingestion, double-send, or lost update. Use DB-level uniqueness constraints / row locks
   / idempotency keys — not "we'll probably not hit that race."
2. **Backpressure & rate-limit compliance.** Every external call (Gmail, Graph, Meta,
   Reqruit.ai) must respect that provider's documented limits (see doc §7 AL-6, §8, §9) and
   implement exponential backoff + jitter on 429/5xx, plus a circuit breaker so one flaky
   provider doesn't take down the whole pipeline.
3. **Async/non-blocking I/O** for all external HTTP calls (httpx.AsyncClient is already the
   chosen stack — use it consistently, don't mix in blocking calls that stall the event
   loop).
4. **Horizontal scalability.** The ingestion pipeline (fetch → convert → classify →
   dedupe → hand to Reqruit.ai) must run as a queue-backed worker pool (e.g. Celery/RQ/
   arq), stateless per worker, so it can scale to N processes/pods under load. No
   in-memory-only state that breaks when you add a second worker.
5. **Database resilience.** Connection pooling sized for concurrent load, retries on
   transient connection errors, migrations are reversible, and every write that matters
   (channel_accounts tokens, role status, approval tokens) is inside a transaction.
6. **Idempotent job processing.** Re-running a pull/fetch/classify job for the same item
   must not create duplicates or re-trigger sends. Use the opaque item reference /
   content hash mentioned in the doc as the dedupe key.
7. **Security.**
   - OAuth tokens encrypted at rest (Fernet, key from `QRUIT_APPROVAL_SECRET`) — verify
     this actually happens, not just documented.
   - Never log tokens, client secrets, or full message bodies at INFO level.
   - Webhook endpoints (if WhatsApp inbound / C3 is ever turned on) must verify Meta's
     signature before processing.
   - `/api/*` must reject requests arriving via the public hostname (doc §11 NW-3) —
     write a test that actually proves this, not just trust the router config.
8. **Observability.** Structured JSON logs with request/job IDs, metrics for: queue depth,
   per-connector call latency & error rate, send success/failure counts, token refresh
   failures. Health check endpoint that checks DB + at least one external dependency.
9. **Graceful degradation.** If Reqruit.ai, a mail provider, or WhatsApp is down, the rest
   of the system keeps working (jobs queue and retry) instead of crashing or blocking
   unrelated roles.
10. **Config/secrets hygiene.** All values in the doc's `backend/.env` map are required at
    startup validation — fail fast with a clear error naming the missing var, don't fail
    obscurely three requests later.

---

## 2. The loop protocol

Instruct Claude Code to run this cycle **per connector, then again end-to-end**:

```
LOOP:
  1. STATE the acceptance criteria for this unit of work in plain language before writing
     code (what does "done" mean here, concretely — list the specific tests that must pass).
  2. IMPLEMENT the smallest coherent slice (e.g. one connector's pull()).
  3. WRITE tests for it BEFORE moving on:
       - unit tests (mocked external API) covering happy path + every edge case in §3 below
       - integration test against a sandbox/test account where one exists
       - a concurrency test that fires N parallel calls at the same resource
  4. RUN the full test suite, not just the new tests.
  5. IF anything fails or is flaky (run it 3x to catch flakiness):
       - diagnose root cause, don't patch the symptom
       - fix
       - GOTO 4
  6. IF everything passes:
       - run the relevant load test (§4) for this component
       - if throughput/error-rate targets are missed, profile, fix, GOTO 4
  7. Only then mark this unit done and move to the next connector/module.
REPEAT for: Gmail, Drive, Outlook, SharePoint/OneDrive, e-mail alerts, WhatsApp,
Reqruit.ai clients, local folder channel, ingestion pipeline, approval/delivery service.
FINALLY: run the full end-to-end load test (§4.4) and the chaos checklist (§5) against the
whole system together, since bugs at integration boundaries won't show up per-connector.
```

Tell Claude Code explicitly: **do not skip step 3 to save time, and do not report a module
as complete if step 6's load test wasn't actually run.**

---

## 3. Edge cases to test per connector (pulled from the spec — not exhaustive, extend as you find more)

**Gmail**
- Empty mailbox / zero matching messages
- Message with attachment > provider size limit, or 0-byte attachment
- Non-DOCX/PDF attachment (unsupported type) — must be handled, not crash the pipeline
- Multiple attachments on one message, mixed valid/invalid
- Access/refresh token expired mid-`pull()` — must trigger silent refresh, not fail the request
- Refresh token revoked by user/admin — must surface as `needs_auth`, not throw 500
- Reply command (`PROCEED`/`PAUSE`/etc.) sent from an address that is *not* the registered
  client contact — must be ignored/flagged, not blindly trusted
- Duplicate message re-appearing in a later `pull()` (label added late, re-sync) — must not
  re-ingest
- `gmail.send` scope missing (only `gmail.readonly` granted) — `send()` must fail loud and
  early, not silently drop the approval
- Rate-limited (429) mid-batch pull — backoff and resume, don't lose already-fetched items

**Google Drive**
- Folder shared with wrong permission level (no access) vs correct (Viewer)
- Folder ID from a Shared Drive without `supportsAllDrives=true` set — must be caught, not
  return an empty/misleading result
- File renamed/moved between `browse()` and `fetch()`
- Very large folder (thousands of files) — pagination must be handled, not just the first
  page

**Outlook / Graph**
- Admin consent not yet granted for `Files.Read.All`/`Sites.Read.All` — `auth_status()` must
  report exactly which scope is missing
- Conditional Access silently invalidating the refresh token (doc MS-4) — detect and surface
  as `needs_auth`, don't retry forever
- `$search` special characters in a user-typed query (quotes, `and`/`or` as literal words)
- Shared mailbox attempted without `Mail.Read.Shared` — clear error, not a generic 403

**SharePoint/OneDrive**
- Site ID resolves but the account has no library permission
- Folder path with spaces/unicode characters
- Non-default document library referenced (doc SP-4 — should fail clearly until that
  one-line change is made, not silently read the wrong library)

**E-mail alerts / delivery**
- No client contact registered on a role — must skip client alerts without blocking the
  pipeline
- Two different connectors both "ready" — verify the documented precedence
  (email→Gmail/Outlook, phone→WhatsApp) is deterministic
- Send fails (bounce/reject) — status must be recorded as `failed`, visible in console, and
  retried according to a defined policy (not silently dropped)
- `QRUIT_EMAIL_MOCK=true` vs `false` — confirm mock path never leaks a real send call
- SPF/DKIM/DMARC not set up — this can't be tested in code, but the health check should be
  able to warn if outbound bounce rate spikes

**WhatsApp**
- Message outside the 24-hour window with no approved template — must fail with a clear
  error, not send raw text that Meta will reject
- Template placeholder count mismatch (e.g. template expects 3 vars, code sends 2)
- Token near/at expiry
- Test recipient not in the WA-8 allow-list during development mode

**Reqruit.ai clients**
- API timeout / 5xx mid-poll — must retry with backoff, not hang forever or lose job state
- Malformed/partial JSON response
- `QRUIT_PUBLIC_BASE_URL` unreachable from Reqruit.ai's side (their fetch fails) — must
  surface clearly, since this is silent-failure-prone (doc RQ-3)
- Concurrent deep-screening requests for the same role

**Local folder / manual upload**
- Symlink loops or paths outside the configured root (path traversal attempt)
- Unsupported file type, corrupted file, OCR failure on an image CV
- Race: two uploads with the same filename at the same time

**Cross-cutting**
- `QRUIT_APPROVAL_SECRET` rotated — old encrypted tokens should fail predictably (and the
  runbook should say what to do), not corrupt data silently
- Approval token reused twice (double-click "approve") — must not double-send
- Clock skew between QRUIT host and providers affecting token expiry checks

---

## 4. Load & concurrency testing (this is the part most "vibe-coded" apps skip)

Tell Claude Code to actually produce and run these, with numbers you can point to:

1. **Per-connector load test:** simulate the realistic peak (define this with your client —
   e.g. "500 CVs land in one hour during a government hiring drive") using mocked provider
   responses, and measure: throughput, p50/p95/p99 latency, error rate, memory growth over
   a sustained run.
2. **Concurrency test:** N simultaneous `pull()`/`fetch()`/ingestion jobs for the *same*
   role and the *same* channel — assert zero duplicate CVs, zero double-sends, no deadlocks.
3. **Queue backlog test:** push far more jobs than workers can process immediately and
   confirm the system queues gracefully (no crash, no dropped jobs, no unbounded memory) and
   drains correctly once load subsides.
4. **End-to-end load test:** drive the whole pipeline (ingest → classify → dedupe → screen →
   approval → deliver) at target load with a realistic mock of Reqruit.ai's latency, and
   confirm approval checkpoints and SLA reminders still fire correctly under load.
5. **Soak test:** run at moderate load for an extended period (hours) to catch memory leaks,
   connection pool exhaustion, and token-refresh timing bugs that only show up over time.

Use whatever tooling fits the stack (locust, k6, or a custom asyncio harness) — the
important part is that Claude Code reports actual numbers against a stated target, not "it
seemed fast."

---

## 5. Chaos / failure-injection checklist

Have Claude Code deliberately break each dependency one at a time and confirm the system
degrades gracefully instead of cascading:

- Kill the DB connection mid-request
- Return 500/timeout from each external API in turn
- Revoke an OAuth token while jobs are in flight
- Fill the job queue to capacity
- Corrupt/truncate a fetched file before conversion
- Restart a worker mid-job — confirm the job resumes or is retried, not lost or duplicated

---

## 6. Definition of Done (don't accept "finished" without this)

- [ ] Every connector's `auth_status/pull/fetch/browse/send` implemented per spec, matching
      the API calls table in the doc exactly (no extra scopes requested, no missing ones)
- [ ] Unit test suite covers every edge case in §3, green, and not flaky across 3 runs
- [ ] Integration tests pass against at least one real sandbox account per provider
- [ ] Load tests in §4 run with recorded numbers meeting an agreed target (define the
      number with your client — don't let Claude Code pick an arbitrary "it handles load")
- [ ] Chaos checklist in §5 all pass with graceful degradation, no data loss
- [ ] Structured logging + basic metrics in place; health check reflects real dependency
      status
- [ ] Config validation fails fast and clearly on any missing required env var from the
      doc's configuration map
- [ ] No secrets/tokens ever appear in logs (grep the logs in a test run to confirm)
- [ ] `/api/*` rejection via public hostname is covered by an actual test, not just config
- [ ] A short runbook exists for: rotating `QRUIT_APPROVAL_SECRET`, MS-2/WA-4/RQ-1 token
      expiry, and what "needs_auth" on each connector means operationally

---

## 7. How to actually use this with Claude Code

1. Put the integration guide doc and this file in your repo (or paste both into context).
2. Give Claude Code the env vars from §13's configuration map once you get them Monday —
   don't let it fake/mock its way past missing real credentials for anything you're about
   to test against a real sandbox.
3. Tell Claude Code: "Follow the loop protocol in section 2 of QRUIT_CLAUDE_CODE_LOOP.md.
   Work one connector at a time. Do not mark a connector done until its tests, including
   the load test, actually pass — show me the test output and the load numbers, don't just
   tell me it's done."
4. After each connector is "done," re-run the full suite (regressions across connectors are
   common, especially around the shared OAuth base and the ingestion pipeline).
5. Only after every connector is individually done, run the end-to-end load test and chaos
   checklist against the whole system.
