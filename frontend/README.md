# QRUIT frontend

React (Vite + TypeScript) app for the Channels dashboard — connect and
monitor Gmail, Outlook, Google Drive, SharePoint/OneDrive, and WhatsApp
against the real backend APIs in `../backend`.

## Setup

```bash
npm install
cp .env.example .env
npm run dev
```

Loads at `http://localhost:5173`. Needs the backend running separately
(`cd ../backend && uvicorn app.main:app --port 8787`).

**`.env` must point at `127.0.0.1`, not `localhost`** — the backend
deliberately rejects `/api/*` requests arriving via whatever hostname
`QRUIT_PUBLIC_BASE_URL` names (`localhost` by default), reserving `/api/*`
for internal access only. `.env.example` already has this right; don't
"simplify" it to `localhost` later.

## What's here

- `src/components/ChannelsPage.tsx` — fetches `GET /api/channels` and
  renders everything from that response; nothing about which connectors
  exist or their status is hardcoded.
- `src/components/AddChannelModal.tsx` — pick a channel → its real
  required fields (mirrors `app/services/connector_config.py` on the
  backend) → `POST /api/channels/{channel}/credentials`. For Gmail/Outlook
  it then opens the OAuth consent screen in a new tab and polls until the
  card flips to Connected — no manual refresh needed.
- `src/types.ts` — the per-channel field list (`CHANNEL_META`). If a
  channel's required fields change on the backend, update this to match.

## Scope note

The original design mockup (`QRUIT_Channels_Tab-1.html` at the repo root)
shows a 6-step wizard with a live-preview "test" step, sourcing APIs
(LinkedIn/Indeed/etc.), ATS sync, and per-channel folder management —
none of that has a real backend endpoint yet. This app only implements
what the API can actually do: submit credentials, authorize if it's OAuth,
see live status. Extending it further means building the backend endpoint
first, not just the UI for it.
