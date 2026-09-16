# QRUIT — Functional Requirements Document (FRD)

**Product:** QRUIT — AI Recruitment Agent
**Scope of this phase:** Gmail, Google Drive, Outlook, SharePoint/OneDrive, WhatsApp Business (Meta Cloud API, send + receive), e-mail alerts, Reqruit.ai (deep-screening + interview APIs), local/shared folders, public host.
**One deliberate exception:** WhatsApp inbound requires a webhook (Meta's Cloud API has no "list messages" endpoint). This is the single background listener the architecture otherwise avoids everywhere else — see §3, principle 1, and §5.1.
**Source reference:** `QRUIT_Connector_Dependencies.html` (1 Sept 2026)

---

## 1. Purpose

QRUIT automates the repetitive middle of recruiting — collecting job descriptions and CVs from wherever a client or recruiter already keeps them, screening candidates against the role, running a first-round AI interview, and keeping the client and candidates informed — while leaving every decision that matters (who gets shortlisted, who gets interviewed, what gets sent) in a human's hands.

It is not an ATS replacement. It is an agent that sits on top of a recruiter's existing mailbox and file storage and does the reading, matching and drafting, and asks for a yes/no at each meaningful step.

## 2. Actors

| Actor | Role in the system |
|---|---|
| **Recruiter / workspace owner** | Configures channels, roles, settings; runs the agent; is the fallback approver. |
| **Client contact** | The hiring company's point of contact, registered per role. Receives checkpoint requests, gap questions, JD confirmations, status updates, the final report. Approves or redirects by replying to QRUIT's e-mails with a small fixed vocabulary of commands. |
| **Candidate** | Applies via CV (email/folder/upload). Receives acknowledgement, interview invite, reminders, nudges. Takes the AI interview through a public link. |
| **QRUIT agent** | The system itself: pulls from channels, classifies documents, calls Reqruit.ai, renders and queues messages, and pauses at checkpoints until a human unblocks it. |
| **Reqruit.ai** | External API that does the actual extraction, deep-screening scoring, and AI-driven interview. QRUIT is the orchestrator; Reqruit.ai is the intelligence engine for these two functions. |

## 3. Guiding principles (non-negotiable, drive every design decision below)

1. **Pull, never push — with one named exception.** QRUIT never keeps a mailbox subscription, folder watcher, or webhook open for any of its channels, except WhatsApp. Meta's Cloud API has no endpoint to list/search received messages — delivery only happens via a webhook — so WhatsApp inbound (CVs and reply commands arriving over WhatsApp) is implemented as a single, narrowly-scoped webhook endpoint. Every other connector remains strictly pull-on-request.
2. **Send only after human approval.** Every outbound message that isn't purely informational (a status ping) waits for an approval checkpoint. There is no code path where QRUIT contacts a client or candidate without a human having agreed to it first.
3. **Read-only everywhere except sending mail.** No connector is ever granted write, delete, label, or settings access on a connected mailbox or drive. The only "write" any connector performs is sending an approved e-mail.
4. **One consistent shape regardless of source.** Whether a CV arrives via Gmail, SharePoint, or a manual upload, it goes through the same classify → convert → screen → checkpoint pipeline. The rest of the system does not know or care where a document came from.

## 4. End-to-end functional flow

### Stage 0 — Setup (one-time per client/role)
- Recruiter connects the channels that hold this client's JDs/CVs (Gmail and/or Drive; Outlook and/or SharePoint; a local/shared folder; and/or WhatsApp — no connector is mandatory).
- Recruiter creates a **role**, sets the **client contact** e-mail (required for any client-facing alert) and, optionally, an SLA (hours before a reminder fires on a pending approval).
- Recruiter chooses a run mode: **Guided** (every checkpoint waits for explicit approval) is the only mode this phase implements end-to-end; a fully autonomous mode is a later-phase idea, not built here.

### Stage 1 — Job description intake (Module 1)
1. QRUIT pulls candidate JD sources on request: a Gmail/Outlook search, a Drive/SharePoint/local folder, or a pasted/typed JD via chat.
2. The JD file (or text) is normalized to DOCX/text and classified as a JD.
3. Reqruit.ai's `/jd/extract` returns structured fields: role title, seniority, location, salary band, must-have skills, etc.
4. **Gap check:** if salary, location, or seniority is missing, QRUIT sends the `gap_question` e-mail to the client contact and pauses this role until a reply is pulled and applied.
5. **Optional confirmation:** QRUIT can send `client_jd_confirmation` summarizing the parsed JD before screening begins; this checkpoint waits for approval like any other.

### Stage 2 — CV intake (Module 2, part 1)
1. QRUIT pulls CVs the same way JDs were pulled — attachments, folder contents, or manual upload — for this role.
2. Each file is normalized to DOCX/text, de-duplicated, and classified as a CV (vs. a JD or a reply command, which are routed differently).
3. Reqruit.ai's `/resume/extract` structures each CV.

### Stage 3 — Deep screening (Module 2, part 2)
1. Reqruit.ai's `/deep-screening/run` scores every extracted CV against the extracted JD.
2. QRUIT assembles a shortlist and sends `checkpoint_request` to the client contact, describing what will happen next (who's shortlisted, what happens if approved).
3. The client contact replies from the registered address with one of: **PROCEED**, **PAUSE**, **REVISE WEIGHTS**, **SKIP INTERVIEW**. This reply sits in the mailbox until QRUIT's next pull; there is no live listener.
4. On the next pull, QRUIT reads the reply, matches it to the waiting role by sender address, and acts on it. An unrecognized or missing reply leaves the role paused; nothing times out into an automatic PROCEED.
5. If the SLA hours elapse with no reply, a `status_update` reminder goes to the client contact (not a repeat of the checkpoint — the original approval token is still what unblocks the role).

### Stage 4 — Candidate notification
1. Shortlisted (or all screened, depending on client preference) candidates receive `candidate_ack` at the e-mail address extracted from their CV or the sender of the message that carried it — or over WhatsApp, if the candidate's phone number is known and WhatsApp is connected.

### Stage 5 — AI interview (Module 3)
1. On approval (unless SKIP INTERVIEW was chosen), QRUIT calls Reqruit.ai's interview API (`/v2/interview/start`) per shortlisted candidate, passing tokenised URLs to the JD, CV, and deep-screen result (QRUIT's public host must be reachable by Reqruit.ai for this).
2. `interview_invite` is sent to the candidate with a link to `{public host}/interview/{token}`, over e-mail or WhatsApp depending on which contact detail is available and which channel is connected.
3. If unanswered, `interview_reminder` then `interview_nudge` are sent on subsequent pulls/schedules, on the same channel as the original invite.
4. The candidate takes the interview through the hosted link; Reqruit.ai drives the turn-based conversation (`/v2/interview/next`) and QRUIT polls session status and pulls the report (`/v2/interview/session/{id}/report`) when complete.
5. **If the candidate replies over WhatsApp instead of clicking the link** (e.g. sends a CV or a text reply), that message arrives via the WhatsApp webhook (see Stage 1b) and is classified the same way as any other inbound item.

### Stage 1b — WhatsApp inbound (the one webhook in the system)
1. Meta delivers an inbound WhatsApp message (text or media) to QRUIT's webhook endpoint the instant it arrives — this is the one place QRUIT does not "pull on request."
2. The webhook handler does the minimum possible: verifies the request is genuinely from Meta, extracts the message, and drops it into the same inbound queue every other connector's `pull()` feeds into. It does not itself classify, screen, or reply.
3. From that point on, the item is indistinguishable from something pulled from Gmail or a folder: it is normalized, classified (JD / CV / reply command), matched to a role by sender phone number, and follows the same checkpoint rules as any other channel.
4. Media (e.g. a CV sent as a WhatsApp document) is downloaded via a follow-up authenticated call to Meta's media endpoint — the webhook payload only contains a media ID, not the file itself.

### Stage 6 — Final recommendation (Module 4)
1. Once all shortlisted candidates have completed (or been marked skipped for) the interview stage, QRUIT prepares the final recommendation report.
2. This is itself a checkpoint: the client contact must approve before the report goes out.
3. On approval, `report_email` (with a PDF attachment) is sent to the client contact. This closes the role's active cycle.

### Cross-cutting: status and reply handling
- `status_update` can be triggered on request (console button, chat command) at any time, independent of the checkpoint flow.
- All replies (approval commands, JD clarifications, general correspondence) are only ever discovered on a pull — there is no push-based inbox monitoring anywhere in this phase.

## 5. Functional requirements by area

### 5.1 Connectors (channels)
Every connector implements the same five operations so the rest of the system is source-agnostic:

| Operation | Requirement |
|---|---|
| `auth_status()` | Must report one of `ready`, `needs_auth`, `error` with a plain-language reason, shown on the Channels page. |
| `pull(query, limit, folder)` | Must list inbound items (attachments/files) matching a service-native query, on request only — no polling loop, no subscription. |
| `fetch(item_id, dest_dir)` | Must download exactly one item to local disk for conversion/classification. |
| `browse(path)` | Storage connectors (Drive, SharePoint, local folder) only: one level of folder tree with JD/CV detection counts, to let a user pick a folder. |
| `send(message)` | Must only execute after an approval token exists for that outbound message; must return a provider message ID for the audit trail. |

In scope this phase: **Gmail, Google Drive, Outlook, SharePoint, OneDrive, WhatsApp Business (Meta Cloud API), local/shared folders, manual upload.**

**WhatsApp is the one connector that does not fit the five-operation shape cleanly:** it has no `browse()` (not a folder-based service), and its "pull" is not request-driven — inbound items arrive via webhook and are pushed into the same inbound queue `pull()`-based connectors feed. `send()` works exactly like the other connectors: template or session message, approval-gated, returns a `wamid` as the audit-trail message ID. See §5.1a below.

#### 5.1a WhatsApp specifics
| Requirement | Detail |
|---|---|
| Outbound | `send()` posts to Meta's Graph API (`/{phone-number-id}/messages`). Business-initiated messages outside a 24-hour reply window must use a pre-approved template (e.g. `interview_invite`, `interview_reminder`, `status_update`). Inside that window, freeform text is allowed (used for replies within an active back-and-forth). |
| Inbound | Delivered only via a webhook Meta calls (`POST /webhooks/whatsapp`). QRUIT verifies the request (signature/verify-token check), extracts the message, and enqueues it — no other processing happens in the webhook handler itself, to keep this the smallest possible exception to the pull-only rule. |
| Media | Inbound media (e.g. a CV sent as a document) arrives as a media ID only; QRUIT fetches the actual bytes via a separate authenticated `GET /{media-id}` call, same as it "fetches" from any other connector. |
| Matching to a role | Inbound messages are matched to a waiting role by the sender's phone number, the WhatsApp equivalent of matching e-mail replies by sender address. |
| Failure mode | If the webhook is unreachable or misconfigured, WhatsApp simply receives nothing — there is no fallback poll, so webhook uptime is a harder requirement for this channel than for any other. |

### 5.2 Authentication
- Gmail, Drive, Outlook, SharePoint all authenticate via OAuth 2.0 authorization-code-with-PKCE, once per account, after which a refresh token keeps the connection working unattended.
- Tokens are encrypted at rest; a missing or revoked scope must surface as `needs_auth` with a specific "reconnect to grant X permission" message — never a silent failure.
- Local folders and manual upload require no authentication.

### 5.3 Document handling
- Every fetched file is normalized to DOCX (Reqruit.ai's required input format) plus plain text; images go through OCR.
- Every file is classified as JD, CV, or command/reply text before anything else happens to it.
- De-duplication must prevent the same CV pulled twice (e.g., re-pulled from the same folder) from being screened twice.

### 5.4 Reqruit.ai integration
- All JD/CV/deep-screening/interview calls go through Reqruit.ai's REST APIs using a bearer token; inputs are passed as tokenised URLs QRUIT's public host serves, not as uploaded bytes.
- A mock mode must exist for development (`QRUIT_MOCK_API`, `QRUIT_INTERVIEW_MOCK`) so the full pipeline can be exercised without a live token or real client-facing calls.

### 5.5 Alerts and approvals
- Every template in the alert table (§7 of the source doc) must be implemented: `checkpoint_request`, `status_update`, `gap_question`, `client_jd_confirmation`, `report_email`, `candidate_ack`, `interview_invite`, `interview_reminder`, `interview_nudge`.
- An outbox mode (`QRUIT_EMAIL_MOCK=true`) must exist so no real mail leaves during development/testing.
- A role with no client contact set must silently skip client-facing alerts rather than error.
- Reply commands (PROCEED / PAUSE / REVISE WEIGHTS / SKIP INTERVIEW) must be case-insensitive and matched by sender address to the correct waiting role.

### 5.6 Public host
- A stable HTTPS host is required for: Reqruit.ai to fetch QRUIT-hosted files, candidates to reach their interview link, OAuth redirect callbacks, and now the WhatsApp webhook.
- Only `/public/*`, `/interview/*`, and `/webhooks/whatsapp` are internet-facing; `/api/*` must stay restricted to the internal network/VPN.

### 5.7 Configuration and secrets
- All credentials live in environment configuration (`backend/.env`), never in code or chat.
- A single server secret (`QRUIT_APPROVAL_SECRET`) encrypts stored OAuth tokens and signs approval tokens.

## 6. Non-functional requirements

| Area | Requirement |
|---|---|
| Security | No connector holds write/delete scope on any external service. Secrets never transit e-mail/chat. Tokens encrypted at rest. |
| Auditability | Every sent message records a provider message ID; every failed send is recorded with status `failed` and visible in the console. |
| Deliverability | Sending domain must have SPF/DKIM/DMARC configured; without this, candidate mail risks landing in spam. |
| Resilience | Expired/revoked OAuth grants must degrade to a clear `needs_auth` state, not a crash or silent no-op. |
| Testability | Every external dependency (Gmail, Outlook, Reqruit.ai, e-mail sending) must be mockable so the pipeline is testable without live credentials. |

## 7. Explicitly out of scope this phase

- Any inbound webhook **other than** the single WhatsApp messages webhook (no other channel gets one — this stays the one named exception, not a precedent).
- Fully autonomous ("no human checkpoint") run mode.
- Google service-account domain-wide delegation / Microsoft client-credentials (app-only) authentication — sign-in-once with refresh tokens is the only auth model this phase implements.
- SMTP-relay fallback for sending mail without OAuth.
- Non-default SharePoint document libraries (only the site's default library is read).

## 8. Open questions for the client (carry into the req doc)

1. Which mailbox identity should connect for each client — a dedicated `recruiting@` mailbox, or a named recruiter? (Recommendation: dedicated mailbox.)
2. Do candidates need to be notified even when not shortlisted, or only shortlisted candidates?
3. Should the recruiter's own inbox get a copy of every client-facing alert? (Planned but not yet wired — confirm priority.)
4. What is the acceptable SLA (hours) before a pending approval triggers a reminder to the client?
5. Should candidate-facing messages prefer WhatsApp over e-mail when both are available, or only fall back to WhatsApp when no e-mail address was found?
6. Whose phone number registers as the "WhatsApp business number" candidates and clients will see and reply to — same identity question as the recruiting mailbox (see C1-equivalent decision).
