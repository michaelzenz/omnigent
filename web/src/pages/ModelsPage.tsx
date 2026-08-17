import { useMemo, useState } from "react";
import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useAdminModelSettings, useUpdateAdminModelSettings } from "@/hooks/useModelSettings";

export function ModelsPage() {
  const settings = useAdminModelSettings();
  const update = useUpdateAdminModelSettings();
  const [filter, setFilter] = useState("");
  const data = settings.data;
  const selected = useMemo(() => new Set(data?.omnigentModels ?? []), [data?.omnigentModels]);
  const models = useMemo(() => {
    const query = filter.trim().toLowerCase();
    const filtered = query
      ? (data?.models ?? []).filter(
          (model) =>
            model.id.toLowerCase().includes(query) ||
            model.displayName.toLowerCase().includes(query),
        )
      : (data?.models ?? []);
    return [...filtered].sort(
      (left, right) => Number(selected.has(right.id)) - Number(selected.has(left.id)),
    );
  }, [data?.models, filter, selected]);

  function toggleModel(modelId: string, enabled: boolean) {
    const next = new Set(selected);
    if (enabled) next.add(modelId);
    else next.delete(modelId);
    update.mutate({ omnigentModels: [...next] });
  }

  return (
    <PageScroll contentClassName="px-8" extraBottom="2.5rem">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Models</h1>
          <p className="mt-1 text-ui text-muted-foreground">
            Choose which workspace models Omnigent offers. Additional harnesses can use their own
            selections here in the future.
          </p>
        </div>
        <Button variant="ghost" size="sm" onClick={() => void settings.refetch()}>
          <RefreshCwIcon /> Refresh
        </Button>
      </div>

      {settings.isLoading ? (
        <p className="text-ui text-muted-foreground">Loading models…</p>
      ) : !data?.databricksConnected ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-lg border border-border bg-muted/50 px-4 py-3"
        >
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div>
            <div className="text-ui font-medium">Databricks workspace required</div>
            <p className="text-sm text-muted-foreground">
              Connect to a Databricks workspace to discover and configure models.
            </p>
            {data?.error && <p className="mt-1 text-sm text-destructive">{data.error}</p>}
          </div>
        </div>
      ) : (
        <section className="rounded-lg border border-border bg-background">
          <div className="border-b border-border p-4">
            <h2 className="text-ui font-medium">Omnigent harness</h2>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Enabled models appear in new-session, chat, and role-profile model pickers.
              {data.profile ? ` Connected with profile ${data.profile}.` : ""}
            </p>
            <Input
              className="mt-3 max-w-md"
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Filter workspace models…"
              aria-label="Filter workspace models"
            />
          </div>
          <div className="divide-y divide-border">
            {models.map((model) => (
              <div key={model.id} className="flex items-center justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-ui font-medium">{model.displayName}</div>
                  <code className="block truncate text-sm text-muted-foreground">{model.id}</code>
                </div>
                <Switch
                  checked={selected.has(model.id)}
                  disabled={update.isPending}
                  onCheckedChange={(checked) => toggleModel(model.id, checked)}
                  aria-label={`Offer ${model.displayName} in Omnigent`}
                />
              </div>
            ))}
            {models.length === 0 && (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                No models match this filter.
              </p>
            )}
          </div>
        </section>
      )}

      {(settings.isError || update.isError) && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {settings.error?.message ?? update.error?.message}
        </p>
      )}
    </PageScroll>
  );
}
