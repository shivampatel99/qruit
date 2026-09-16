# QRUIT — What We're Building (Client Requirement Summary)

## In one paragraph

QRUIT is an assistant that takes over the repetitive part of hiring. It reads job descriptions and CVs from wherever you already keep them — your inbox, Google Drive, SharePoint, a shared folder, or WhatsApp — scores each candidate against the role, runs a first-round AI interview, and keeps you updated by email or WhatsApp. It never sends anything or moves a candidate forward without you saying yes first.

## What it does for you

- **Collects job descriptions and CVs automatically** from your email, Drive, SharePoint/OneDrive, a shared folder, or WhatsApp — no manual forwarding or re-uploading.
- **Fills in the gaps** — if a job description is missing salary, location, or seniority, QRUIT emails you to ask before moving forward.
- **Screens every candidate** against the role and gives you a shortlist with reasoning, not just a list of names.
- **Waits for your approval** before doing anything client- or candidate-facing. You get an email (or WhatsApp message) describing what's about to happen, and you reply with a simple word: PROCEED, PAUSE, REVISE WEIGHTS, or SKIP INTERVIEW.
- **Runs a first-round AI interview** with shortlisted candidates through a private link, sent by email or WhatsApp, and chases them with reminders if they don't respond.
- **Delivers a final report** — a clear, ranked recommendation with supporting detail, sent as a PDF once you approve it.
- **Keeps you in the loop** with status updates whenever you ask, or automatically if something's been waiting too long for a decision.
- **Talks over WhatsApp both ways** — candidates can send a CV or reply to an invite over WhatsApp, not just email, and QRUIT picks it up the moment it arrives.

## What it will NOT do (in this phase)

- **No fully automatic mode.** Nothing goes out to a client or candidate without a human approving it first — there's no "set and forget" mode yet.
- **It won't watch your inbox live.** QRUIT checks your connected email/Drive/SharePoint accounts when you ask it to (or on a schedule), not the instant something arrives. This is intentional — it keeps things predictable and avoids surprise sends. WhatsApp is the one exception: those messages arrive instantly, because WhatsApp itself doesn't offer any other way to receive them.
- **Read-only on your accounts.** QRUIT can read and send email, but it can never delete, label, or change settings on your mailbox, Drive, or SharePoint. The only thing it ever "writes" is an email you've approved.

## What we need from you to get started

| # | What | Why |
|---|---|---|
| 1 | Confirm which mailbox/folder(s) hold your job descriptions and CVs today | So we connect the right sources |
| 2 | A dedicated recruiting mailbox (recommended) e.g. `recruiting@yourcompany.com` | Keeps the connection clean and independent of any one employee's account |
| 3 | Admin sign-off to register QRUIT as an approved application in your Google Workspace / Microsoft 365 | Required once, by whoever manages your company's Google/Microsoft admin console |
| 4 | The email address that should receive approvals and updates for each role | This is who QRUIT will write to |
| 5 | How long we should wait before nudging you if an approval is pending | Sets the reminder timing — a sensible default is provided if you're not sure |
| 6 | A WhatsApp Business number, verified through Meta, and completed business verification for your company | Required before any WhatsApp messages can be sent or received — this is a one-time setup with Meta and can take a few days |
| 7 | Wording approval for the WhatsApp message templates (interview invite, reminder, status update) | Meta requires template messages to be pre-approved before they can be sent outside an active conversation |

## How a typical role will flow, day to day

1. You post or forward a job description (or drop it in a connected folder). QRUIT picks it up next time it checks.
2. If anything important is missing from the JD, you get a quick email asking for it.
3. CVs come in the same way — forwarded, uploaded, sitting in a folder, or sent straight over WhatsApp.
4. QRUIT screens them and emails you a shortlist with its reasoning. You reply PROCEED (or PAUSE, or ask for different weighting).
5. Shortlisted candidates are invited to a short AI interview through a link — sent by email or WhatsApp, whichever fits — no scheduling back-and-forth needed.
6. Once interviews are done, you get a final report to review and approve.
7. On your approval, the report is sent to you as a PDF and the role's active cycle closes.

You're never surprised by a message going out — every client- or candidate-facing message, on any channel, is something you've already agreed to.

## Timeline dependency notes (plain language)

- Getting Gmail/Outlook and Drive/SharePoint connected requires roughly 20 minutes with someone who has admin rights on your company's Google or Microsoft account.
- WhatsApp Business setup depends on Meta's business verification, which can take a few days and needs your company's legal/registration details — worth starting early since it's outside our control.
- The AI interview and screening features depend on API access from our screening partner (Reqruit.ai) — this is being arranged and isn't something you need to action.
- A stable, secure web address for QRUIT itself needs to be live before candidates can be sent interview links or WhatsApp messages can be received — this is on our side to set up.
