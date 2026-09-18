// Mirrors app/models.py::AuthState / AuthStatus.
export type AuthState = "ready" | "needs_auth" | "error";

export interface AuthStatus {
  state: AuthState;
  reason: string;
  account_label: string;
  // True when `state` is "ready" only because no real credentials exist
  // yet and the backend fell back to safe mock data — not a real
  // connection. Treated as "not connected" in this UI.
  mock: boolean;
}

export type ChannelName =
  | "gmail"
  | "outlook"
  | "gdrive"
  | "sharepoint"
  | "onedrive"
  | "whatsapp"
  | "local_folder"
  | "manual_upload";

export type ChannelsResponse = Record<ChannelName, AuthStatus>;

// The channels a person can actually configure through the API — matches
// app/api/channels.py's _REQUEST_TO_SETTINGS. onedrive/local_folder/
// manual_upload aren't here: onedrive piggybacks on outlook's credentials,
// the other two need no credentials at all.
export type ConfigurableChannel = "gmail" | "outlook" | "gdrive" | "sharepoint" | "whatsapp";

export interface FieldSpec {
  key: string;
  label: string;
  required: boolean;
  secret?: boolean;
}

export interface ChannelMeta {
  label: string;
  category: "Email" | "Storage" | "Messaging";
  color: string;
  fields: FieldSpec[];
  /** True if a real Google/Microsoft consent tab needs opening after submit. */
  needsAuthorize: boolean;
}

// Mirrors app/api/channels.py::_REQUEST_TO_SETTINGS + REQUIRED_FIELDS in
// app/services/connector_config.py — kept here so the Add Channel form only
// ever shows the fields that channel actually reads.
export const CHANNEL_META: Record<ConfigurableChannel, ChannelMeta> = {
  gmail: {
    label: "Gmail", category: "Email", color: "#EA4335", needsAuthorize: true,
    fields: [
      { key: "client_id", label: "Google Client ID", required: true },
      { key: "client_secret", label: "Google Client Secret", required: true, secret: true },
    ],
  },
  outlook: {
    label: "Outlook / Microsoft 365", category: "Email", color: "#0078D4", needsAuthorize: true,
    fields: [
      { key: "client_id", label: "Microsoft Client ID", required: true },
      { key: "client_secret", label: "Microsoft Client Secret", required: true, secret: true },
      { key: "tenant", label: "Directory (Tenant) ID", required: true },
    ],
  },
  gdrive: {
    label: "Google Drive", category: "Storage", color: "#4285F4", needsAuthorize: false,
    fields: [{ key: "folder_id", label: "Drive Folder ID", required: true }],
  },
  sharepoint: {
    label: "SharePoint / OneDrive", category: "Storage", color: "#0078D4", needsAuthorize: false,
    fields: [
      { key: "site_id", label: "Site ID (blank = OneDrive)", required: false },
      { key: "folder_path", label: "Folder Path", required: false },
    ],
  },
  whatsapp: {
    label: "WhatsApp Business", category: "Messaging", color: "#25D366", needsAuthorize: false,
    fields: [
      { key: "token", label: "Permanent Access Token", required: true, secret: true },
      { key: "phone_id", label: "Phone Number ID", required: true },
      { key: "app_secret", label: "App Secret", required: false, secret: true },
      { key: "verify_token", label: "Webhook Verify Token", required: false },
    ],
  },
};
