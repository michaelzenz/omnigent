import { useId, useState } from "react";
import { PlusIcon, Trash2Icon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { ConfirmDialog } from "@/globalhub/ConfirmDialog";
import { OpenSessionLink } from "@/globalhub/OpenSessionLink";
import {
  type WatchItem,
  type Watcher,
  type WatcherChanges,
  type WatcherDefaults,
  type WatcherDetail,
  useCreateWatcher,
  useDeleteWatcher,
  useSaveWatcher,
  useWatchItems,
  useWatcher,
  useWatcherDefaults,
  useWatchers,
} from "@/hooks/useGlobalHubWatchers";
import { absoluteTime, relativeTime } from "@/lib/relativeTime";
import { localTimezone } from "@/lib/timezones";
import {
  CollapsibleRoleCard,
  ManualRow,
  RoleModelSelect,
  SavedStatus,
  SettingRow,
} from "./RoleFields";
import {
  RoleScheduleField,
  type RoleScheduleDraft,
  scheduleChange,
  scheduleDraft,
  scheduleError,
  scheduleSettings,
} from "./RoleScheduleField";

const NAME_MAX_CHARS = 200;
const SCHEDULE_HINT =
  "When the watcher runs. Runs come at least 10 minutes apart; turn the schedule off to pause the watcher.";
const MANUAL_HINT =
  "What the watcher checks and how it reports. Sent with every turn of the watcher's session.";

/** The user's watchers: role sessions that run on a schedule and report what changed. */
export function WatchersSection() {
  const watchers = useWatchers();
  const [adding, setAdding] = useState(false);
  return (
    <section className="flex flex-col gap-4" data-testid="glossaries-watchers">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-lg font-semibold">Watchers</h2>
          <p className="text-sm text-muted-foreground">
            A watcher runs on its schedule, checks the items that its manual names, and reports each
            change as an event.
          </p>
        </div>
        <Button size="sm" disabled={adding} onClick={() => setAdding(true)}>
          <PlusIcon aria-hidden />
          Add watcher
        </Button>
      </div>
      {adding && <NewWatcherCard onDone={() => setAdding(false)} />}
      {watchers.isPending ? (
        <div className="flex justify-center p-4">
          <Spinner className="size-5 text-muted-foreground" aria-label="Loading watchers" />
        </div>
      ) : watchers.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load the watchers: {watchers.error.message}
        </p>
      ) : watchers.data.length === 0 ? (
        !adding && <p className="text-sm text-muted-foreground">No watchers yet.</p>
      ) : (
        watchers.data.map((watcher) => <WatcherCard key={watcher.id} watcher={watcher} />)
      )}
    </section>
  );
}

function NewWatcherCard({ onDone }: { onDone: () => void }) {
  const defaults = useWatcherDefaults();
  return (
    <section
      className="space-y-4 rounded-xl border border-border bg-card p-4"
      data-testid="new-watcher-card"
    >
      <h3 className="text-base font-semibold">New watcher</h3>
      {defaults.data ? (
        <NewWatcherForm defaults={defaults.data} onDone={onDone} />
      ) : defaults.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load the watcher template: {defaults.error.message}
        </p>
      ) : (
        <Spinner className="size-4 text-muted-foreground" aria-label="Loading the template" />
      )}
    </section>
  );
}

function NewWatcherForm({ defaults, onDone }: { defaults: WatcherDefaults; onDone: () => void }) {
  const create = useCreateWatcher();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [model, setModel] = useState<string | null>(null);
  const [manual, setManual] = useState(defaults.manual);
  const [schedule, setSchedule] = useState(() =>
    scheduleDraft({
      enabled: true,
      rrule: defaults.rrule,
      timezone: localTimezone(),
      next_run_at: null,
    }),
  );
  const ids = useId();
  const invalid = name.trim() === "" || scheduleError(schedule) !== null;
  return (
    <div className="space-y-4">
      <WatcherFields
        ids={ids}
        name={name}
        description={description}
        model={model}
        schedule={schedule}
        saved={null}
        onName={setName}
        onDescription={setDescription}
        onModel={setModel}
        onSchedule={setSchedule}
      />
      <ManualRow
        id={`${ids}-manual`}
        value={manual}
        defaultValue={defaults.manual}
        customized={false}
        hint={MANUAL_HINT}
        onChange={setManual}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          disabled={invalid}
          loading={create.isPending}
          onClick={() =>
            create.mutate(
              {
                name: name.trim(),
                description,
                model,
                manual: manual === defaults.manual ? null : manual,
                schedule: scheduleSettings(schedule),
              },
              { onSuccess: onDone },
            )
          }
        >
          Create watcher
        </Button>
        <Button size="sm" variant="outline" disabled={create.isPending} onClick={onDone}>
          Cancel
        </Button>
      </div>
      {create.error && (
        <p role="alert" className="text-xs text-destructive">
          {create.error.message}
        </p>
      )}
    </div>
  );
}

function WatcherCard({ watcher }: { watcher: Watcher }) {
  const remove = useDeleteWatcher();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  return (
    <>
      <CollapsibleRoleCard
        testId={`watcher-card-${watcher.id}`}
        title={watcher.name}
        badges={
          watcher.builtin && (
            <Badge variant="secondary" title="Built in: checks the source types no watcher covers">
              Built-in
            </Badge>
          )
        }
        actions={
          <>
            {watcher.session_id && (
              <OpenSessionLink
                sessionId={watcher.session_id}
                label={`Open the ${watcher.name} session`}
              />
            )}
            {!watcher.builtin && (
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Delete watcher ${watcher.name}`}
                title="Delete the watcher and its session"
                onClick={() => setConfirmingDelete(true)}
              >
                <Trash2Icon className="text-destructive" aria-hidden />
              </Button>
            )}
          </>
        }
        summary={
          <>
            {watcher.description && (
              <p className="truncate text-sm text-muted-foreground">{watcher.description}</p>
            )}
            <SourceTypes watcher={watcher} />
          </>
        }
      >
        <WatcherSettings watcherId={watcher.id} />
      </CollapsibleRoleCard>
      {confirmingDelete && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setConfirmingDelete(false);
          }}
          title={`Delete watcher ${watcher.name}?`}
          description="The watcher, its schedule and its tracked items are deleted, and so is its session. This cannot be undone."
          confirmLabel="Delete watcher"
          destructive
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() =>
            remove.mutate(watcher.id, { onSuccess: () => setConfirmingDelete(false) })
          }
        />
      )}
    </>
  );
}

/** The watcher's form and tracked items, loaded when its card first opens. */
function WatcherSettings({ watcherId }: { watcherId: string }) {
  const detail = useWatcher(watcherId);
  const save = useSaveWatcher(watcherId);
  // A new form starts from the saved watcher after each save or cancel.
  const [formVersion, setFormVersion] = useState(0);
  return (
    <>
      {detail.data ? (
        <WatcherForm
          key={formVersion}
          watcher={detail.data}
          save={save}
          onDone={() => setFormVersion((version) => version + 1)}
        />
      ) : detail.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load the watcher: {detail.error.message}
        </p>
      ) : (
        <Spinner className="size-4 text-muted-foreground" aria-label="Loading the watcher" />
      )}
      <WatchItems watcherId={watcherId} />
    </>
  );
}

/** The source types the watcher checks for the sessions that follow sources. */
function SourceTypes({ watcher }: { watcher: Watcher }) {
  const label = watcher.builtin ? "Checks types without a watcher" : "Source types";
  return (
    <div
      className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground"
      data-testid={`watcher-source-types-${watcher.id}`}
    >
      <span>{label}:</span>
      {watcher.source_types.length === 0 ? (
        <span>none</span>
      ) : (
        watcher.source_types.map((sourceType) => (
          <Badge key={sourceType} variant="outline" className="font-mono">
            {sourceType}
          </Badge>
        ))
      )}
    </div>
  );
}

/** The changes to save: a manual equal to the template saves as ``null``, which resets it. */
function changesFrom(
  watcher: WatcherDetail,
  draft: {
    name: string;
    description: string;
    model: string | null;
    manual: string;
    schedule: RoleScheduleDraft;
  },
): WatcherChanges {
  const changes: WatcherChanges = {};
  const name = draft.name.trim();
  if (name !== watcher.name) changes.name = name;
  if (draft.description !== watcher.description) changes.description = draft.description;
  if (draft.model !== watcher.model) changes.model = draft.model;
  if (draft.manual !== watcher.manual) {
    changes.manual = draft.manual === watcher.default_manual ? null : draft.manual;
  }
  const schedule = scheduleChange(watcher.schedule, draft.schedule);
  if (schedule) changes.schedule = schedule;
  return changes;
}

function WatcherForm({
  watcher,
  save,
  onDone,
}: {
  watcher: WatcherDetail;
  save: ReturnType<typeof useSaveWatcher>;
  /** Start a new form from the saved watcher. */
  onDone: () => void;
}) {
  const [name, setName] = useState(watcher.name);
  const [description, setDescription] = useState(watcher.description);
  const [model, setModel] = useState(watcher.model);
  const [manual, setManual] = useState(watcher.manual);
  const [schedule, setSchedule] = useState(() => scheduleDraft(watcher.schedule));
  const changes = changesFrom(watcher, { name, description, model, manual, schedule });
  const changed = Object.keys(changes).length > 0;
  const invalid = name.trim() === "" || scheduleError(schedule) !== null;
  const ids = useId();
  return (
    <div className="space-y-4">
      <WatcherFields
        ids={ids}
        name={name}
        description={description}
        model={model}
        schedule={schedule}
        saved={watcher}
        onName={setName}
        onDescription={setDescription}
        onModel={setModel}
        onSchedule={setSchedule}
      />
      <ManualRow
        id={`${ids}-manual`}
        value={manual}
        defaultValue={watcher.default_manual}
        customized={watcher.customized.includes("manual")}
        hint={MANUAL_HINT}
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

/** The name, description, model and schedule rows that the new and the saved watcher share. */
function WatcherFields({
  ids,
  name,
  description,
  model,
  schedule,
  saved,
  onName,
  onDescription,
  onModel,
  onSchedule,
}: {
  ids: string;
  name: string;
  description: string;
  model: string | null;
  schedule: RoleScheduleDraft;
  /** The saved watcher, or ``null`` for a new one. */
  saved: WatcherDetail | null;
  onName: (name: string) => void;
  onDescription: (description: string) => void;
  onModel: (model: string | null) => void;
  onSchedule: (schedule: RoleScheduleDraft) => void;
}) {
  return (
    <>
      <SettingRow id={`${ids}-name`} label="Name" customized={false}>
        <Input
          id={`${ids}-name`}
          value={name}
          maxLength={NAME_MAX_CHARS}
          placeholder="e.g. Pull request watcher"
          onChange={(event) => onName(event.target.value)}
        />
      </SettingRow>
      <SettingRow id={`${ids}-description`} label="Description" customized={false}>
        <Textarea
          id={`${ids}-description`}
          rows={2}
          value={description}
          onChange={(event) => onDescription(event.target.value)}
        />
      </SettingRow>
      <SettingRow
        id={`${ids}-model`}
        label="Model"
        customized={saved !== null && saved.model !== null}
        hint="Saving a new model also switches the watcher's running session."
        onReset={model === null ? undefined : () => onModel(null)}
      >
        <RoleModelSelect id={`${ids}-model`} value={model} onChange={onModel} />
      </SettingRow>
      <SettingRow id={`${ids}-schedule`} label="Schedule" customized={false} hint={SCHEDULE_HINT}>
        <RoleScheduleField
          id={`${ids}-schedule`}
          draft={schedule}
          saved={saved?.schedule ?? null}
          onChange={onSchedule}
        />
      </SettingRow>
    </>
  );
}

/** A watcher's tracked items in pages, the active ones first and the done ones on request. */
function WatchItems({ watcherId }: { watcherId: string }) {
  const [withDone, setWithDone] = useState(false);
  const items = useWatchItems(watcherId, withDone);
  const ids = useId();
  const pages = items.data?.pages ?? [];
  const rows = pages.flatMap((page) => page.data);
  const total = pages[0]?.total;
  return (
    <div className="space-y-2" data-testid={`watch-items-${watcherId}`}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">Tracked items</h4>
        {total !== undefined && <Badge variant="outline">{total}</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <label htmlFor={`${ids}-done`} className="text-xs text-muted-foreground">
            Show done items
          </label>
          <Switch id={`${ids}-done`} checked={withDone} onCheckedChange={setWithDone} />
        </div>
      </div>
      {items.isPending ? (
        <Spinner className="size-4 text-muted-foreground" aria-label="Loading tracked items" />
      ) : items.isError && rows.length === 0 ? (
        <p role="alert" className="text-xs text-destructive">
          Could not load the tracked items: {items.error.message}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">
          Nothing tracked yet. The watcher adds items on its runs.
        </p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {rows.map((item) => (
            <WatchItemRow key={item.id} item={item} />
          ))}
        </ul>
      )}
      {items.hasNextPage && (
        <Button
          size="sm"
          variant="outline"
          loading={items.isFetchingNextPage}
          onClick={() => void items.fetchNextPage()}
        >
          Load more
        </Button>
      )}
      {items.isFetchNextPageError && (
        <p role="alert" className="text-xs text-destructive">
          Could not load more items: {items.error?.message}
        </p>
      )}
    </div>
  );
}

function WatchItemRow({ item }: { item: WatchItem }) {
  const label = item.title || item.source_key;
  const link = /^https?:\/\//.test(item.source_key) ? item.source_key : null;
  const checkedMs = item.checked_at * 1000;
  const checkedAgo = relativeTime(checkedMs);
  const offsets = Object.entries(item.offsets);
  return (
    <li className="space-y-1 px-3 py-2" data-testid={`watch-item-${item.id}`}>
      <div className="flex items-center gap-2">
        {link ? (
          <a
            href={link}
            target="_blank"
            rel="noreferrer"
            className="min-w-0 truncate text-sm font-medium hover:underline"
          >
            {label}
          </a>
        ) : (
          <span className="min-w-0 truncate text-sm font-medium">{label}</span>
        )}
        <Badge variant="secondary">{item.source}</Badge>
        {item.status === "done" && <Badge variant="outline">Done</Badge>}
        <span
          className="ml-auto shrink-0 text-xs text-muted-foreground"
          title={absoluteTime(checkedMs)}
        >
          {checkedAgo === "now" ? "Checked now" : `Checked ${checkedAgo} ago`}
        </span>
      </div>
      {offsets.length > 0 && (
        <p className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          {offsets.map(([name, value]) => (
            <span key={name}>
              {name}: {value}
            </span>
          ))}
        </p>
      )}
    </li>
  );
}
