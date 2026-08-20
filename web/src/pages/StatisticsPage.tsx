import { useMemo, useState } from "react";
import {
  AlertTriangleIcon,
  BarChart3Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  Loader2Icon,
} from "lucide-react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { PageScroll } from "@/components/PageScroll";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { useAdminModelSettings, useUpdateAdminModelSettings } from "@/hooks/useModelSettings";
import {
  useClearModelPricing,
  useStatisticsReport,
  useUpdateModelPricing,
} from "@/hooks/useStatisticsReport";
import { formatTokenCount } from "@/lib/formatCost";
import type {
  EnabledModelPricing,
  StatisticsBreakdown,
  StatisticsReport,
} from "@/lib/statisticsApi";

function currentMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(month: string): string {
  const [year, numericMonth] = month.split("-").map(Number);
  if (!year || !numericMonth) return month;
  return new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, numericMonth - 1, 1)));
}

function formatCost(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "Unavailable";
  if (value > 0 && value < 0.01) return "<$0.01";
  return `$${value.toFixed(2)}`;
}

function formatRate(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `$${value.toFixed(value < 1 ? 3 : 2)}`;
}

function formatShare(value: number): string {
  const percent = value <= 1 ? value * 100 : value;
  return `${percent.toFixed(1)}%`;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: { value?: number | null; payload?: { tokens?: number; calls?: number } }[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  return (
    <div className="rounded-lg border border-border bg-popover px-3 py-2 text-xs shadow-menu">
      <p className="font-medium">{label}</p>
      <p className="mt-1 tabular-nums text-muted-foreground">{formatCost(entry.value ?? null)}</p>
      {entry.payload?.tokens != null && (
        <p className="tabular-nums text-muted-foreground">
          {formatTokenCount(entry.payload.tokens)} tokens · {entry.payload.calls ?? 0} calls
        </p>
      )}
    </div>
  );
}

function DailyChart({ report }: { report: StatisticsReport }) {
  const data = report.daily.map((row) => ({
    day: new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    }).format(new Date(`${row.day}T00:00:00Z`)),
    cost: row.costUsd,
    tokens: row.totalTokens,
    calls: row.calls,
  }));

  return (
    <div
      className="h-64 w-full"
      role="img"
      aria-label={`Daily Omnigent consumption for ${monthLabel(report.month)}`}
      data-testid="statistics-daily-chart"
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="3 3" />
          <XAxis
            dataKey="day"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            className="fill-muted-foreground"
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={52}
            tickFormatter={(value: number) => `$${value}`}
            className="fill-muted-foreground"
          />
          <Tooltip content={<ChartTooltip />} cursor={{ fill: "var(--accent)", opacity: 0.35 }} />
          <Bar dataKey="cost" fill="var(--primary)" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function BreakdownSection({
  id,
  title,
  description,
  rows,
  workloadControl,
  disabledGuidance,
}: {
  id: string;
  title: string;
  description: string;
  rows: StatisticsBreakdown[];
  workloadControl?: React.ReactNode;
  disabledGuidance?: React.ReactNode;
}) {
  const chartData = rows.map((row) => ({
    name: row.label,
    cost: row.costUsd,
    tokens: row.totalTokens,
    calls: row.calls,
  }));
  const chartHeight = Math.max(180, rows.length * 40 + 44);

  return (
    <section
      className="border-t border-border pt-6"
      aria-labelledby={`${id}-heading`}
      data-testid={`statistics-${id}-section`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id={`${id}-heading`} className="text-base font-semibold">
            {title}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">{description}</p>
        </div>
        {workloadControl}
      </div>
      {disabledGuidance}
      {rows.length === 0 ? (
        <div className="mt-4 rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
          No {title.toLowerCase()} data for this month.
        </div>
      ) : (
        <div className="mt-4 grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(390px,0.9fr)]">
          <div
            style={{ height: chartHeight }}
            role="img"
            aria-label={`${title} chart`}
            data-testid={`statistics-${id}-chart`}
          >
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chartData}
                layout="vertical"
                margin={{ top: 4, right: 12, bottom: 0, left: 0 }}
              >
                <CartesianGrid horizontal={false} stroke="var(--border)" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(value: number) => `$${value}`}
                  className="fill-muted-foreground"
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fontSize: 11 }}
                  tickLine={false}
                  axisLine={false}
                  width={132}
                  className="fill-muted-foreground"
                />
                <Tooltip
                  content={<ChartTooltip />}
                  cursor={{ fill: "var(--accent)", opacity: 0.35 }}
                />
                <Bar dataKey="cost" fill="var(--primary)" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[390px] text-left text-xs">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Name
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Cost
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Tokens
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Calls
                  </th>
                  <th scope="col" className="px-3 py-2 text-right font-medium">
                    Share
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {rows.map((row) => (
                  <tr key={row.key}>
                    <th scope="row" className="max-w-56 px-3 py-2 font-medium">
                      {row.label}
                    </th>
                    <td className="px-3 py-2 text-right tabular-nums">{formatCost(row.costUsd)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {formatTokenCount(row.totalTokens)}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">
                      {row.calls.toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums">{formatShare(row.share)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}

interface PricingForm {
  input: string;
  output: string;
  cacheRead: string;
  cacheWrite: string;
}

function pricingForm(row: EnabledModelPricing): PricingForm {
  return {
    input: row.inputPerMillion?.toString() ?? "",
    output: row.outputPerMillion?.toString() ?? "",
    cacheRead: row.cacheReadPerMillion?.toString() ?? "",
    cacheWrite: row.cacheWritePerMillion?.toString() ?? "",
  };
}

function validatePricing(form: PricingForm): string | null {
  const required = [
    ["Input", form.input],
    ["Output", form.output],
  ] as const;
  for (const [label, value] of required) {
    if (value.trim() === "") return `${label} rate is required.`;
    if (!Number.isFinite(Number(value)) || Number(value) < 0) {
      return `${label} rate must be a nonnegative number.`;
    }
  }
  for (const [label, value] of [
    ["Cache read", form.cacheRead],
    ["Cache write", form.cacheWrite],
  ] as const) {
    if (value.trim() !== "" && (!Number.isFinite(Number(value)) || Number(value) < 0)) {
      return `${label} rate must be a nonnegative number.`;
    }
  }
  return null;
}

function PricingDialog({ pricing, month }: { pricing: EnabledModelPricing[]; month: string }) {
  const updatePricing = useUpdateModelPricing(month);
  const clearPricing = useClearModelPricing(month);
  const [editingModel, setEditingModel] = useState<string | null>(null);
  const [form, setForm] = useState<PricingForm | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);
  const mutationError = updatePricing.error ?? clearPricing.error;
  const pending = updatePricing.isPending || clearPricing.isPending;

  const beginEdit = (row: EnabledModelPricing) => {
    updatePricing.reset();
    clearPricing.reset();
    setValidationError(null);
    setEditingModel(row.model);
    setForm(pricingForm(row));
  };

  const cancelEdit = () => {
    updatePricing.reset();
    setEditingModel(null);
    setForm(null);
    setValidationError(null);
  };

  const save = (model: string) => {
    if (!form) return;
    const error = validatePricing(form);
    setValidationError(error);
    if (error) return;
    updatePricing.mutate(
      {
        model,
        pricing: {
          inputPerMillion: Number(form.input),
          outputPerMillion: Number(form.output),
          cacheReadPerMillion: form.cacheRead.trim() === "" ? null : Number(form.cacheRead),
          cacheWritePerMillion: form.cacheWrite.trim() === "" ? null : Number(form.cacheWrite),
        },
      },
      { onSuccess: cancelEdit },
    );
  };

  const clear = (model: string) => {
    updatePricing.reset();
    clearPricing.reset();
    setValidationError(null);
    clearPricing.mutate(model);
  };

  const setField = (field: keyof PricingForm, value: string) => {
    setForm((current) => (current ? { ...current, [field]: value } : current));
    setValidationError(null);
  };

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" data-testid="statistics-pricing-trigger">
          Cost / token
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Enabled Omnigent model pricing</DialogTitle>
          <DialogDescription>
            Effective rates for enabled models. Pricing edits affect future calls only; historical
            monthly costs remain unchanged.
          </DialogDescription>
        </DialogHeader>
        {pricing.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            No enabled Omnigent models have pricing information.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full min-w-[840px] text-left text-xs">
              <thead className="bg-muted/50 text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium" scope="col">
                    Enabled model
                  </th>
                  <th className="px-3 py-2 text-right font-medium" scope="col">
                    Input / 1M
                  </th>
                  <th className="px-3 py-2 text-right font-medium" scope="col">
                    Output / 1M
                  </th>
                  <th className="px-3 py-2 text-right font-medium" scope="col">
                    Cache read / 1M
                  </th>
                  <th className="px-3 py-2 text-right font-medium" scope="col">
                    Cache write / 1M
                  </th>
                  <th className="px-3 py-2 text-right font-medium" scope="col">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {pricing.map((row) => {
                  const editing = editingModel === row.model && form != null;
                  const serviceUnknown = row.servicePricingStatus !== "known";
                  const displayRate = (value: number | null) =>
                    serviceUnknown && !row.hasCustomPricing ? "Unknown" : formatRate(value);
                  return (
                    <tr key={row.model} data-testid={`pricing-row-${row.model}`}>
                      <th className="px-3 py-2 font-medium" scope="row">
                        <span className="block">{row.displayName}</span>
                        {serviceUnknown && (
                          <span className="text-10 font-normal text-muted-foreground">
                            Service price: Unknown
                          </span>
                        )}
                      </th>
                      {editing ? (
                        <>
                          {(
                            [
                              ["input", "Input"],
                              ["output", "Output"],
                              ["cacheRead", "Cache read"],
                              ["cacheWrite", "Cache write"],
                            ] as const
                          ).map(([field, label]) => (
                            <td className="px-1.5 py-2" key={field}>
                              <Input
                                type="number"
                                min="0"
                                step="any"
                                aria-label={`${label} price per 1M for ${row.displayName}`}
                                value={form[field]}
                                disabled={pending}
                                onChange={(event) => setField(field, event.target.value)}
                                className="min-w-28 text-right tabular-nums"
                              />
                            </td>
                          ))}
                          <td className="px-3 py-2 text-right">
                            <div className="flex justify-end gap-1">
                              <Button size="sm" disabled={pending} onClick={() => save(row.model)}>
                                {updatePricing.isPending ? "Saving…" : "Save"}
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                disabled={pending}
                                onClick={cancelEdit}
                              >
                                Cancel
                              </Button>
                            </div>
                            {(validationError || mutationError) && (
                              <p className="mt-1 text-right text-destructive" role="alert">
                                {validationError ?? "Could not save pricing. Try again."}
                              </p>
                            )}
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {displayRate(row.inputPerMillion)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {displayRate(row.outputPerMillion)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {displayRate(row.cacheReadPerMillion)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums">
                            {displayRate(row.cacheWritePerMillion)}
                          </td>
                          <td className="px-3 py-2 text-right">
                            <div className="flex justify-end gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                disabled={pending}
                                onClick={() => beginEdit(row)}
                              >
                                Edit
                              </Button>
                              {row.hasCustomPricing &&
                                (serviceUnknown ? (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    disabled={pending}
                                    onClick={() => clear(row.model)}
                                  >
                                    {clearPricing.isPending ? "Clearing…" : "Clear custom pricing"}
                                  </Button>
                                ) : (
                                  row.customDiffersFromService && (
                                    <Button
                                      variant="outline"
                                      size="sm"
                                      disabled={pending}
                                      onClick={() => clear(row.model)}
                                    >
                                      {clearPricing.isPending
                                        ? "Resetting…"
                                        : "Reset to pulled pricing"}
                                    </Button>
                                  )
                                ))}
                            </div>
                            {mutationError && (
                              <p className="mt-1 text-right text-destructive" role="alert">
                                Could not update pricing. Try again.
                              </p>
                            )}
                          </td>
                        </>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export function StatisticsPage() {
  const [month, setMonth] = useState(currentMonth);
  const report = useStatisticsReport(month);
  const modelSettings = useAdminModelSettings();
  const updateModelSettings = useUpdateAdminModelSettings();
  const data = report.data;
  const workloadEnabled =
    modelSettings.data?.workloadClassificationEnabled ??
    data?.workloadClassificationEnabled ??
    false;
  const months = useMemo(
    () => Array.from(new Set([...(data?.availableMonths ?? []), month])).sort(),
    [data?.availableMonths, month],
  );
  const monthIndex = months.indexOf(month);
  const previousMonth = monthIndex > 0 ? months[monthIndex - 1] : null;
  const nextMonth =
    monthIndex >= 0 && monthIndex < months.length - 1 ? months[monthIndex + 1] : null;
  const hasActivity =
    data != null &&
    (data.totals.userTurns > 0 ||
      data.totals.totalTokens > 0 ||
      data.daily.length > 0 ||
      data.byModel.length > 0 ||
      data.byPurpose.length > 0 ||
      data.byWorkload.length > 0);

  return (
    <PageScroll
      maxWidthClassName="max-w-6xl"
      contentClassName="px-4 sm:px-6"
      data-testid="statistics-page"
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">Omnigent statistics</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Monthly cost and token consumption for OmniHarness only.
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Previous available month"
            disabled={!previousMonth}
            onClick={() => previousMonth && setMonth(previousMonth)}
          >
            <ChevronLeftIcon />
          </Button>
          <label className="sr-only" htmlFor="statistics-month">
            Statistics month
          </label>
          <select
            id="statistics-month"
            data-testid="statistics-month-select"
            className="h-8 rounded-lg border border-input bg-background px-2.5 text-ui outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            value={month}
            onChange={(event) => setMonth(event.target.value)}
          >
            {months.map((availableMonth) => (
              <option key={availableMonth} value={availableMonth}>
                {monthLabel(availableMonth)}
              </option>
            ))}
          </select>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Next available month"
            disabled={!nextMonth}
            onClick={() => nextMonth && setMonth(nextMonth)}
          >
            <ChevronRightIcon />
          </Button>
        </div>
      </div>

      {report.isLoading && (
        <div
          className="flex h-64 items-center justify-center"
          aria-label="Loading Omnigent statistics"
          data-testid="statistics-loading"
        >
          <Loader2Icon className="size-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {report.isError && (
        <div
          role="alert"
          className="flex h-64 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
          data-testid="statistics-error"
        >
          <AlertTriangleIcon className="size-5" />
          <p>Failed to load Omnigent statistics.</p>
          <Button variant="outline" size="sm" onClick={() => void report.refetch()}>
            Retry
          </Button>
        </div>
      )}

      {data && (
        <div className="mt-7 space-y-8">
          <div className="flex justify-end">
            <PricingDialog pricing={data.enabledModelPricing} month={month} />
          </div>

          {!hasActivity && (
            <div
              className="flex min-h-40 flex-col items-center justify-center rounded-xl border border-dashed border-border px-6 text-center"
              data-testid="statistics-empty"
            >
              <BarChart3Icon className="size-6 text-muted-foreground" />
              <h2 className="mt-3 text-base font-medium">No Omnigent activity this month</h2>
              <p className="mt-1 max-w-md text-sm text-muted-foreground">
                Statistics will appear after OmniHarness completes model calls.
              </p>
            </div>
          )}
          <section aria-labelledby="statistics-overview-heading">
            <h2 id="statistics-overview-heading" className="sr-only">
              Monthly totals
            </h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["Total cost", formatCost(data.totals.costUsd)],
                ["Total tokens", formatTokenCount(data.totals.totalTokens)],
                ["User turns", data.totals.userTurns.toLocaleString()],
                ["Average cost / turn", formatCost(data.totals.averageCostPerTurn)],
              ].map(([label, value]) => (
                <div key={label} className="rounded-xl border border-border bg-card p-4">
                  <p className="text-xs text-muted-foreground">{label}</p>
                  <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="statistics-daily-heading">
            <h2 id="statistics-daily-heading" className="text-base font-semibold">
              Daily consumption
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Estimated Omnigent cost by day in {monthLabel(data.month)}.
            </p>
            {data.daily.length === 0 ? (
              <div className="mt-4 flex h-48 items-center justify-center rounded-lg border border-dashed border-border text-sm text-muted-foreground">
                No daily consumption data for this month.
              </div>
            ) : (
              <div className="mt-4">
                <DailyChart report={data} />
              </div>
            )}
          </section>

          <BreakdownSection
            id="model"
            title="Cost by Model"
            description="Which enabled Omnigent models generated consumption."
            rows={data.byModel}
          />
          <BreakdownSection
            id="purpose"
            title="Cost by Purpose"
            description="Why each request was made. Consolidated calls keep their combined purposes together."
            rows={data.byPurpose}
          />
          <BreakdownSection
            id="workload"
            title="Cost by Workload"
            description="What kind of user work drove each Omnigent turn."
            rows={data.byWorkload}
            workloadControl={
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-muted-foreground">
                  {workloadEnabled ? "Monitoring on" : "Monitoring off"}
                </span>
                <Switch
                  aria-label="Enable workload monitoring"
                  data-testid="workload-monitoring-toggle"
                  checked={workloadEnabled}
                  disabled={modelSettings.isLoading || updateModelSettings.isPending}
                  onCheckedChange={(checked) =>
                    updateModelSettings.mutate({ workloadClassificationEnabled: checked })
                  }
                />
              </div>
            }
            disabledGuidance={
              <div
                className="mt-3 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
                data-testid="workload-monitoring-guidance"
              >
                <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
                <p>
                  Workload monitoring uses an agent call and consumes tokens and cost.{" "}
                  {!workloadEnabled &&
                    (data.byWorkload.length > 0
                      ? "Monitoring is currently off; historical classifications for this month remain visible."
                      : "Monitoring is off, so new turns remain unclassified.")}
                  {modelSettings.isError &&
                    " The current monitoring setting could not be loaded; retry from Models settings."}
                </p>
              </div>
            }
          />
        </div>
      )}
    </PageScroll>
  );
}
