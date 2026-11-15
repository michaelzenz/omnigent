import { ScheduleFields } from "@/components/scheduled/ScheduleFields";
import { Switch } from "@/components/ui/switch";
import type { RoleSchedule, RoleScheduleSettings } from "@/hooks/useGlobalHubRoleProfiles";
import {
  buildRRule,
  DEFAULT_SCHEDULE_MODEL,
  parseRRuleToScheduleModel,
  type ScheduleModel,
  validateSchedule,
} from "@/lib/scheduleBuilder";
import { formatNextRunAt } from "@/lib/scheduleText";
import { localTimezone } from "@/lib/timezones";

// A role session continues across its runs, so main's minimum for continued
// sessions applies: runs at least 10 minutes apart.
const ROLE_SCHEDULE_LIMITS = { sessionMode: "continue", subhourSupported: true } as const;

/** The editor's state for a role's schedule. */
export interface RoleScheduleDraft {
  enabled: boolean;
  model: ScheduleModel;
  timezone: string;
}

/** The draft a saved schedule opens as; without one, off, every day at 09:00 in the browser's timezone. */
export function scheduleDraft(saved: RoleSchedule | null): RoleScheduleDraft {
  return {
    enabled: saved?.enabled ?? false,
    model: (saved && parseRRuleToScheduleModel(saved.rrule)) ?? DEFAULT_SCHEDULE_MODEL,
    timezone: saved?.timezone ?? localTimezone(),
  };
}

/**
 * The schedule to save, or `undefined` when the draft changes nothing. A rule
 * the editor did not change keeps its saved text, and an unsaved schedule
 * saves only once it is turned on.
 */
export function scheduleChange(
  saved: RoleSchedule | null,
  draft: RoleScheduleDraft,
): RoleScheduleSettings | undefined {
  const initial = scheduleDraft(saved);
  const rrule = buildRRule(draft.model);
  const ruleChanged = rrule !== buildRRule(initial.model);
  if (!ruleChanged && draft.enabled === initial.enabled && draft.timezone === initial.timezone) {
    return undefined;
  }
  if (saved === null && !draft.enabled) return undefined;
  return {
    enabled: draft.enabled,
    rrule: ruleChanged || saved === null ? rrule : saved.rrule,
    timezone: draft.timezone,
  };
}

/** Why the draft cannot be saved, or `null`; a schedule that is off always saves. */
export function scheduleError(draft: RoleScheduleDraft): string | null {
  return draft.enabled ? validateSchedule(draft.model, ROLE_SCHEDULE_LIMITS) : null;
}

/** A switch for a role's scheduled runs and, while on, main's schedule editor without an end. */
export function RoleScheduleField({
  id,
  draft,
  saved,
  onChange,
}: {
  /** The switch's id, for the row's label. */
  id: string;
  draft: RoleScheduleDraft;
  /** The saved schedule, whose next run the field shows while it is on. */
  saved: RoleSchedule | null;
  onChange: (draft: RoleScheduleDraft) => void;
}) {
  const nextRun =
    saved?.next_run_at != null
      ? formatNextRunAt(new Date(saved.next_run_at * 1000).toISOString())
      : null;
  return (
    <div className="space-y-2">
      <Switch
        id={id}
        checked={draft.enabled}
        onCheckedChange={(enabled) => onChange({ ...draft, enabled })}
      />
      {draft.enabled && (
        <ScheduleFields
          model={draft.model}
          anchoredScheduling
          {...ROLE_SCHEDULE_LIMITS}
          onChange={(model) => onChange({ ...draft, model })}
          timezone={draft.timezone}
          onTimezoneChange={(timezone) => onChange({ ...draft, timezone })}
        >
          {({ recurrenceFields }) => recurrenceFields}
        </ScheduleFields>
      )}
      {nextRun && (
        <p className="text-xs text-muted-foreground" data-testid="role-schedule-next-run">
          Next run {nextRun}.
        </p>
      )}
    </div>
  );
}
