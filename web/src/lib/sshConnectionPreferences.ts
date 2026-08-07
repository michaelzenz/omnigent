// SSH connection profiles stored in ~/.omnigent/config.yaml on the host machine.

export type SshConnectionStatus = "unknown" | "checking" | "ok" | "failed";

export type SshHostPhase =
  | "queued"
  | "waiting_for_ssh"
  | "installing"
  | "opening_tunnel"
  | "starting_host"
  | "waiting_for_host"
  | "ready"
  | "backoff"
  | "detaching"
  | "detached"
  | (string & {});

export interface SshConnection {
  id: string;
  label: string;
  /** SSH config Host alias, e.g. arca.ssh */
  alias: string;
  createdAt: string;
  hostId: string | null;
  lifecycle: string;
  phase: SshHostPhase;
  lastError: string | null;
  attempt: number;
  nextRetryAt: string | null;
  updatedAt: string;
  status: "online" | "offline";
}

export function createSshConnectionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `ssh_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}
