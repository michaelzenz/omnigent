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
import { cn } from "@/lib/utils";
import {
  fetchSshConnections,
  retrySshConnection,
  saveSshConnections,
  testSshConnection,
} from "@/lib/sshApi";
import {
  createSshConnectionId,
  type SshConnection,
  type SshConnectionStatus,
} from "@/lib/sshConnectionPreferences";

type StatusMap = Record<
  string,
  { status: SshConnectionStatus; message?: string; latencyMs?: number }
>;

const ACTIVE_POLL_MS = 2_000;
const STABLE_POLL_MS = 20_000;

const PHASE_LABELS: Record<string, string> = {
  queued: "Queued",
  waiting_for_ssh: "Waiting for SSH",
  installing: "Installing remote host",
  opening_tunnel: "Opening tunnel",
  starting_host: "Starting remote host",
  waiting_for_host: "Waiting for remote host",
  ready: "Ready",
  backoff: "Waiting to retry",
  detaching: "Detaching",
  detached: "Detached",
};

function needsFrequentPolling(connection: SshConnection): boolean {
  return connection.phase !== "ready" || connection.status === "offline";
}

/**
 * SSH connection profiles under Settings → Connection. Profiles are stored in
 * ~/.omnigent/config.yaml on the server and reconciled into durable remote hosts.
 */
export function ConnectionSettingsBody() {
  const [connections, setConnections] = useState<SshConnection[]>([]);
  const [packageIndexUrl, setPackageIndexUrl] = useState("");
  const [statusById, setStatusById] = useState<StatusMap>({});
  const [label, setLabel] = useState("");
  const [alias, setAlias] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [retryingIds, setRetryingIds] = useState<Set<string>>(() => new Set());
  const [pollTick, setPollTick] = useState(0);
  const requestGeneration = useRef(0);

  const refreshConnections = useCallback(async () => {
    const generation = ++requestGeneration.current;
    const loaded = await fetchSshConnections();
    if (generation !== requestGeneration.current) return loaded;
    setConnections(loaded.connections);
    setPackageIndexUrl(loaded.packageIndexUrl ?? "");
    setLoadError(null);
    return loaded;
  }, []);

  const persist = useCallback(
    async (next: SshConnection[], nextPackageIndexUrl: string | null) => {
      const generation = ++requestGeneration.current;
      const saved = await saveSshConnections(next, nextPackageIndexUrl);
      if (generation === requestGeneration.current) {
        setConnections(saved.connections);
        setPackageIndexUrl(saved.packageIndexUrl ?? "");
      }
      try {
        const refreshed = await fetchSshConnections();
        if (generation !== requestGeneration.current) return refreshed;
        setConnections(refreshed.connections);
        setPackageIndexUrl(refreshed.packageIndexUrl ?? "");
        setLoadError(null);
        return refreshed;
      } catch (error) {
        setLoadError(error instanceof Error ? error.message : "Failed to refresh connections");
      }
      return saved;
    },
    [],
  );

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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (!cancelled) await refreshConnections();
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
  }, [refreshConnections]);

  useEffect(() => {
    if (loading || saving) return;
    const interval = connections.some(needsFrequentPolling) ? ACTIVE_POLL_MS : STABLE_POLL_MS;
    const timer = window.setTimeout(() => {
      void refreshConnections()
        .catch((error) => {
          setLoadError(error instanceof Error ? error.message : "Failed to refresh connections");
        })
        .finally(() => setPollTick((tick) => tick + 1));
    }, interval);
    return () => window.clearTimeout(timer);
  }, [connections, loading, pollTick, refreshConnections, saving]);

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
      createdAt: new Date().toISOString(),
      hostId: null,
      lifecycle: "connected",
      phase: "queued",
      lastError: null,
      attempt: 0,
      nextRetryAt: null,
      updatedAt: new Date().toISOString(),
      status: "offline",
    };

    setSaving(true);
    try {
      const next = [...connections, connection];
      await persist(next, packageIndexUrl.trim() || null);
      setLabel("");
      setAlias("");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Failed to save connection");
    } finally {
      setSaving(false);
    }
  }, [alias, connections, label, packageIndexUrl, persist]);

  const handleSavePackageIndex = useCallback(async () => {
    if (saving || retryingIds.size > 0) return;
    setFormError(null);
    setSaving(true);
    try {
      await persist(connections, packageIndexUrl.trim() || null);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Failed to save package index URL");
    } finally {
      setSaving(false);
    }
  }, [connections, packageIndexUrl, persist, retryingIds.size, saving]);

  const handleRetry = useCallback(
    async (id: string) => {
      if (saving || retryingIds.size > 0) return;
      setRetryingIds((current) => new Set(current).add(id));
      try {
        await retrySshConnection(id);
        setConnections((current) =>
          current.map((connection) =>
            connection.id === id
              ? {
                  ...connection,
                  phase: "queued",
                  lastError: null,
                  nextRetryAt: null,
                }
              : connection,
          ),
        );
        await refreshConnections();
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "Failed to retry connection");
      } finally {
        setRetryingIds((current) => {
          const next = new Set(current);
          next.delete(id);
          return next;
        });
      }
    },
    [refreshConnections, retryingIds.size, saving],
  );

  const handleRemove = useCallback(
    async (id: string) => {
      if (saving || retryingIds.size > 0) return;
      const next = connections.filter((c) => c.id !== id);
    setSaving(true);
    try {
      await persist(next, packageIndexUrl.trim() || null);
        setStatusById((prev) => {
          const copy = { ...prev };
          delete copy[id];
          return copy;
        });
      } catch (error) {
        setFormError(error instanceof Error ? error.message : "Failed to remove connection");
      } finally {
        setSaving(false);
      }
    },
    [connections, packageIndexUrl, persist, retryingIds.size, saving],
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

      <div
        className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4"
        data-testid="ssh-package-index-form"
      >
        <div>
          <h3 className="text-sm font-medium">Remote package index</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Optional HTTPS PyPI simple index used when installing Omnigent on remote SSH hosts.
            Leave blank to use the public PyPI default.
          </p>
        </div>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">Package index URL</span>
          <Input
            value={packageIndexUrl}
            onChange={(e) => setPackageIndexUrl(e.target.value)}
            placeholder="https://pypi.example.com/simple"
            data-testid="ssh-package-index-url"
            disabled={saving || retryingIds.size > 0}
            spellCheck={false}
            autoCapitalize="off"
          />
        </label>
        <div>
          <Button
            type="button"
            size="sm"
            data-testid="ssh-package-index-save"
            disabled={saving || retryingIds.size > 0}
            onClick={() => void handleSavePackageIndex()}
          >
            {saving ? (
              <Loader2Icon className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : null}
            Save package index
          </Button>
        </div>
      </div>

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
                onRetry={() => void handleRetry(connection.id)}
                retrying={retryingIds.has(connection.id)}
                actionsDisabled={saving || retryingIds.size > 0}
                onRemove={() => void handleRemove(connection.id)}
              />
            ))}
          </ul>
        )}
      </div>

      <div
        className="flex flex-col gap-4 rounded-lg border border-border bg-card p-4"
        data-testid="ssh-connection-form"
      >
        <div>
          <h3 className="text-sm font-medium">Add connection</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Enter a Host alias from the server&apos;s <code className="text-xs">~/.ssh/config</code>
            . Saving starts the remote host automatically.
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
              disabled={saving || retryingIds.size > 0}
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
              disabled={saving || retryingIds.size > 0}
              spellCheck={false}
              autoCapitalize="off"
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
            disabled={saving || retryingIds.size > 0}
            onClick={() => void handleAdd()}
          >
            {saving ? (
              <Loader2Icon className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <PlusIcon className="mr-1.5 h-3.5 w-3.5" />
            )}
            Add connection
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
  onRetry,
  retrying,
  actionsDisabled,
  onRemove,
}: {
  connection: SshConnection;
  status?: StatusMap[string];
  onRetest: () => void;
  onRetry: () => void;
  retrying: boolean;
  actionsDisabled: boolean;
  onRemove: () => void;
}) {
  const current = status?.status ?? "unknown";
  const canRetry =
    connection.lifecycle !== "detached" &&
    connection.phase !== "detaching" &&
    connection.phase !== "detached" &&
    (connection.phase === "backoff" ||
      Boolean(connection.lastError) ||
      (connection.phase === "ready" && connection.status === "offline"));
  const phaseLabel =
    connection.lifecycle === "detached"
      ? PHASE_LABELS.detached
      : (PHASE_LABELS[connection.phase] ?? connection.phase.replaceAll("_", " "));
  return (
    <li
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
      data-testid={`ssh-connection-row-${connection.id}`}
    >
      <div className="flex min-w-0 flex-1 items-start gap-2.5">
        <LifecycleStatusIcon connection={connection} />
        <div className="min-w-0">
          <div className="text-sm font-medium">{connection.label}</div>
          <div className="truncate text-xs text-muted-foreground">{connection.alias}</div>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
            <span data-testid={`ssh-connection-phase-${connection.id}`}>{phaseLabel}</span>
            {connection.hostId && (
              <span
                className={connection.status === "online" ? "text-green-600" : "text-destructive"}
                data-testid={`ssh-connection-online-status-${connection.id}`}
              >
                {connection.status === "online" ? "Online" : "Offline"}
              </span>
            )}
            {connection.attempt > 0 && (
              <span className="text-muted-foreground">
                {connection.attempt} {connection.attempt === 1 ? "attempt" : "attempts"}
              </span>
            )}
          </div>
          {connection.nextRetryAt && (
            <div className="mt-0.5 text-xs text-muted-foreground">
              Next retry: {new Date(connection.nextRetryAt).toLocaleString()}
            </div>
          )}
          {connection.lastError && (
            <div
              className="mt-0.5 text-xs text-destructive"
              data-testid={`ssh-connection-last-error-${connection.id}`}
            >
              {connection.lastError}
            </div>
          )}
          {status?.message && current === "failed" && (
            <div className="mt-0.5 text-xs text-destructive">SSH test: {status.message}</div>
          )}
          {current === "ok" && status?.latencyMs != null && (
            <div className="mt-0.5 text-xs text-muted-foreground">
              SSH test passed ({status.latencyMs} ms)
            </div>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {canRetry && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            data-testid={`ssh-connection-retry-${connection.id}`}
            onClick={onRetry}
            disabled={actionsDisabled}
          >
            {retrying && <Loader2Icon className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
            Retry now
          </Button>
        )}
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          aria-label={`Test SSH ${connection.label}`}
          title="Test SSH"
          data-testid={`ssh-connection-retest-${connection.id}`}
          onClick={onRetest}
          disabled={actionsDisabled || current === "checking"}
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
          disabled={actionsDisabled}
        >
          <Trash2Icon className="h-4 w-4" />
        </Button>
      </div>
    </li>
  );
}

function LifecycleStatusIcon({ connection }: { connection: SshConnection }) {
  if (
    connection.phase === "queued" ||
    connection.phase === "waiting_for_ssh" ||
    connection.phase === "installing" ||
    connection.phase === "opening_tunnel" ||
    connection.phase === "starting_host" ||
    connection.phase === "waiting_for_host" ||
    connection.phase === "detaching"
  ) {
    return <Loader2Icon className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-muted-foreground" />;
  }
  if (connection.phase === "ready" && connection.status === "online") {
    return <CheckCircle2Icon className="mt-0.5 h-4 w-4 shrink-0 text-green-500" />;
  }
  if (connection.phase === "backoff" || connection.lastError || connection.status === "offline") {
    return <XCircleIcon className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />;
  }
  return (
    <span
      className={cn("mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-muted-foreground/40")}
      aria-hidden
    />
  );
}
