import { authenticatedFetch } from "@/lib/identity";
import type { SshConnection } from "@/lib/sshConnectionPreferences";

interface ApiConnection {
  id: string;
  label: string;
  alias: string;
  created_at: string;
  codex_remote?: boolean;
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
    codexRemote: entry.codex_remote ?? true,
    createdAt: entry.created_at,
  };
}

function toApiConnection(connection: SshConnection): ApiConnection {
  return {
    id: connection.id,
    label: connection.label,
    alias: connection.alias,
    created_at: connection.createdAt,
    codex_remote: connection.codexRemote,
  };
}

export async function fetchSshConnections(): Promise<SshConnection[]> {
  const res = await authenticatedFetch("/v1/ssh/connections");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const body = (await res.json()) as { connections?: ApiConnection[] };
  return (body.connections ?? []).map(fromApiConnection);
}

export async function saveSshConnections(connections: SshConnection[]): Promise<SshConnection[]> {
  const res = await authenticatedFetch("/v1/ssh/connections", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      connections: connections.map(toApiConnection),
    }),
  });
  if (!res.ok) {
    const body = (await res.json()) as { detail?: string };
    throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  const saved = (await res.json()) as { connections?: ApiConnection[] };
  return (saved.connections ?? []).map(fromApiConnection);
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
