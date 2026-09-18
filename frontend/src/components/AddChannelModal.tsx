import { useEffect, useRef, useState } from "react";
import { getAuthorizeUrl, getChannelStatus, submitCredentials } from "../api";
import type { AuthStatus, ConfigurableChannel } from "../types";
import { CHANNEL_META } from "../types";

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 2 * 60 * 1000;

export default function AddChannelModal({
  initialChannel, onClose, onConnected,
}: {
  initialChannel: ConfigurableChannel | null;
  onClose: () => void;
  onConnected: () => void;
}) {
  const [channel, setChannel] = useState<ConfigurableChannel | null>(initialChannel);
  const [values, setValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<AuthStatus | null>(null);
  const [polling, setPolling] = useState(false);
  const pollTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (pollTimer.current) window.clearInterval(pollTimer.current);
  }, []);

  function pick(c: ConfigurableChannel) {
    setChannel(c);
    setValues({});
    setError("");
    setResult(null);
  }

  async function handleSubmit() {
    if (!channel) return;
    setSubmitting(true);
    setError("");
    try {
      const status = await submitCredentials(channel, values);
      setResult(status);
      if (status.state === "ready") {
        onConnected();
      } else if (CHANNEL_META[channel].needsAuthorize) {
        await startAuthorize(channel);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  async function startAuthorize(c: ConfigurableChannel) {
    try {
      const { authorize_url } = await getAuthorizeUrl(c);
      window.open(authorize_url, "_blank", "noopener,noreferrer");
      pollForConnection(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not build the authorize link");
    }
  }

  function pollForConnection(c: ConfigurableChannel) {
    setPolling(true);
    const startedAt = Date.now();
    pollTimer.current = window.setInterval(async () => {
      if (Date.now() - startedAt > POLL_TIMEOUT_MS) {
        window.clearInterval(pollTimer.current!);
        setPolling(false);
        return;
      }
      const status = await getChannelStatus(c).catch(() => null);
      if (status?.state === "ready") {
        window.clearInterval(pollTimer.current!);
        setPolling(false);
        setResult(status);
        onConnected();
      }
    }, POLL_INTERVAL_MS);
  }

  const meta = channel ? CHANNEL_META[channel] : null;

  return (
    <div className="overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="modal-header">
          <div>
            <div className="modal-title">{meta ? `Connect ${meta.label}` : "Add Channel"}</div>
            <div className="modal-sub">
              {meta ? "Enter the credentials below — nothing is sent anywhere else." : "Choose a channel to connect"}
            </div>
          </div>
          <button className="x-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          {!channel && (
            <div className="type-grid">
              {(Object.keys(CHANNEL_META) as ConfigurableChannel[]).map((key) => (
                <button key={key} className="type-tile" onClick={() => pick(key)}>
                  <div className="svc-icon" style={{ background: CHANNEL_META[key].color }}>
                    {key.slice(0, 2).toUpperCase()}
                  </div>
                  <div className="type-name">{CHANNEL_META[key].label}</div>
                </button>
              ))}
            </div>
          )}

          {channel && meta && !result?.state && !polling && (
            <>
              <button className="back-link" onClick={() => setChannel(null)}>
                ← Back
              </button>
              {error && <div className="error-banner">{error}</div>}
              {meta.fields.map((f) => (
                <div className="field" key={f.key}>
                  <label>
                    {f.label}
                    {!f.required && " (optional)"}
                  </label>
                  <input
                    type={f.secret ? "password" : "text"}
                    value={values[f.key] ?? ""}
                    onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                  />
                </div>
              ))}
            </>
          )}

          {polling && (
            <div className="info-banner" style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span className="spinner" />
              Waiting for you to finish allowing access in the other tab — this updates automatically.
            </div>
          )}

          {result?.state === "ready" && (
            <div className="info-banner" style={{ borderColor: "rgba(0,212,138,.3)", background: "var(--green-dim)", color: "var(--green)" }}>
              Connected{result.account_label ? ` — ${result.account_label}` : ""}.
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn btn-ghost" onClick={onClose}>
            {result?.state === "ready" ? "Close" : "Cancel"}
          </button>
          {channel && !result?.state && !polling && (
            <button className="btn btn-primary" disabled={submitting} onClick={handleSubmit}>
              {submitting ? <span className="spinner" /> : "Connect"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
