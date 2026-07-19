import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2Icon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import { fetchSshConnections, saveSshConnections, testSshConnection } from "@/lib/sshApi";
import {
  createSshConnectionId,
  type SshConnection,
  type SshConnectionStatus,
} from "@/lib/sshConnectionPreferences";

type StatusMap = Record<string, { status: SshConnectionStatus; message?: string; latencyMs?: number }>;

/**
 * SSH connection profiles under Settings → Connection. Profiles are stored in
 * ~/.omnigent/config.yaml on the host; connectivity is probed via POST /v1/ssh/test.
 */
export function ConnectionSettingsBody() {
  const [connections, setConnections] = useState<SshConnection[]>([]);
  const [statusById, setStatusById] = useState<StatusMap>({});
  const [label, setLabel] = useState("");
  const [alias, setAlias] = useState("");
  const [codexRemote, setCodexRemote] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const probedOnMount = useRef(false);

  const persist = useCallback(async (next: SshConnection[]) => {
    const saved = await saveSshConnections(next);
    setConnections(saved);
    return saved;
  }, []);

  const runProbe = useCallback(async (connection: SshConnection) => {
    setStatusById((prev) => ({
      ...prev,
      [connection.id]: { status: "checking" },
    }));
    const result = await testSshConnection(connection.alias);
    setStatusById((prev) => ({
      ...prev,
      [connection.id]: {
        status: result.ok ? "ok" : "failed",
        message: result.message,
        latencyMs: result.latencyMs ?? undefined,
      },
    }));
  }, []);

  const probeAll = useCallback(
    async (list: SshConnection[]) => {
      await Promise.all(list.map((c) => runProbe(c)));
    },
    [runProbe],
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchSshConnections();
        if (!cancelled) {
          setConnections(loaded);
          setLoadError(null);
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : "Failed to load connections");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (loading || probedOnMount.current) return;
    probedOnMount.current = true;
    if (connections.length > 0) {
      void probeAll(connections);
    }
  }, [connections, loading, probeAll]);

  const handleAdd = useCallback(async () => {
    setFormError(null);
    const trimmedLabel = label.trim();
    const trimmedAlias = alias.trim();
    if (!trimmedLabel) {
      setFormError("Label is required");
      return;
    }
    if (!trimmedAlias) {
      setFormError("SSH alias is required");
      return;
    }

    const connection: SshConnection = {
      id: createSshConnectionId(),
      label: trimmedLabel,
      alias: trimmedAlias,
      codexRemote,
      createdAt: new Date().toISOString(),
    };

    setSaving(true);
    try {
      const next = [...connections, connection];
      await persist(next);
      setLabel("");
      setAlias("");
      setCodexRemote(true);
      await runProbe(connection);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Failed to save connection");
    } finally {
      setSaving(false);
    }
  }, [alias, codexRemote, connections, label, persist, runProbe]);

  const handleRemove = useCallback(
    async (id: string) => {
      const next = connections.filter((c) => c.id !== id);
      try {
        await persist(next);
        setStatusById((prev) => {
          const copy = { ...prev };
          delete copy[id];
          return copy;
        });
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "Failed to remove connection");
      }
    },
    [connections, persist],
  );

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading connections…</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {loadError && (
        <p className="text-sm text-destructive" data-testid="ssh-connections-load-error">
          {loadError}
        </p>
      )}

      <div className="flex flex-col gap-4">
        {connections.length === 0 ? (
          <p className="text-sm text-muted-foreground" data-testid="ssh-connections-empty">
            No SSH connections yet. Add a Host alias from your SSH config (e.g. arca.ssh).
          </p>
        ) : (
          <ul className="flex flex-col gap-2" data-testid="ssh-connections-list">
            {connections.map((connection) => (
              <SshConnectionRow
                key={connection.id}
                connection={connection}
                status={statusById[connection.id]}
                onRetest={() => void runProbe(connection)}
                onRemove={() => void handleRemove(connection.id)}
              />
            ))}
          </ul>
        )}

        {connections.length > 0 && (
          <div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              data-testid="ssh-connections-retest-all"
              onClick={() => void probeAll(connections)}
            >
              <RefreshCwIcon className="mr-1.5 h-3.5 w-3.5" />
              Test all connections
            </Button>
          </div>
        )}
      </div>

      <div
        className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4"
        data-testid="ssh-connection-form"
      >
        <div>
          <h3 className="text-sm font-medium">Add connection</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Enter a Host alias from <code className="text-xs">~/.ssh/config</code>. The alias is
            tested automatically after you save. When enabled, remote Codex sessions on that host
            are imported into Omnigent.
          </p>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Label</span>
            <Input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Arca devbox"
              data-testid="ssh-connection-label"
              spellCheck={false}
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">SSH alias</span>
            <Input
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. arca.ssh"
              data-testid="ssh-connection-alias"
              spellCheck={false}
              autoCapitalize="off"
            />
          </label>
          <label className="col-span-full flex items-center justify-between gap-4 text-sm">
            <div className="flex flex-col">
              <span className="font-medium">Import remote Codex sessions</span>
              <span className="text-muted-foreground">
                Mirror Codex rollouts from this host&apos;s ~/.codex
              </span>
            </div>
            <Switch
              checked={codexRemote}
              onCheckedChange={setCodexRemote}
              data-testid="ssh-connection-codex-remote"
            />
          </label>
        </div>

        {formError && (
          <p className="text-sm text-destructive" data-testid="ssh-connection-form-error">
            {formError}
          </p>
        )}

        <div>
          <Button
            type="button"
            size="sm"
            data-testid="ssh-connection-add"
            disabled={saving}
            onClick={() => void handleAdd()}
          >
            {saving ? (
              <Loader2Icon className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <PlusIcon className="mr-1.5 h-3.5 w-3.5" />
            )}
            Add and test
          </Button>
        </div>
      </div>
    </div>
  );
}

function SshConnectionRow({
  connection,
  status,
  onRetest,
  onRemove,
}: {
  connection: SshConnection;
  status?: StatusMap[string];
  onRetest: () => void;
  onRemove: () => void;
}) {
  const current = status?.status ?? "unknown";
  return (
    <li
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
      data-testid={`ssh-connection-row-${connection.id}`}
    >
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        <SshStatusIcon status={current} />
        <div className="min-w-0">
          <div className="text-sm font-medium">{connection.label}</div>
          <div className="truncate text-xs text-muted-foreground">
            {connection.alias}
            {connection.codexRemote ? " · Codex import on" : " · Codex import off"}
          </div>
          {status?.message && current === "failed" && (
            <div className="mt-0.5 text-xs text-destructive">{status.message}</div>
          )}
          {current === "ok" && status?.latencyMs != null && (
            <div className="mt-0.5 text-xs text-muted-foreground">{status.latencyMs} ms</div>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          aria-label={`Test ${connection.label}`}
          data-testid={`ssh-connection-retest-${connection.id}`}
          onClick={onRetest}
          disabled={current === "checking"}
        >
          {current === "checking" ? (
            <Loader2Icon className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCwIcon className="h-4 w-4" />
          )}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-muted-foreground hover:text-destructive"
          aria-label={`Remove ${connection.label}`}
          data-testid={`ssh-connection-remove-${connection.id}`}
          onClick={onRemove}
        >
          <Trash2Icon className="h-4 w-4" />
        </Button>
      </div>
    </li>
  );
}

function SshStatusIcon({ status }: { status: SshConnectionStatus }) {
  if (status === "checking") {
    return <Loader2Icon className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />;
  }
  if (status === "ok") {
    return <CheckCircle2Icon className="mt-0.5 h-4 w-4 shrink-0 text-green-500" />;
  }
  if (status === "failed") {
    return <XCircleIcon className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />;
  }
  return (
    <span
      className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-muted-foreground/40")}
      aria-hidden
    />
  );
}
