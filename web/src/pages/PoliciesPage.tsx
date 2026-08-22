/**
 * Admin default-policies management page (``/settings/policies``; the legacy
 * ``/policies`` path redirects here). Rendered as a Settings sub-category.
 *
 * Lists every global default policy and lets admins add, toggle,
 * and remove them. The add-policy dialog reuses the same registry-
 * driven picker as the per-session policy UI in AgentInfo.
 *
 * Gated on the client by an early admin check (non-admins see a
 * "no permission" message) AND on the server by the route handlers
 * themselves — client-side gating is just UX.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangleIcon,
  ChevronsUpDownIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  TrashIcon,
} from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import {
  EditPolicyInstanceDialog,
  PolicyInstanceFields,
  policyParamProperties,
} from "@/components/PolicyInstanceEditor";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  useDefaultPolicies,
  useAddDefaultPolicy,
  useUpdateDefaultPolicy,
  useDeleteDefaultPolicy,
  type DefaultPolicy,
} from "@/hooks/useDefaultPolicies";
import { usePolicyRegistry, type PolicyRegistryEntry } from "@/hooks/usePolicies";
import { useAdminModelSettings, useUpdateAdminModelSettings } from "@/hooks/useModelSettings";
import { getCurrentIsAdmin, resolveIdentity } from "@/lib/identity";
import { useServerInfo } from "@/lib/CapabilitiesContext";
import { isSingleUserMode } from "@/lib/capabilities";
import { coercePolicyParams } from "@/lib/policyParams";

// ---------------------------------------------------------------------------
// Add-policy dialog (registry-driven, same UX as session policies)
// ---------------------------------------------------------------------------

function AddDefaultPolicyDialog({
  registry,
  modelIds,
  open,
  onOpenChange,
}: {
  registry: PolicyRegistryEntry[];
  modelIds: string[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [selected, setSelected] = useState<string>("");
  const [filter, setFilter] = useState("");
  const [policyName, setPolicyName] = useState<string>("");
  const [factoryParams, setFactoryParams] = useState<Record<string, string>>({});
  const [paramError, setParamError] = useState<string | null>(null);
  const addPolicy = useAddDefaultPolicy();

  const entry = registry.find((r) => r.handler === selected);
  const properties = useMemo(() => policyParamProperties(entry, modelIds), [entry, modelIds]);
  const paramKeys = Object.keys(properties);

  function handleSelect(handler: string) {
    const e = registry.find((r) => r.handler === handler);
    setSelected(handler);
    setFilter("");
    setPolicyName(e ? e.name.toLowerCase().replace(/\s+/g, "_") : "");
    setFactoryParams({});
    setParamError(null);
  }

  function handleAdd() {
    if (!entry) return;
    let parsedParams: Record<string, unknown> | undefined;
    if (entry.kind === "factory" && paramKeys.length > 0) {
      const result = coercePolicyParams(paramKeys, properties, factoryParams);
      if (!result.ok) {
        setParamError(result.error);
        return;
      }
      parsedParams = result.params;
    }
    setParamError(null);
    const includeFactoryParams =
      entry.kind === "factory" ? { factory_params: parsedParams ?? {} } : {};
    addPolicy.mutate(
      {
        name: policyName || entry.name.toLowerCase().replace(/\s+/g, "_"),
        type: "python",
        handler: entry.handler,
        ...includeFactoryParams,
      },
      {
        onSuccess: () => {
          setSelected("");
          setPolicyName("");
          setFactoryParams({});
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // Reset to the policy list on close so reopening never lands mid-config.
        if (!next) {
          setSelected("");
          setPolicyName("");
          setFactoryParams({});
          setParamError(null);
        }
        onOpenChange(next);
      }}
    >
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Global Policy</DialogTitle>
          <DialogDescription>Choose a policy to apply globally to all sessions.</DialogDescription>
        </DialogHeader>
        <div className="min-w-0 space-y-3 pt-1">
          {!selected &&
            (() => {
              const lowerFilter = filter.toLowerCase();
              const filtered = lowerFilter
                ? registry.filter(
                    (r) =>
                      r.name.toLowerCase().includes(lowerFilter) ||
                      r.description?.toLowerCase().includes(lowerFilter),
                  )
                : registry;
              return (
                <>
                  <input
                    type="text"
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Filter policies..."
                    className="w-full rounded border border-border bg-background px-2 py-1.5 text-ui placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-ring"
                    autoFocus
                  />
                  <div className="flex max-h-52 flex-col divide-y divide-border overflow-y-auto rounded border border-border">
                    {filtered.map((r) => (
                      <button
                        key={r.handler}
                        type="button"
                        onClick={() => handleSelect(r.handler)}
                        className="flex flex-col gap-0.5 px-2.5 py-2 text-left hover:bg-muted"
                      >
                        <span className="text-ui">{r.name}</span>
                        {r.description && (
                          <span className="line-clamp-2 text-sm text-muted-foreground">
                            {r.description}
                          </span>
                        )}
                      </button>
                    ))}
                    {filtered.length === 0 && (
                      <p className="py-2 text-center text-sm text-muted-foreground">
                        No policies match your filter.
                      </p>
                    )}
                  </div>
                </>
              );
            })()}
          {entry && (
            <div className="flex flex-col gap-1 rounded border border-border bg-muted/50 px-2.5 py-2">
              <div className="flex items-center justify-between">
                <span className="text-ui font-medium">{entry.name}</span>
                <button
                  type="button"
                  onClick={() => {
                    setSelected("");
                    setPolicyName("");
                    setFactoryParams({});
                    setParamError(null);
                  }}
                  className="text-sm text-muted-foreground hover:text-foreground"
                >
                  Change
                </button>
              </div>
              {entry.description && (
                <p className="text-sm text-muted-foreground">{entry.description}</p>
              )}
            </div>
          )}
          {entry && (
            <PolicyInstanceFields
              name={policyName}
              onNameChange={setPolicyName}
              properties={entry.kind === "factory" ? properties : {}}
              factoryParams={factoryParams}
              onFactoryParamsChange={setFactoryParams}
            />
          )}
          {(paramError || addPolicy.isError) && (
            <div
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui text-destructive"
            >
              {paramError ?? addPolicy.error?.message}
            </div>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                // With a policy selected, Cancel steps back to the list so the
                // user can pick another; only close the dialog from the list.
                if (selected) {
                  setSelected("");
                  setFactoryParams({});
                  setParamError(null);
                } else {
                  onOpenChange(false);
                }
              }}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleAdd}
              loading={addPolicy.isPending}
              disabled={!selected}
            >
              Add
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

const NO_POLICY_MODEL = "__none__";

function PolicyModelPicker({
  models,
  value,
  disabled,
  onChange,
}: {
  models: { id: string; displayName: string }[];
  value: string | null;
  disabled: boolean;
  onChange: (model: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = models.find((model) => model.id === value);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          aria-label="Policy checking model"
          aria-expanded={open}
          className="mt-3 w-full max-w-md justify-between font-normal"
        >
          <span className="truncate">{selected?.displayName ?? value ?? "No model selected"}</span>
          <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-0">
        <Command>
          <CommandInput placeholder="Search Databricks models…" />
          <CommandList>
            <CommandEmpty>No models found.</CommandEmpty>
            <CommandGroup>
              <CommandItem
                value={NO_POLICY_MODEL}
                data-checked={value === null}
                onSelect={() => {
                  onChange(null);
                  setOpen(false);
                }}
              >
                No model selected
              </CommandItem>
              {models.map((model) => (
                <CommandItem
                  key={model.id}
                  value={`${model.displayName} ${model.id}`}
                  data-checked={model.id === value}
                  onSelect={() => {
                    onChange(model.id);
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0">
                    <span className="block truncate">{model.displayName}</span>
                    <code className="block truncate text-sm text-muted-foreground">{model.id}</code>
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

function PolicyCheckingModelSection() {
  const settings = useAdminModelSettings();
  const update = useUpdateAdminModelSettings();
  const data = settings.data;

  return (
    <section className="mb-6 rounded-lg border border-border bg-background p-4">
      <h2 className="text-ui font-medium">Policy checking model</h2>
      <p className="mt-0.5 text-sm text-muted-foreground">
        Used by intent-based and other LLM-backed policies. This updates the server
        <code className="mx-1">llm:</code>
        configuration.
      </p>
      {!settings.isLoading && !data?.databricksConnected ? (
        <div
          role="alert"
          className="mt-3 flex items-start gap-2 rounded-md border border-warning/50 bg-warning/15 px-3 py-2 text-foreground"
        >
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-warning" />
          <span className="text-sm font-medium">
            Connect to a Databricks workspace to enable intent based policies.
          </span>
        </div>
      ) : (
        <PolicyModelPicker
          models={data?.models ?? []}
          value={data?.policyModel ?? null}
          disabled={settings.isLoading || update.isPending}
          onChange={(policyModel) => update.mutate({ policyModel })}
        />
      )}
      {(settings.isError || update.isError) && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {settings.error?.message ?? update.error?.message}
        </p>
      )}
    </section>
  );
}

export function PoliciesPage() {
  const info = useServerInfo();
  // Explicit single-user local runtime: no auth endpoints exist, so skip the
  // admin probe. A multi-user header-auth deploy (same accounts_enabled:false
  // / login_url:null shape) is NOT single-user and keeps its admin gate.
  const isSingleUser = isSingleUserMode(info);
  const [meIsAdmin, setMeIsAdmin] = useState<boolean | null>(null);
  const { data: policies = [], refetch } = useDefaultPolicies();
  const { data: registry = [] } = usePolicyRegistry();
  const modelSettings = useAdminModelSettings();
  const updatePolicy = useUpdateDefaultPolicy();
  const deletePolicy = useDeleteDefaultPolicy();
  const [addOpen, setAddOpen] = useState(false);
  const [editCandidate, setEditCandidate] = useState<DefaultPolicy | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<DefaultPolicy | null>(null);
  const [pendingAction, setPendingAction] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const registryByHandler = new Map(registry.map((r) => [r.handler, r]));

  const refresh = useCallback(() => {
    void refetch();
  }, [refetch]);

  // Admin probe via the mode-agnostic `/v1/me` identity (works under OIDC
  // too, unlike the accounts-only `/auth/me`). Skipped in single-user mode
  // because no auth endpoints exist and the backend skips admin enforcement.
  useEffect(() => {
    if (isSingleUser) return;
    void (async () => {
      const userId = await resolveIdentity();
      if (userId === null) return;
      setMeIsAdmin(getCurrentIsAdmin());
    })();
  }, [isSingleUser]);

  if (!isSingleUser && meIsAdmin === null) {
    return (
      <div className="flex min-h-full items-center justify-center text-ui text-muted-foreground">
        Loading...
      </div>
    );
  }

  if (!isSingleUser && meIsAdmin === false) {
    return (
      <PageScroll contentClassName="px-8" extraBottom="2.5rem">
        <h1 className="mb-2 text-2xl font-semibold">Global Policies</h1>
        <p className="text-ui text-muted-foreground">
          You don't have permission to manage global policies.
        </p>
      </PageScroll>
    );
  }

  async function onConfirmDelete() {
    if (deleteCandidate === null || deleteCandidate.id === null) return;
    setPendingAction(true);
    setActionError(null);
    deletePolicy.mutate(deleteCandidate.id, {
      onSuccess: () => {
        setPendingAction(false);
        setDeleteCandidate(null);
      },
      onError: (err) => {
        setPendingAction(false);
        setActionError(err.message);
      },
    });
  }

  return (
    <PageScroll contentClassName="px-8" extraBottom="2.5rem">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Global Policies</h1>
          <p className="mt-1 text-ui text-muted-foreground">
            Global policies applied to all sessions.
          </p>
        </div>
        <Button onClick={() => setAddOpen(true)}>
          <PlusIcon /> Add policy
        </Button>
      </div>

      <PolicyCheckingModelSection />

      {policies.length > 0 && (
        <div className="flex flex-col gap-3">
          {policies.map((p) => {
            const registryEntry = registryByHandler.get(p.handler);
            const params = p.factory_params;
            const hasParams = params != null && Object.keys(params).length > 0;
            const missingRequiredModel =
              p.enabled && registryEntry?.requires_llm === true && !modelSettings.data?.policyModel;
            return (
              <div
                key={p.id ?? p.name}
                className="rounded-lg border border-border bg-background p-4"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-2.5 min-w-0">
                    <ShieldCheckIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-ui font-medium">{p.name}</span>
                        {p.source === "config" && (
                          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            Config
                          </span>
                        )}
                        {!p.enabled && p.source !== "config" && (
                          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                            Disabled
                          </span>
                        )}
                      </div>
                      {registryEntry?.description && (
                        <p className="mt-0.5 text-sm text-muted-foreground">
                          {registryEntry.description}
                        </p>
                      )}
                      <code className="mt-1 block text-sm text-muted-foreground/70">
                        {p.handler}
                      </code>
                      {missingRequiredModel && (
                        <div
                          role="alert"
                          className="mt-2 flex items-start gap-1.5 text-sm text-warning"
                        >
                          <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
                          <span>
                            This policy requires a policy checking model. Choose one above before
                            relying on it.
                          </span>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {p.source !== "config" ? (
                      <>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 text-muted-foreground"
                          title="Edit policy"
                          aria-label={`Edit ${p.name}`}
                          onClick={() => setEditCandidate(p)}
                        >
                          <PencilIcon className="size-3.5" />
                        </Button>
                        <Switch
                          checked={p.enabled}
                          onCheckedChange={(checked) =>
                            updatePolicy.mutate({
                              policyId: p.id!,
                              enabled: checked,
                            })
                          }
                          aria-label={`Toggle ${p.name}`}
                        />
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 text-muted-foreground hover:text-destructive"
                          title="Remove policy"
                          onClick={() => setDeleteCandidate(p)}
                          disabled={pendingAction}
                        >
                          <TrashIcon className="size-3.5" />
                        </Button>
                      </>
                    ) : null}
                  </div>
                </div>
                {hasParams && (
                  <div className="ml-6.5 mt-2 rounded-md border border-border/60 bg-muted/40 px-3 py-2">
                    <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">
                      Parameters
                    </span>
                    <div className="mt-1 flex flex-col gap-0.5">
                      {Object.entries(params).map(([key, value]) => (
                        <div key={key} className="flex items-baseline gap-1.5 text-sm">
                          <span className="font-medium text-foreground/80">{key}:</span>
                          <span className="text-muted-foreground">
                            {Array.isArray(value) ? value.join(", ") : String(value)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {policies.length === 0 && (
        <p className="text-ui text-muted-foreground">
          No global policies configured. Add one to apply it to all sessions.
        </p>
      )}

      <div className="mt-3 flex items-center justify-end">
        <Button variant="ghost" size="sm" onClick={refresh}>
          <RefreshCwIcon /> Refresh
        </Button>
      </div>

      <AddDefaultPolicyDialog
        registry={registry}
        modelIds={(modelSettings.data?.models ?? []).map((model) => model.id)}
        open={addOpen}
        onOpenChange={setAddOpen}
      />

      <EditPolicyInstanceDialog
        policy={
          editCandidate
            ? {
                name: editCandidate.name,
                handler: editCandidate.handler,
                factory_params: editCandidate.factory_params,
              }
            : null
        }
        registryEntry={editCandidate ? registryByHandler.get(editCandidate.handler) : undefined}
        modelIds={(modelSettings.data?.models ?? []).map((model) => model.id)}
        open={editCandidate !== null}
        onOpenChange={(open) => {
          if (!open) setEditCandidate(null);
        }}
        onSave={(payload) => {
          if (!editCandidate?.id) return;
          updatePolicy.mutate(
            { policyId: editCandidate.id, ...payload },
            { onSuccess: () => setEditCandidate(null) },
          );
        }}
        isPending={updatePolicy.isPending}
        error={updatePolicy.isError ? updatePolicy.error : null}
      />

      {/* Delete confirmation */}
      <Dialog
        open={deleteCandidate !== null}
        onOpenChange={(open) => {
          if (pendingAction) return;
          if (!open) {
            setDeleteCandidate(null);
            setActionError(null);
          }
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Remove {deleteCandidate?.name}?</DialogTitle>
            <DialogDescription>
              This removes the global policy from all sessions. Existing session-level policies with
              the same handler are unaffected.
            </DialogDescription>
          </DialogHeader>
          {actionError !== null && (
            <div
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-ui text-destructive"
            >
              {actionError}
            </div>
          )}
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteCandidate(null)}
              disabled={pendingAction}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => void onConfirmDelete()}
              loading={pendingAction}
            >
              Remove
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageScroll>
  );
}
