import type { AuthStatus, ChannelName } from "../types";
import { CHANNEL_META, type ConfigurableChannel } from "../types";

const DISPLAY: Record<ChannelName, { label: string; abbr: string; color: string; category: string }> = {
  gmail: { label: "Gmail", abbr: "G", color: "#EA4335", category: "Google Workspace" },
  outlook: { label: "Outlook / Microsoft 365", abbr: "Ou", color: "#0078D4", category: "Microsoft Graph" },
  gdrive: { label: "Google Drive", abbr: "Gd", color: "#4285F4", category: "Drive API v3" },
  sharepoint: { label: "SharePoint", abbr: "Sp", color: "#0078D4", category: "Microsoft Graph" },
  onedrive: { label: "OneDrive", abbr: "Od", color: "#0078D4", category: "Microsoft Graph" },
  whatsapp: { label: "WhatsApp Business", abbr: "W", color: "#25D366", category: "Meta Cloud API" },
  local_folder: { label: "Local Folder", abbr: "Lf", color: "#8090b8", category: "Server directory" },
  manual_upload: { label: "Manual Upload", abbr: "Mu", color: "#8090b8", category: "Server directory" },
};

const STATUS_LABEL: Record<string, string> = {
  ready: "Connected",
  needs_auth: "Not connected",
  error: "Error",
};

export default function ChannelCard({
  name, status, onConfigure, onDisconnect,
}: {
  name: ChannelName;
  status: AuthStatus;
  onConfigure: (channel: ConfigurableChannel) => void;
  onDisconnect: (channel: ConfigurableChannel) => void;
}) {
  const info = DISPLAY[name];
  const configurable = name in CHANNEL_META;
  // A mock fallback isn't a real connection — show it the same way as
  // "needs_auth" rather than green, so this reads correctly to a real
  // customer who hasn't connected anything yet.
  const displayState = status.mock ? "needs_auth" : status.state;
  const isRealConnection = status.state === "ready" && !status.mock;

  return (
    <div className={`card ${displayState}`}>
      <div className="card-top">
        <div className="svc-icon" style={{ background: info.color }}>
          {info.abbr}
        </div>
        <div className="svc-info">
          <div className="svc-name">{info.label}</div>
          <div className="svc-cat">{info.category}</div>
        </div>
        <span className={`status-pill ${displayState}`}>
          <span className="status-dot" />
          {STATUS_LABEL[displayState] ?? displayState}
        </span>
      </div>

      {isRealConnection && status.account_label && <div className="account-row">{status.account_label}</div>}
      {!status.mock && status.reason && <div className="reason-row">{status.reason}</div>}

      {configurable && (
        <div className="card-actions">
          {isRealConnection ? (
            <>
              <button className="btn btn-ghost" onClick={() => onConfigure(name as ConfigurableChannel)}>
                Reconfigure
              </button>
              <button className="btn btn-ghost" onClick={() => onDisconnect(name as ConfigurableChannel)}>
                Disconnect
              </button>
            </>
          ) : (
            <button className="btn btn-ghost" onClick={() => onConfigure(name as ConfigurableChannel)}>
              Connect
            </button>
          )}
        </div>
      )}
    </div>
  );
}
