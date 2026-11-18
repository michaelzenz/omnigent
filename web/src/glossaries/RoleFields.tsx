import { type ReactNode, useState } from "react";
import { ChevronDownIcon, ChevronRightIcon } from "lucide-react";
import { MessageResponse } from "@/components/ai-elements/message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import { useHostModelOptions } from "@/hooks/useHosts";
import { nativeModelLabel } from "@/lib/composerModelLabel";

// The harness that the GlobalHub role agents run.
const ROLE_HARNESS = "pi";
const ROLE_DEFAULT_MODEL = "__role_default__";

/** A role's card: its title opens and closes the role's settings, which start closed. */
export function CollapsibleRoleCard({
  testId,
  title,
  badges,
  actions,
  summary,
  children,
}: {
  testId: string;
  title: string;
  /** Shown after the title, e.g. a "Built-in" badge. */
  badges?: ReactNode;
  /** Shown at the end of the header, e.g. a delete button. */
  actions?: ReactNode;
  /** Shown under the header, also while the card is closed. */
  summary?: ReactNode;
  /** The settings: rendered when the card first opens, then kept while it is closed. */
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [opened, setOpened] = useState(false);
  return (
    <Collapsible
      asChild
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) setOpened(true);
      }}
    >
      <section className="rounded-xl border border-border bg-card p-4" data-testid={testId}>
        <div className="flex items-center gap-2">
          <h3 className="min-w-0 text-base font-semibold">
            <CollapsibleTrigger className="flex max-w-full items-center gap-1.5 text-left">
              {open ? (
                <ChevronDownIcon className="size-4 shrink-0" aria-hidden />
              ) : (
                <ChevronRightIcon className="size-4 shrink-0" aria-hidden />
              )}
              <span className="truncate">{title}</span>
            </CollapsibleTrigger>
          </h3>
          {badges}
          <span className="flex-1" />
          {actions}
        </div>
        {summary && <div className="mt-1.5 space-y-1.5 pl-5.5">{summary}</div>}
        <CollapsibleContent forceMount hidden={!open} className="space-y-4 pt-4">
          {opened && children}
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

/** A labeled field of a role form, with an optional reset to its default. */
export function SettingRow({
  id,
  label,
  customized,
  hint,
  onReset,
  trailing,
  children,
}: {
  id: string;
  label: string;
  customized: boolean;
  hint?: string;
  /** Puts the default back in the field; absent while the field holds the default. */
  onReset?: () => void;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {customized && <Badge variant="outline">Customized</Badge>}
        <div className="ml-auto flex items-center gap-1">
          {trailing}
          {onReset && (
            <Button variant="ghost" size="sm" onClick={onReset}>
              Reset to default
            </Button>
          )}
        </div>
      </div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** The models the user's GlobalHub host offers for the role harness, and the role default. */
export function RoleModelSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string | null;
  onChange: (model: string | null) => void;
}) {
  const settings = useGlobalHubSettings();
  const hostId = settings.data?.host_id ?? null;
  const options = useHostModelOptions(hostId, ROLE_HARNESS, hostId !== null, { poll: false });
  const models = options.data ?? [];
  const listed = value === null || models.some((option) => option.id === value);
  return (
    <Select
      value={value ?? ROLE_DEFAULT_MODEL}
      onValueChange={(next) => onChange(next === ROLE_DEFAULT_MODEL ? null : next)}
    >
      <SelectTrigger id={id} className="w-full max-w-sm">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ROLE_DEFAULT_MODEL}>Role default</SelectItem>
        {!listed && value !== null && <SelectItem value={value}>{value}</SelectItem>}
        {models.map((option) => (
          <SelectItem key={option.id} value={option.id}>
            {nativeModelLabel(option)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** A role's manual in Markdown, with a preview and a reset to the default manual. */
export function ManualRow({
  id,
  value,
  defaultValue,
  customized,
  hint,
  onChange,
}: {
  id: string;
  value: string;
  defaultValue: string;
  customized: boolean;
  hint: string;
  onChange: (manual: string) => void;
}) {
  const [preview, setPreview] = useState(false);
  return (
    <SettingRow
      id={id}
      label="Manual"
      customized={customized}
      hint={hint}
      onReset={value === defaultValue ? undefined : () => onChange(defaultValue)}
      trailing={
        <Button
          variant="ghost"
          size="sm"
          aria-pressed={preview}
          onClick={() => setPreview((shown) => !shown)}
        >
          {preview ? "Edit" : "Preview"}
        </Button>
      }
    >
      {preview ? (
        <div className="max-h-96 overflow-auto rounded-md border border-border p-3 text-ui">
          <MessageResponse>{value}</MessageResponse>
        </div>
      ) : (
        <Textarea
          id={id}
          rows={16}
          className="font-mono text-xs"
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </SettingRow>
  );
}

/** What a save did: whether the running sessions took a new model. */
export function SavedStatus({
  switched,
  notSwitched,
}: {
  switched: number;
  notSwitched: string[];
}) {
  const sessions = (count: number) => `${count} running session${count === 1 ? "" : "s"}`;
  return (
    <p role="status" className="text-xs text-muted-foreground">
      Saved.
      {switched > 0 && ` ${sessions(switched)} switched to the new model.`}
      {notSwitched.length > 0 && ` Could not switch: ${notSwitched.join(", ")}.`}
    </p>
  );
}
