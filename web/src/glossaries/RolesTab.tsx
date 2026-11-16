import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
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
import { ManualRow, RoleModelSelect, SavedStatus, SettingRow } from "./RoleFields";
import {
  RoleScheduleField,
  type RoleScheduleDraft,
  scheduleChange,
  scheduleDraft,
  scheduleError,
} from "./RoleScheduleField";
import { WatchersSection } from "./WatchersSection";

/** The user's GlobalHub roles: the built-in roles, then the watchers that the user defines. */
export function RolesTab() {
  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-8" data-testid="glossaries-roles">
      <BuiltinRoles />
      <WatchersSection />
    </div>
  );
}

/** The broker, the manager and the custodian, each with its description, model and manual. */
function BuiltinRoles() {
  const profiles = useRoleProfiles();
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-semibold">Built-in roles</h2>
        <p className="text-sm text-muted-foreground">The roles that run your board.</p>
      </div>
      {profiles.isPending ? (
        <div className="flex justify-center p-8">
          <Spinner className="size-5 text-muted-foreground" aria-label="Loading roles" />
        </div>
      ) : profiles.isError ? (
        <p role="alert" className="p-8 text-center text-sm text-destructive">
          Could not load the roles: {profiles.error.message}
        </p>
      ) : (
        profiles.data.map((summary) => <RoleCard key={summary.role} summary={summary} />)
      )}
    </section>
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
      <h3 className="text-base font-semibold">{summary.title}</h3>
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
      <ManualRow
        id={`${ids}-manual`}
        value={manual}
        defaultValue={profile.default_manual}
        customized={customized("manual")}
        hint="Sent with every turn of the role's sessions, from the next turn."
        onChange={setManual}
      />
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
