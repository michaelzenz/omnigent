import { useEffect, useMemo, useState } from "react";
import { SettingsIcon, Trash2Icon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useAvailableAgents, type AvailableAgent } from "@/hooks/useAvailableAgents";
import { useOmniHarnessModelOptions } from "@/hooks/useModelSettings";
import {
  useCreateWorkerProvider,
  useDeleteWorkerProvider,
  useUpdateWorkerProvider,
  useWorkerProviders,
} from "@/hooks/useWorkerProviders";
import type { InternalWorkerProviderConfiguration, WorkerProvider } from "@/lib/workerProvidersApi";
import { isOnihTargetName } from "@/lib/omniharnessModels";
import { AgentHarnessPicker, groupNewSessionAgents } from "@/shell/NewChatDialog";

const DEFAULT_MODEL = "__default__";
const EMPTY_CONFIG: InternalWorkerProviderConfiguration = {
  agent_id: null,
  model: null,
};

function internalConfiguration(provider: WorkerProvider): InternalWorkerProviderConfiguration {
  const raw = provider.configuration as Partial<InternalWorkerProviderConfiguration>;
  return {
    agent_id: raw.agent_id ?? null,
    model: raw.model ?? null,
  };
}

function ProviderLaunchControls({
  configuration,
  agents,
  disabled,
  onChange,
}: {
  configuration: InternalWorkerProviderConfiguration;
  agents: readonly AvailableAgent[];
  disabled: boolean;
  onChange: (patch: Partial<InternalWorkerProviderConfiguration>) => void;
}) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const modelOptions = useOmniHarnessModelOptions().data ?? [];
  const { agentList, agentEntries, harnessEntries } = useMemo(
    () => groupNewSessionAgents(agents),
    [agents],
  );
  const selectedAgent =
    configuration.agent_id === null
      ? (agentList[0] ?? null)
      : (agentList.find((agent) => agent.id === configuration.agent_id) ?? null);
  const omniHarnessSelected = isOnihTargetName(selectedAgent?.name);

  useEffect(() => {
    if (configuration.agent_id === null && selectedAgent !== null) {
      onChange({ agent_id: selectedAgent.id });
    }
  }, [configuration.agent_id, onChange, selectedAgent]);

  return (
    <>
      <div className="flex items-end justify-end gap-2">
        {omniHarnessSelected ? (
          <label className="w-52 space-y-1 text-xs text-muted-foreground">
            Model
            <Select
              value={configuration.model ?? DEFAULT_MODEL}
              onValueChange={(value) => onChange({ model: value === DEFAULT_MODEL ? null : value })}
              disabled={disabled}
            >
              <SelectTrigger data-testid="worker-provider-model-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_MODEL}>Default</SelectItem>
                {modelOptions.map((option) => (
                  <SelectItem key={option.id} value={option.id}>
                    {option.displayName}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        ) : null}
        <div className="space-y-1 text-xs text-muted-foreground">
          Harness
          <div className="flex items-center rounded-md border bg-background [&>button]:bg-transparent!">
            <AgentHarnessPicker
              agentEntries={agentEntries}
              harnessEntries={harnessEntries}
              effectiveAgentId={selectedAgent?.id ?? null}
              agentLabel={selectedAgent?.display_name ?? "Select harness"}
              hasAgents={agentList.length > 0}
              disabled={disabled}
              host={null}
              onSelectAgent={(agent) => onChange({ agent_id: agent.id, model: null })}
              pendingAgent={null}
              pendingAgentId="__worker_provider_pending__"
              onSelectPending={() => {}}
              onCreateCustomAgent={() => {}}
              allowCreateCustomAgent={false}
              triggerClassName="h-9 min-w-40 justify-between rounded-r-none"
              triggerTestId="worker-provider-harness-select"
            />
            <span aria-hidden className="h-4 w-px shrink-0 bg-border" />
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className="size-9 rounded-l-none text-muted-foreground"
              disabled={disabled || selectedAgent === null}
              onClick={() => setSettingsOpen(true)}
              aria-label={`Configure ${selectedAgent?.display_name ?? "harness"}`}
              data-testid="worker-provider-config-gear"
            >
              <SettingsIcon className="size-4" />
            </Button>
          </div>
        </div>
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Configure {selectedAgent?.display_name ?? "worker"}</DialogTitle>
            <DialogDescription>
              Advanced launch overrides for workers created from this provider.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              {omniHarnessSelected
                ? "Choose the OmniHarness model with the selector beside the harness."
                : "Host-specific harness settings are resolved when the manager proposes a workspace."}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" onClick={() => setSettingsOpen(false)}>
              Done
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function WorkerProviderCard({ provider }: { provider: WorkerProvider }) {
  const updateProvider = useUpdateWorkerProvider(provider.id);
  const deleteProvider = useDeleteWorkerProvider();
  const { data: agents = [] } = useAvailableAgents();
  const [name, setName] = useState(provider.name);
  const [description, setDescription] = useState(provider.description ?? "");
  const [configuration, setConfiguration] = useState(() => internalConfiguration(provider));

  useEffect(() => {
    setName(provider.name);
    setDescription(provider.description ?? "");
    setConfiguration(internalConfiguration(provider));
  }, [provider]);

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
        <label className="min-w-0 flex-1 space-y-1 text-xs text-muted-foreground">
          Display name
          <Input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
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
      <ProviderLaunchControls
        configuration={configuration}
        agents={agents}
        disabled={updateProvider.isPending}
        onChange={patchConfiguration}
      />
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">
          {provider.available ? "Available" : (provider.unavailable_reason ?? "Unavailable")}
        </span>
        <Button
          disabled={!name.trim() || !configuration.agent_id || updateProvider.isPending}
          onClick={save}
        >
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
