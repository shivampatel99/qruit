import { useState } from "react";

interface KeyDoc {
  /** The exact label shown on the "Add Channel" form for this field. */
  fieldLabel: string;
  /** Plain-English note on whether it's required and what it's for. */
  note: string;
}

interface ProviderGuide {
  id: string;
  label: string;
  abbr: string;
  color: string;
  /** One-line summary of what connecting this actually does. */
  summary: string;
  keys: KeyDoc[];
  steps: string[];
  tip?: string;
}

// Every step here assumes QRUIT is reachable at http://localhost:8787 (the
// default in backend/.env.example). If it's deployed somewhere else, swap
// that address for the real QRUIT_PUBLIC_BASE_URL everywhere it appears.
const QRUIT_URL = "http://localhost:8787";

const GUIDES: ProviderGuide[] = [
  {
    id: "gmail",
    label: "Gmail",
    abbr: "G",
    color: "#EA4335",
    summary: "Lets QRUIT read incoming job descriptions/CVs from a Gmail inbox and send replies from it.",
    keys: [
      { fieldLabel: "Google Client ID", note: "Required. Identifies the app to Google." },
      { fieldLabel: "Google Client Secret", note: "Required. Proves the app is who it says it is — keep this private." },
    ],
    steps: [
      "Open console.cloud.google.com and sign in with the Google account that owns (or manages) the Gmail inbox you want to connect.",
      "Top-left, click the project dropdown next to the Google Cloud logo → \"New Project\" → give it any name (e.g. \"QRUIT\") → Create. Wait a moment for the page to switch into that new project.",
      "Open the ☰ menu on the left → \"APIs & Services\" → \"OAuth consent screen\". Choose \"External\" → Create. Fill in an App name, your email as \"User support email\" and \"Developer contact\" → Save and Continue through the remaining steps (defaults are fine).",
      "☰ menu → \"APIs & Services\" → \"Library\". Type \"Gmail API\" in the search box → click it → \"Enable\".",
      "☰ menu → \"APIs & Services\" → \"Credentials\" → \"+ Create Credentials\" (top of page) → \"OAuth client ID\".",
      "For \"Application type\" pick \"Web application\". Give it any name.",
      `Under \"Authorized redirect URIs\", click \"+ Add URI\" and paste exactly: ${QRUIT_URL}/api/oauth/callback`,
      "Click \"Create\". A window pops up showing \"Client ID\" and \"Client secret\" — copy both somewhere safe.",
      "In QRUIT: Channels → the Gmail card → Connect → paste the Client ID into \"Google Client ID\" and the Client secret into \"Google Client Secret\" → Save. A Google sign-in tab opens — sign in and click Allow.",
    ],
    tip: "While the app is still in \"Testing\" mode on the OAuth consent screen, only accounts you've explicitly added under \"Test users\" can sign in. Add the Gmail account there, or click \"Publish App\" once you're ready for anyone to be able to connect.",
  },
  {
    id: "gdrive",
    label: "Google Drive",
    abbr: "Gd",
    color: "#4285F4",
    summary: "Lets QRUIT watch a Google Drive folder for new job description / CV files.",
    keys: [
      { fieldLabel: "Drive Folder ID", note: "Required. Tells QRUIT which folder to watch." },
    ],
    steps: [
      "Connect Gmail first (above) — Drive reuses that same Google sign-in, there's no separate login for it.",
      "In the same Google Cloud project used for Gmail: ☰ menu → \"APIs & Services\" → \"Library\" → search \"Google Drive API\" → Enable. (Skip this and Drive will stay stuck showing an error.)",
      "Open drive.google.com, signed in as the same account, and open (or create) the folder QRUIT should watch.",
      "Look at the address bar — it'll look like drive.google.com/drive/folders/1AbCDefGhIjkLmnOPQrstUvWxYZ. Copy everything after the last \"/\" — that string is the Folder ID.",
      "In QRUIT: Channels → the Google Drive card → Connect → paste that string into \"Drive Folder ID\" → Save.",
    ],
  },
  {
    id: "outlook",
    label: "Outlook / Microsoft 365",
    abbr: "Ou",
    color: "#0078D4",
    summary: "Lets QRUIT read incoming JDs/CVs from an Outlook mailbox and send replies from it.",
    keys: [
      { fieldLabel: "Microsoft Client ID", note: "Required. Identifies the app to Microsoft." },
      { fieldLabel: "Microsoft Client Secret", note: "Required. Keep this private." },
      { fieldLabel: "Directory (Tenant) ID", note: "Required. Identifies which organization's Microsoft 365 this app is registered under." },
    ],
    steps: [
      "Go to portal.azure.com and sign in with a Microsoft 365 admin (or work) account for the organization the mailbox belongs to.",
      "Use the search bar at the top → type \"App registrations\" → open it → \"+ New registration\".",
      `Give it a name (e.g. \"QRUIT\") → under \"Supported account types\" choose \"Accounts in this organizational directory only\" → under \"Redirect URI\" pick \"Web\" and paste: ${QRUIT_URL}/api/oauth/callback → Register.`,
      "On the app's Overview page, copy \"Application (client) ID\" (this is the Client ID) and \"Directory (tenant) ID\" (this is the Tenant ID).",
      "Left menu → \"Certificates & secrets\" → \"+ New client secret\" → add a description and pick an expiry → Add. Immediately copy the value shown under \"Value\" (not \"Secret ID\") — it's only ever shown this once.",
      "Left menu → \"API permissions\" → \"+ Add a permission\" → \"Microsoft Graph\" → \"Delegated permissions\" → search and add: Mail.Read, Mail.Send, Files.Read.All, Sites.Read.All, offline_access, User.Read.",
      "Click \"Grant admin consent for [your organization]\" above the permissions list. If that button is greyed out, you're not an admin — ask one to click it for you.",
      "In QRUIT: Channels → the Outlook card → Connect → paste Client ID, Client Secret, and Tenant ID → Save. A Microsoft sign-in tab opens — sign in and click Accept.",
    ],
    tip: "SharePoint and OneDrive don't need their own app registration — they reuse whatever you set up here. Connect Outlook first, then SharePoint/OneDrive below.",
  },
  {
    id: "sharepoint",
    label: "SharePoint / OneDrive",
    abbr: "Sp",
    color: "#0078D4",
    summary: "Lets QRUIT watch a OneDrive or SharePoint site folder for new JD/CV files.",
    keys: [
      { fieldLabel: "Site ID (blank = OneDrive)", note: "Optional. Leave blank to watch your own OneDrive's \"My files\". Fill in to watch a SharePoint team site's document library instead." },
      { fieldLabel: "Folder Path", note: "Optional. The folder inside that OneDrive/site to watch, e.g. \"Active Roles\". Leave blank to watch everything." },
    ],
    steps: [
      "Connect Outlook first (above) — this one reuses that same sign-in, there's no separate login.",
      "If you want QRUIT to watch your own OneDrive \"My files\": leave \"Site ID\" blank and skip to the last step.",
      "If instead you want it to watch a specific SharePoint team site's document library: open developer.microsoft.com/graph/graph-explorer, sign in with the same Microsoft account, and run a GET request to: https://graph.microsoft.com/v1.0/sites/{yourtenant}.sharepoint.com:/sites/{site-name}",
      "In the response, copy the value of the \"id\" field — that's your Site ID.",
      "Decide the Folder Path: the folder name inside that OneDrive/site you want watched, e.g. \"Active Roles\" or \"Active Roles/CVs\". Leave blank to watch from the top.",
      "In QRUIT: Channels → the SharePoint / OneDrive card → Connect → paste the Site ID (or leave blank) and the Folder Path → Save. It connects immediately — no extra sign-in tab.",
    ],
  },
  {
    id: "whatsapp",
    label: "WhatsApp Business",
    abbr: "W",
    color: "#25D366",
    summary: "Lets QRUIT receive JDs/CVs and send candidate updates over WhatsApp.",
    keys: [
      { fieldLabel: "Permanent Access Token", note: "Required. Lets QRUIT send messages on your business number's behalf." },
      { fieldLabel: "Phone Number ID", note: "Required. Identifies which WhatsApp Business number to use." },
      { fieldLabel: "App Secret", note: "Optional but recommended. Used to verify incoming messages are really from Meta." },
      { fieldLabel: "Webhook Verify Token", note: "Optional. Any string you make up — entered here and in Meta's webhook setup, to confirm the two sides match." },
    ],
    steps: [
      "Go to developers.facebook.com/apps → create a new app (or open an existing one) → add the \"WhatsApp\" product to it.",
      "Left menu → WhatsApp → \"API Setup\". Under \"From\", you'll see a phone number — copy its \"Phone number ID\" shown just below it.",
      "For real (non-test) use, get a permanent token: go to business.facebook.com → Settings → \"System Users\" → create or open a system user → \"Generate new token\" → pick your WhatsApp app → check \"whatsapp_business_messaging\" and \"whatsapp_business_management\" → Generate → copy the token (shown once).",
      "App Secret: back in the Meta app dashboard → Settings → Basic → \"App Secret\" → click \"Show\" (it may ask for your Facebook password) → copy it.",
      "Webhook Verify Token: make up any string yourself, e.g. a random word or password — you'll use this exact string in both the next two steps.",
      "In QRUIT: Channels → the WhatsApp Business card → Connect → paste Token, Phone Number ID, App Secret, and your made-up Verify Token → Save. It connects immediately.",
      `Back in the Meta app dashboard: WhatsApp → Configuration → Webhook → Edit → Callback URL = ${QRUIT_URL}/webhooks/whatsapp, Verify token = the exact same string you made up above → Verify and Save → then subscribe to the \"messages\" field.`,
    ],
  },
];

export default function SetupGuidePage() {
  const [open, setOpen] = useState<string | null>(GUIDES[0].id);

  return (
    <div>
      <div className="info-banner" style={{ marginBottom: 20 }}>
        Step-by-step instructions for getting each key QRUIT asks for. Open a channel below,
        follow the numbered steps in order, then paste what you copied into the matching field
        on that channel's "Connect" form.
      </div>

      {GUIDES.map((g) => {
        const expanded = open === g.id;
        return (
          <div className="guide-item" key={g.id}>
            <button
              className="guide-header"
              onClick={() => setOpen(expanded ? null : g.id)}
              aria-expanded={expanded}
            >
              <div className="svc-icon" style={{ background: g.color, width: 32, height: 32, fontSize: 12 }}>
                {g.abbr}
              </div>
              <div className="guide-header-text">
                <div className="svc-name">{g.label}</div>
                <div className="svc-cat">{g.summary}</div>
              </div>
              <span className="guide-chevron">{expanded ? "−" : "+"}</span>
            </button>

            {expanded && (
              <div className="guide-body">
                <div className="guide-keys">
                  {g.keys.map((k) => (
                    <div className="guide-key-row" key={k.fieldLabel}>
                      <span className="guide-key-tag">{k.fieldLabel}</span>
                      <span className="guide-key-note">{k.note}</span>
                    </div>
                  ))}
                </div>

                <ol className="guide-steps">
                  {g.steps.map((step, i) => (
                    <li key={i}>{step}</li>
                  ))}
                </ol>

                {g.tip && <div className="guide-tip">{g.tip}</div>}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
