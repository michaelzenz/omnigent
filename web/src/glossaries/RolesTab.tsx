import { type ReactNode, useId, useState } from "react";
import { MessageResponse } from "@/components/ai-elements/message";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import {
  type RoleField,
  type RoleProfile,
  type RoleProfileChanges,
  type RoleProfileSummary,
  useRoleProfile,
  useRoleProfiles,
  useSaveRoleProfile,
} from "@/hooks/useGlobalHubRoleProfiles";
import { useGlobalHubSettings } from "@/hooks/useGlobalHubSettings";
import { useHostModelOptions } from "@/hooks/useHosts";
import { nativeModelLabel } from "@/lib/composerModelLabel";
import {
  RoleScheduleField,
  type RoleScheduleDraft,
  scheduleChange,
  scheduleDraft,
  scheduleError,
} from "./RoleScheduleField";

// The harness that the GlobalHub role agents run.
const ROLE_HARNESS = "pi";
const ROLE_DEFAULT_MODEL = "__role_default__";

/** The user's GlobalHub roles, each with its description, model and manual. */
export function RolesTab() {
  const profiles = useRoleProfiles();
  if (profiles.isPending) {
    return (
      <div className="flex justify-center p-8">
        <Spinner className="size-5 text-muted-foreground" aria-label="Loading roles" />
      </div>
    );
  }
  if (profiles.isError) {
    return (
      <p role="alert" className="p-8 text-center text-sm text-destructive">
        Could not load the roles: {profiles.error.message}
      </p>
    );
  }
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4" data-testid="glossaries-roles">
      {profiles.data.map((summary) => (
        <RoleCard key={summary.role} summary={summary} />
      ))}
    </div>
  );
}

type RoleSave = ReturnType<typeof useSaveRoleProfile>;

function RoleCard({ summary }: { summary: RoleProfileSummary }) {
  const profile = useRoleProfile(summary.role);
  const save = useSaveRoleProfile(summary.role);
  // A new form starts from the saved profile after each save or cancel.
  const [formVersion, setFormVersion] = useState(0);
  return (
    <section
      className="space-y-4 rounded-xl border border-border bg-card p-4"
      data-testid={`role-card-${summary.role}`}
    >
      <h2 className="text-base font-semibold">{summary.title}</h2>
      {profile.data ? (
        <RoleForm
          key={formVersion}
          profile={profile.data}
          save={save}
          onDone={() => setFormVersion((version) => version + 1)}
        />
      ) : profile.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load the role: {profile.error.message}
        </p>
      ) : (
        <Spinner className="size-4 text-muted-foreground" aria-label="Loading the role" />
      )}
    </section>
  );
}

/** The changes to save: a value equal to its default saves as ``null``, which resets it. */
function changesFrom(
  profile: RoleProfile,
  draft: {
    description: string;
    model: string | null;
    manual: string;
    schedule: RoleScheduleDraft;
  },
): RoleProfileChanges {
  const changes: RoleProfileChanges = {};
  if (draft.description !== profile.description) {
    changes.description =
      draft.description === profile.default_description ? null : draft.description;
  }
  if (draft.model !== profile.model) changes.model = draft.model;
  if (draft.manual !== profile.manual) {
    changes.manual = draft.manual === profile.default_manual ? null : draft.manual;
  }
  const schedule = profile.schedulable
    ? scheduleChange(profile.schedule, draft.schedule)
    : undefined;
  if (schedule) changes.schedule = schedule;
  return changes;
}

function RoleForm({
  profile,
  save,
  onDone,
}: {
  profile: RoleProfile;
  save: RoleSave;
  /** Start a new form from the saved profile. */
  onDone: () => void;
}) {
  const [description, setDescription] = useState(profile.description);
  const [model, setModel] = useState(profile.model);
  const [manual, setManual] = useState(profile.manual);
  const [schedule, setSchedule] = useState(() => scheduleDraft(profile.schedule));
  const [previewManual, setPreviewManual] = useState(false);
  const changes = changesFrom(profile, { description, model, manual, schedule });
  const changed = Object.keys(changes).length > 0;
  const invalid = profile.schedulable && scheduleError(schedule) !== null;
  const ids = useId();
  const customized = (field: RoleField) => profile.customized.includes(field);

  return (
    <div className="space-y-4">
      <SettingRow
        id={`${ids}-description`}
        label="Description"
        customized={customized("description")}
        onReset={
          description === profile.default_description
            ? undefined
            : () => setDescription(profile.default_description)
        }
      >
        <Textarea
          id={`${ids}-description`}
          rows={2}
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </SettingRow>
      <SettingRow
        id={`${ids}-model`}
        label="Model"
        customized={customized("model")}
        hint="Saving a new model also switches the role's running sessions."
        onReset={model === null ? undefined : () => setModel(null)}
      >
        <RoleModelSelect id={`${ids}-model`} value={model} onChange={setModel} />
      </SettingRow>
      {profile.schedulable && (
        <SettingRow
          id={`${ids}-schedule`}
          label="Schedule"
          customized={false}
          hint="When the role's session gets its scheduled run, e.g. the custodian's session cleanup. Runs come at least 10 minutes apart."
        >
          <RoleScheduleField
            id={`${ids}-schedule`}
            draft={schedule}
            saved={profile.schedule}
            onChange={setSchedule}
          />
        </SettingRow>
      )}
      <SettingRow
        id={`${ids}-manual`}
        label="Manual"
        customized={customized("manual")}
        hint="Sent with every turn of the role's sessions, from the next turn."
        onReset={
          manual === profile.default_manual ? undefined : () => setManual(profile.default_manual)
        }
        trailing={
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={previewManual}
            onClick={() => setPreviewManual((value) => !value)}
          >
            {previewManual ? "Edit" : "Preview"}
          </Button>
        }
      >
        {previewManual ? (
          <div className="max-h-96 overflow-auto rounded-md border border-border p-3 text-ui">
            <MessageResponse>{manual}</MessageResponse>
          </div>
        ) : (
          <Textarea
            id={`${ids}-manual`}
            rows={16}
            className="font-mono text-xs"
            value={manual}
            onChange={(event) => setManual(event.target.value)}
          />
        )}
      </SettingRow>
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={!changed || invalid}
          loading={save.isPending}
          onClick={() => save.mutate(changes, { onSuccess: onDone })}
        >
          Save
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!changed || save.isPending}
          onClick={() => {
            save.reset();
            onDone();
          }}
        >
          Cancel
        </Button>
        {save.data && (
          <SavedStatus switched={save.data.switched} notSwitched={save.data.notSwitched} />
        )}
      </div>
      {save.error && (
        <p role="alert" className="text-xs text-destructive">
          {save.error.message}
        </p>
      )}
    </div>
  );
}

function SettingRow({
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
function RoleModelSelect({
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

function SavedStatus({ switched, notSwitched }: { switched: number; notSwitched: string[] }) {
  const sessions = (count: number) => `${count} running session${count === 1 ? "" : "s"}`;
  return (
    <p role="status" className="text-xs text-muted-foreground">
      Saved.
      {switched > 0 && ` ${sessions(switched)} switched to the new model.`}
      {notSwitched.length > 0 && ` Could not switch: ${notSwitched.join(", ")}.`}
    </p>
  );
}
