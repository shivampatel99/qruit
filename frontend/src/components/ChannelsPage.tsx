import { useCallback, useEffect, useState } from "react";
import { disconnectChannel, getChannels } from "../api";
import type { ChannelName, ChannelsResponse } from "../types";
import type { ConfigurableChannel } from "../types";
import ChannelCard from "./ChannelCard";
import AddChannelModal from "./AddChannelModal";

const SECTIONS: { title: string; sub: string; channels: ChannelName[] }[] = [
  {
    title: "Email",
    sub: "QRUIT reads these inboxes for incoming JDs, CV attachments, and text commands",
    channels: ["gmail", "outlook"],
  },
  {
    title: "Cloud & Local Storage",
    sub: "QRUIT watches these folders for new JD and CV files",
    channels: ["gdrive", "sharepoint", "onedrive"],
  },
  {
    title: "Messaging",
    sub: "Receive JDs/CVs and send candidate updates over WhatsApp",
    channels: ["whatsapp"],
  },
];

export default function ChannelsPage() {
  const [channels, setChannels] = useState<ChannelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [modalChannel, setModalChannel] = useState<ConfigurableChannel | null | "picker">(null);

  const load = useCallback(async () => {
    try {
      const data = await getChannels();
      setChannels(data);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reach the QRUIT backend");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function handleDisconnect(channel: ConfigurableChannel) {
    try {
      await disconnectChannel(channel);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not disconnect that channel");
    }
  }

  const shownChannels = new Set(SECTIONS.flatMap((s) => s.channels));
  const counts = channels
    ? Object.entries(channels).reduce(
        (acc, [name, s]) => {
          if (!shownChannels.has(name as ChannelName)) return acc;
          acc.total += 1;
          // A mock fallback isn't a real connection — count it as
          // "not connected," matching how ChannelCard displays it.
          if (s.state === "ready" && !s.mock) acc.ready += 1;
          else if (s.state === "error") acc.error += 1;
          else acc.needsAuth += 1;
          return acc;
        },
        { ready: 0, needsAuth: 0, error: 0, total: 0 },
      )
    : null;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 16 }}>
        <button className="btn btn-ghost" onClick={load}>
          ↻ Refresh
        </button>
        <button className="btn btn-primary" onClick={() => setModalChannel("picker")}>
          + Add Channel
        </button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {counts && (
        <div className="health">
          <div className="health-stat">
            <div className="health-val green">{counts.ready}</div>
            <div className="health-key">Connected</div>
          </div>
          <div className="health-stat">
            <div className="health-val amber">{counts.needsAuth}</div>
            <div className="health-key">Not Connected</div>
          </div>
          <div className="health-stat">
            <div className="health-val red">{counts.error}</div>
            <div className="health-key">Errors</div>
          </div>
          <div className="health-stat">
            <div className="health-val">{counts.total}</div>
            <div className="health-key">Total Channels</div>
          </div>
        </div>
      )}

      {loading && <div className="empty-state">Loading channels…</div>}

      {channels &&
        SECTIONS.map((section) => {
          const present = section.channels.filter((c) => c in channels);
          if (present.length === 0) return null;
          return (
            <div className="section" key={section.title}>
              <div className="section-title">{section.title}</div>
              <div className="section-sub">{section.sub}</div>
              <div className="grid">
                {present.map((name) => (
                  <ChannelCard
                    key={name}
                    name={name}
                    status={channels[name]}
                    onConfigure={(c) => setModalChannel(c)}
                    onDisconnect={handleDisconnect}
                  />
                ))}
              </div>
            </div>
          );
        })}

      {modalChannel && (
        <AddChannelModal
          initialChannel={modalChannel === "picker" ? null : modalChannel}
          onClose={() => setModalChannel(null)}
          onConnected={load}
        />
      )}
    </div>
  );
}
