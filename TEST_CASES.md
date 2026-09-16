# QRUIT — Test Cases

Written against `FRD.md` before implementation, per project convention: no feature is
coded without its test cases existing first. Organized by flow stage. Each case names
its expected result — implement to make these pass, not the other way around.

Legend: **[P0]** must pass before first live pull · **[P1]** must pass before client pilot.

---

## 1. Connector contract (applies to every connector: Gmail, Drive, Outlook, SharePoint, local folder)

| ID | Case | Expected result |
|---|---|---|
| CN-1 [P0] | Connector with no credentials configured, `auth_status()` called | Returns `error` with a human-readable reason (e.g. "Google client ID/secret not configured"), never raises unhandled |
| CN-2 [P0] | Connector with valid token but a required scope missing (e.g. send scope revoked), `auth_status()` called | Returns `needs_auth` with message naming the missing permission (e.g. "reconnect to grant send permission") |
| CN-3 [P0] | Connector fully authorized, `auth_status()` called | Returns `ready` with the connected account label |
| CN-4 [P0] | `pull()` called with no query/filter | Returns items per connector default (e.g. Gmail: `newer_than:90d has:attachment`) without raising |
| CN-5 [P0] | `pull()` called twice for the same source with no new items in between | Second call does not produce duplicate `InboundItem`s downstream (dedup at ingestion) |
| CN-6 [P1] | `fetch()` called with an item id that no longer exists on the provider (e.g. message deleted) | Fails gracefully with a clear error surfaced to the console; does not crash the pull job |
| CN-7 | `browse()` called on a storage connector for a folder with 0 files | Returns an empty result with JD/CV counts at 0, not an error |
| CN-8 [P0] | `send()` called without an approval token for the given message | Rejected before any network call is made; no provider API is hit |
| CN-9 [P0] | `send()` called with a valid approval token | Message sent, provider message ID returned and stored on the message record |
| CN-10 | Every connector's OAuth scope set is inspected | No connector requests a write/delete/modify scope on mail, drive, or sites (only read + send-mail) |

## 2. OAuth / auth flow (Gmail, Drive, Outlook, SharePoint — shared PKCE flow)

| ID | Case | Expected result |
|---|---|---|
| AU-1 [P0] | User clicks Connect, completes consent | Refresh token stored encrypted; `auth_status()` flips to `ready` |
| AU-2 [P0] | Access token near expiry (< 2 min) and an API call is made | Token silently refreshed first; call succeeds without user involvement |
| AU-3 [P1] | Refresh token revoked externally (e.g. admin revokes grant) and a call is made | Call fails, `auth_status()` reports `needs_auth`, no crash loop / repeated failed retries |
| AU-4 | State parameter on callback does not match the one issued | Callback rejected, no token exchange attempted (CSRF protection) |
| AU-5 [P1] | Google consent screen scopes: user grants `gmail.readonly` but denies `gmail.send` | `auth_status()` reports `needs_auth` specifically for send, while pull/read still works |

## 3. JD intake and gap-check (Module 1)

| ID | Case | Expected result |
|---|---|---|
| JD-1 [P0] | JD email pulled, attachment present | File converted to DOCX/text, classified as JD |
| JD-2 [P0] | Extracted JD missing salary, location, and seniority | `gap_question` sent to client contact; role paused pending reply |
| JD-3 | Extracted JD has all required fields | No `gap_question` sent; flow proceeds directly to (optional) confirmation or screening |
| JD-4 [P1] | Client replies to `gap_question` with the missing info, reply pulled on next sync | Role's JD updated, gap_question not re-sent for the same fields |
| JD-5 | Role has no client contact configured | `gap_question`/`client_jd_confirmation` silently skipped; role still progresses, console still shows any manual confirmation needed |
| JD-6 | Same JD file pulled twice (e.g. still in the inbox on a second pull) | Not re-processed as a new JD (dedup) |

## 4. CV intake and classification (Module 2, part 1)

| ID | Case | Expected result |
|---|---|---|
| CV-1 [P0] | CV attachment pulled from Gmail/Outlook | Converted to DOCX/text, classified as CV, linked to the correct role |
| CV-2 | Image-only CV (scanned PDF/JPEG) | Routed through OCR before classification; still produces usable text |
| CV-3 [P0] | Same CV pulled from two different sources (e.g. emailed, then also uploaded manually) | De-duplicated — screened once, not twice |
| CV-4 | File that is neither a JD nor CV nor a recognized reply command (e.g. an unrelated attachment) | Classified as "unrecognized"/ignored, does not block the pipeline or crash classification |
| CV-5 | CV missing an extractable candidate email address | Flagged so `candidate_ack`/interview invite cannot silently fail — surfaced in console for manual entry |

## 5. Deep screening and checkpoint approval (Module 2, part 2)

| ID | Case | Expected result |
|---|---|---|
| SC-1 [P0] | All CVs for a role extracted and scored | Shortlist assembled; `checkpoint_request` sent to client contact |
| SC-2 [P0] | Client replies **PROCEED** from the registered client-contact address | On next pull, role advances to candidate notification / interview stage |
| SC-3 [P0] | Client replies **PAUSE** | Role stays paused; no further outbound messages for this role until a new command is received |
| SC-4 [P1] | Client replies **REVISE WEIGHTS** | Screening re-run with adjusted weights (per whatever weight-input mechanism exists); new `checkpoint_request` issued for the revised shortlist |
| SC-5 [P0] | Client replies **SKIP INTERVIEW** | Role proceeds directly toward final report, bypassing Module 3 |
| SC-6 | Reply arrives from an unregistered/incorrect address | Not treated as a valid approval command; role remains paused, no state change |
| SC-7 | Reply text does not match any recognized command (typo, unrelated reply) | Role remains paused; no default/implicit PROCEED ever happens |
| SC-8 [P1] | SLA hours elapse with no reply to `checkpoint_request` | `status_update` reminder sent to client contact; original approval token/checkpoint remains the only way to unblock |
| SC-9 [P0] | `checkpoint_request` send fails (e.g. mailbox `needs_auth`) | Failure recorded with status `failed`, visible in console; role does not silently proceed |

## 6. Candidate notification

| ID | Case | Expected result |
|---|---|---|
| CA-1 [P0] | Role approved (PROCEED) | `candidate_ack` sent to each shortlisted candidate's extracted email |
| CA-2 | Candidate email could not be extracted from their CV (see CV-5) | `candidate_ack` skipped for that candidate with a console flag, not a silent drop |

## 7. AI interview (Module 3)

| ID | Case | Expected result |
|---|---|---|
| IV-1 [P1] | Candidate approved for interview, public host reachable | Reqruit.ai `/v2/interview/start` called with tokenised JD/CV/deep-screen URLs; `interview_invite` sent with working link |
| IV-2 | Public host unreachable from Reqruit.ai (misconfigured `QRUIT_PUBLIC_BASE_URL`) | Interview start fails clearly; surfaced in console, not silently retried forever |
| IV-3 [P1] | Candidate does not open the interview link within a set window | `interview_reminder` sent; further inaction triggers `interview_nudge` |
| IV-4 | Candidate completes the interview | Session status polled to completion; report fetched via `/v2/interview/session/{id}/report` and stored against the candidate |
| IV-5 | Role's SKIP INTERVIEW command applied to a candidate already invited | No further interview reminders/nudges sent for that candidate |

## 8. Final recommendation report (Module 4)

| ID | Case | Expected result |
|---|---|---|
| RP-1 [P0] | All shortlisted candidates have completed interview (or were skipped) | Final report prepared and a checkpoint issued to the client contact for approval |
| RP-2 [P0] | Client approves the final report checkpoint | `report_email` with PDF attachment sent; role's active cycle marked closed |
| RP-3 | Client does not approve (no reply) | Report not sent; role remains in "awaiting final approval" state indefinitely (no auto-send) |

## 9. E-mail alert delivery (cross-cutting)

| ID | Case | Expected result |
|---|---|---|
| AL-1 [P0] | `QRUIT_EMAIL_MOCK=true` | No real network send occurs; message written to `backend/data/outbox/` instead |
| AL-2 [P1] | `QRUIT_EMAIL_MOCK=false`, mailbox connected with send scope | Real send occurs from the connected mailbox address |
| AL-3 | Neither Gmail nor Outlook connected/ready when a send is attempted | Delivery service reports failure clearly; no exception bubbles up uncaught |
| AL-4 [P1] | Test send to an external domain | Message not flagged as spam (manual verification against AL-4 in the source doc: SPF/DKIM/DMARC configured) |
| AL-5 | Reply command arrives attached to an old, already-resolved checkpoint (e.g. late reply after role already proceeded via a later command) | Late/duplicate command does not re-trigger already-completed actions |

## 9b. WhatsApp Business (Meta Cloud API) — send + receive

| ID | Case | Expected result |
|---|---|---|
| WA-1 [P0] | `WHATSAPP_MOCK=true` | No real network send occurs; outbound WhatsApp messages written to the mock outbox instead |
| WA-2 [P0] | `send()` called with an approval token, template message, recipient outside the 24-hour conversation window | Sent as a pre-approved template (not freeform text); rejected before the API call if the template isn't in the approved list |
| WA-3 | `send()` called with freeform text to a recipient inside an active 24-hour window | Allowed; sent as freeform, no template required |
| WA-4 [P0] | `send()` called without a valid approval token | Rejected before any network call, same as every other connector (CN-8) |
| WA-5 [P0] | Webhook receives a valid, correctly-signed inbound text message from Meta | Verified, message extracted, enqueued into the shared inbound pipeline; no classification/screening logic runs inside the webhook handler itself |
| WA-6 [P0] | Webhook receives a request with an invalid/missing signature or wrong verify token | Rejected with no processing; not enqueued |
| WA-7 [P1] | Webhook receives an inbound message containing a media attachment (e.g. CV as document) | Media ID extracted; a follow-up authenticated call to Meta's media endpoint downloads the actual file before classification |
| WA-8 [P0] | Inbound WhatsApp message from a phone number matching a role's registered candidate | Matched to the correct role by phone number, same pattern as matching e-mail replies by sender address |
| WA-9 | Inbound WhatsApp message from an unregistered/unknown phone number | Not matched to any role; held/logged, not silently dropped, not treated as a valid command for an arbitrary role |
| WA-10 [P1] | Webhook endpoint unreachable or down when Meta attempts delivery | Message is lost (no fallback poll exists for WhatsApp) — verify this failure mode is documented/monitored, not silently invisible |
| WA-11 | Candidate replies over WhatsApp with a recognized command (PROCEED/PAUSE/etc.) instead of e-mail | Treated identically to an e-mail reply command — matched to the waiting role, same vocabulary, same rules (no auto-PROCEED on unrecognized text) |
| WA-12 [P1] | `interview_invite` approved for a candidate whose only known contact is a phone number (no e-mail) | Sent over WhatsApp instead of e-mail; no error from a missing e-mail address |
| WA-13 | WABA/template not yet approved by Meta when a template send is attempted | Fails clearly with a "template not approved" style error, not a generic send failure |

## 10. Reqruit.ai integration mock/live modes

| ID | Case | Expected result |
|---|---|---|
| RQ-1 [P0] | `QRUIT_MOCK_API=true` | JD/CV extraction and deep-screening run against mock responses; full pipeline exercisable with no live token |
| RQ-2 [P1] | `QRUIT_MOCK_API=false`, invalid/expired `REQRUIT_AUTH_TOKEN` | Calls fail with a clear auth error surfaced to console, not treated as "no candidates found" |
| RQ-3 [P1] | `QRUIT_INTERVIEW_MOCK=true` | Interview flow exercisable end-to-end (start/next/report) without hitting the live interview API |

## 11. Explicitly-excluded scope — regression guards

| ID | Case | Expected result |
|---|---|---|
| EX-1 | Codebase searched for any webhook route (`/webhooks/*`) registered and enabled | Exactly one: `/webhooks/whatsapp`. No other webhook route exists (no Gmail push notifications, no Drive change feed, no Graph subscriptions, etc.) |
| EX-2 | Any connector configuration checked for write/delete scopes | None present — read + send-mail/send-message only, per FRD §5.1 |
| EX-3 | Any code path checked for a "fully autonomous, no approval" run mode | Not present — Guided mode (approval-gated) is the only implemented mode |
| EX-4 | The WhatsApp webhook handler's code reviewed | Contains only: signature/verify-token check, message extraction, enqueue. No classification, screening, or reply logic lives inside the handler itself |

---

## Notes for whoever implements these

- Each `[P0]` case should have an automated test (unit or integration with mocked
  external services) before its corresponding connector/flow is merged.
- `[P1]` cases may start as documented manual verification steps (mirroring the
  "Verify" sections in the original connector spec) and should be automated as the
  mock layer matures.
- Cases without a priority tag are edge cases — track them, but they don't block a
  merge on their own.
