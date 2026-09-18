import type { AuthStatus, ChannelsResponse, ConfigurableChannel } from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8787";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

export function getChannels(): Promise<ChannelsResponse> {
  return request<ChannelsResponse>("/api/channels");
}

export function getChannelStatus(channel: string): Promise<AuthStatus> {
  return request<AuthStatus>(`/api/channels/${channel}/status`);
}

export function submitCredentials(
  channel: ConfigurableChannel, body: Record<string, string>,
): Promise<AuthStatus> {
  return request<AuthStatus>(`/api/channels/${channel}/credentials`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getAuthorizeUrl(channel: string): Promise<{ authorize_url: string }> {
  return request<{ authorize_url: string }>(`/api/oauth/${channel}/connect`);
}

export function disconnectChannel(channel: ConfigurableChannel): Promise<AuthStatus> {
  return request<AuthStatus>(`/api/channels/${channel}/disconnect`, { method: "POST" });
}
