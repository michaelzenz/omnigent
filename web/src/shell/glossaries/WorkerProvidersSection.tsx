import { useEffect, useMemo, useState } from "react";
import { Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAvailableAgents } from "@/hooks/useAvailableAgents";
import { useHosts } from "@/hooks/useHosts";
import {
  useCreateWorkerProvider,
  useDeleteWorkerProvider,
  useUpdateWorkerProvider,
  useWorkerProviders,
} from "@/hooks/useWorkerProviders";
import type { InternalWorkerProviderConfiguration, WorkerProvider } from "@/lib/workerProvidersApi";
import { WorkspacePathField } from "@/shell/WorkspacePathField";
import { RoleHarnessPicker } from "./RoleHarnessPicker";

const EMPTY_CONFIG: InternalWorkerProviderConfiguration = {
  agent_id: null,
  host_id: null,
  workspace: null,
  harness: null,
  model: null,
};

function internalConfiguration(provider: WorkerProvider): InternalWorkerProviderConfiguration {
  return { ...EMPTY_CONFIG, ...provider.configuration } as InternalWorkerProviderConfiguration;
}

function WorkerProviderCard({ provider }: { provider: WorkerProvider }) {
  const updateProvider = useUpdateWorkerProvider(provider.id);
  const deleteProvider = useDeleteWorkerProvider();
  const { data: agents = [] } = useAvailableAgents();
  const { data: hosts = [] } = useHosts();
  const [name, setName] = useState(provider.name);
  const [description, setDescription] = useState(provider.description ?? "");
  const [configuration, setConfiguration] = useState(() => internalConfiguration(provider));

  useEffect(() => {
    setName(provider.name);
    setDescription(provider.description ?? "");
    setConfiguration(internalConfiguration(provider));
  }, [provider]);

  const selectedHost = useMemo(
    () => hosts.find((host) => host.host_id === configuration.host_id) ?? null,
    [configuration.host_id, hosts],
  );

  if (provider.kind === "external") {
    return (
      <article className="space-y-2 rounded-lg border p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="font-medium">{provider.name}</h4>
            <p className="text-xs text-muted-foreground">External worker provider</p>
          </div>
          {!provider.built_in ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete ${provider.name}`}
              onClick={() => deleteProvider.mutate(provider.id)}
            >
              <Trash2Icon className="size-4" />
            </Button>
          ) : null}
        </div>
        {provider.description ? <p className="text-sm">{provider.description}</p> : null}
        <p className="text-xs text-muted-foreground">
          External provider settings are managed by its adapter.
        </p>
      </article>
    );
  }

  const patchConfiguration = (patch: Partial<InternalWorkerProviderConfiguration>) => {
    setConfiguration((current) => ({ ...current, ...patch }));
  };
  const save = () =>
    updateProvider.mutate({
      name: name.trim(),
      description: description.trim() || null,
      configuration,
    });

  return (
    <article
      className="space-y-4 rounded-lg border p-4"
      data-testid={`worker-provider-${provider.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="grid min-w-0 flex-1 gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-muted-foreground">
            Display name
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Execution target
            <Select
              value={configuration.agent_id ?? undefined}
              onValueChange={(agent_id) => {
                const agent = agents.find((candidate) => candidate.id === agent_id);
                patchConfiguration({
                  agent_id,
                  harness: agent?.default_harness ?? agent?.harness ?? null,
                  model: agent?.default_model ?? null,
                });
              }}
            >
              <SelectTrigger>
                <SelectValue placeholder="Select execution target" />
              </SelectTrigger>
              <SelectContent>
                {agents.map((agent) => (
                  <SelectItem key={agent.id} value={agent.id}>
                    {agent.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
        {!provider.built_in ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label={`Delete ${provider.name}`}
            onClick={() => deleteProvider.mutate(provider.id)}
          >
            <Trash2Icon className="size-4" />
          </Button>
        ) : null}
      </div>
      <label className="block space-y-1 text-xs text-muted-foreground">
        Description shown to the manager
        <Textarea
          value={description}
          rows={2}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-muted-foreground">
          Host
          <Select
            value={configuration.host_id ?? undefined}
            onValueChange={(host_id) => patchConfiguration({ host_id })}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select host" />
            </SelectTrigger>
            <SelectContent>
              {hosts.map((host) => (
                <SelectItem key={host.host_id} value={host.host_id}>
                  {host.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Workspace
          <WorkspacePathField
            hostId={configuration.host_id}
            value={configuration.workspace ?? ""}
            onChange={(workspace) => patchConfiguration({ workspace: workspace || null })}
            onBrowse={() => {}}
            recent={[]}
          />
        </label>
        <div className="space-y-1 text-xs text-muted-foreground sm:col-span-2">
          Harness and model
          <RoleHarnessPicker
            host={selectedHost}
            agents={agents}
            harness={configuration.harness ?? ""}
            model={configuration.model ?? ""}
            testId={`worker-provider-harness-${provider.id}`}
            onChange={({ harness, model }) => patchConfiguration({ harness, model })}
          />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {provider.available ? "Available" : (provider.unavailable_reason ?? "Unavailable")}
        </span>
        <Button disabled={!name.trim() || updateProvider.isPending} onClick={save}>
          {updateProvider.isPending ? "Saving…" : "Save provider"}
        </Button>
      </div>
      {updateProvider.error ? (
        <p className="text-xs text-destructive">{updateProvider.error.message}</p>
      ) : null}
    </article>
  );
}

export function WorkerProvidersSection() {
  const { data: providers = [], isLoading, error } = useWorkerProviders();
  const createProvider = useCreateWorkerProvider();

  const addInternalProvider = () =>
    createProvider.mutate({
      name: "Internal worker",
      description: null,
      kind: "internal",
      configuration: EMPTY_CONFIG,
    });

  return (
    <section className="space-y-3" data-testid="glossary-worker-providers-section">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">Worker providers</h3>
          <p className="text-xs text-muted-foreground">
            Providers tell the manager how to initialize workers. They do not add a prompt.
          </p>
        </div>
        <Button size="sm" onClick={addInternalProvider} disabled={createProvider.isPending}>
          Add provider
        </Button>
      </div>
      {isLoading ? <p className="text-sm text-muted-foreground">Loading providers…</p> : null}
      {error ? <p className="text-sm text-destructive">{error.message}</p> : null}
      {providers.map((provider) => (
        <WorkerProviderCard key={provider.id} provider={provider} />
      ))}
    </section>
  );
}
