// SSH connection profiles stored in ~/.omnigent/config.yaml on the host machine.

export type SshConnectionStatus = "unknown" | "checking" | "ok" | "failed";

export interface SshConnection {
  id: string;
  label: string;
  /** SSH config Host alias, e.g. arca.ssh */
  alias: string;
  codexRemote: boolean;
  createdAt: string;
}

export function createSshConnectionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `ssh_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}
