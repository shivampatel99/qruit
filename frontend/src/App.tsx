import { useState } from "react";
import ChannelsPage from "./components/ChannelsPage";
import SetupGuidePage from "./components/SetupGuidePage";

type Tab = "channels" | "guide";

export default function App() {
  const [tab, setTab] = useState<Tab>("channels");

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <div className="app-title">QRUIT</div>
          <div className="app-sub">
            {tab === "channels"
              ? "Channels — connect the sources QRUIT pulls JDs, CVs, and commands from"
              : "Setup Guide — where to find every key QRUIT asks for"}
          </div>
        </div>
      </header>

      <div className="tabs">
        <button className={`tab ${tab === "channels" ? "active" : ""}`} onClick={() => setTab("channels")}>
          Channels
        </button>
        <button className={`tab ${tab === "guide" ? "active" : ""}`} onClick={() => setTab("guide")}>
          Setup Guide
        </button>
      </div>

      {tab === "channels" ? <ChannelsPage /> : <SetupGuidePage />}
    </div>
  );
}
