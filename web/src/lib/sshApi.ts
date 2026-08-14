import { authenticatedFetch } from "@/lib/identity";
import type { SshConnection } from "@/lib/sshConnectionPreferences";

interface ApiConnection {
  id: string;
  label: string;
  alias: string;
  created_at: string;
  host_id?: string | null;
  lifecycle?: string;
  phase?: string;
  last_error?: string | null;
  attempt?: number;
  next_retry_at?: string | null;
  updated_at?: string;
  status?: "online" | "offline";
}

export interface SshConnectionsPayload {
  connections: SshConnection[];
  packageIndexUrl: string | null;
}

export interface SshTestResult {
  ok: boolean;
  message: string;
  latencyMs: number | null;
}

function fromApiConnection(entry: ApiConnection): SshConnection {
  return {
    id: entry.id,
    label: entry.label,
    alias: entry.alias,
    createdAt: entry.created_at,
    hostId: entry.host_id ?? null,
    lifecycle: entry.lifecycle ?? "connected",
    phase: entry.phase ?? "queued",
    lastError: entry.last_error ?? null,
    attempt: entry.attempt ?? 0,
    nextRetryAt: entry.next_retry_at ?? null,
    updatedAt: entry.updated_at ?? entry.created_at,
    status: entry.status ?? "offline",
  };
}

function toApiConnection(connection: SshConnection): ApiConnection {
  return {
    id: connection.id,
    label: connection.label,
    alias: connection.alias,
    created_at: connection.createdAt,
  };
}

function fromApiPayload(body: {
  connections?: ApiConnection[];
  package_index_url?: string | null;
}): SshConnectionsPayload {
  return {
    connections: (body.connections ?? []).map(fromApiConnection),
    packageIndexUrl: typeof body.package_index_url === "string" ? body.package_index_url : null,
  };
}

export async function fetchSshConnections(): Promise<SshConnectionsPayload> {
  const res = await authenticatedFetch("/v1/ssh/connections");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const body = (await res.json()) as {
    connections?: ApiConnection[];
    package_index_url?: string | null;
  };
  return fromApiPayload(body);
}

export async function saveSshConnections(
  connections: SshConnection[],
  packageIndexUrl: string | null,
): Promise<SshConnectionsPayload> {
  const res = await authenticatedFetch("/v1/ssh/connections", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      connections: connections.map(toApiConnection),
      package_index_url: packageIndexUrl,
    }),
  });
  if (!res.ok) {
    const body = (await res.json()) as { detail?: string };
    throw new Error(
      typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`,
    );
  }
  const saved = (await res.json()) as {
    connections?: ApiConnection[];
    package_index_url?: string | null;
  };
  return fromApiPayload(saved);
}

export async function retrySshConnection(id: string): Promise<void> {
  const res = await authenticatedFetch(`/v1/ssh/connections/${encodeURIComponent(id)}/retry`, {
    method: "POST",
  });
  if (!res.ok) {
    const body = (await res.json()) as { detail?: string };
    throw new Error(
      typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`,
    );
  }
}

export async function testSshConnection(alias: string): Promise<SshTestResult> {
  const res = await authenticatedFetch("/v1/ssh/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alias }),
  });
  const body = (await res.json()) as {
    ok?: boolean;
    message?: string;
    latency_ms?: number | null;
    detail?: string;
  };
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : typeof body.message === "string"
          ? body.message
          : `${res.status} ${res.statusText}`;
    return { ok: false, message: detail, latencyMs: null };
  }
  return {
    ok: Boolean(body.ok),
    message: typeof body.message === "string" ? body.message : "Unknown result",
    latencyMs: typeof body.latency_ms === "number" ? body.latency_ms : null,
  };
}
