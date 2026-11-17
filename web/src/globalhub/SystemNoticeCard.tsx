import { useEffect, useRef, useState } from "react";
import { XIcon } from "lucide-react";
import { MessageResponse } from "@/components/ai-elements/message";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  markSystemNoticeSeen,
  type SystemNotice,
  type SystemNoticeAction,
  type SystemNoticeInput,
  type SystemNoticeValues,
  useClickSystemNoticeAction,
  useDismissSystemNotice,
  useSystemNotice,
} from "@/hooks/useGlobalHubNotice";
import { BOARD_COLUMN_CLASS } from "./boardLayout";
import { HtmlNoticeBody } from "./HtmlNoticeBody";

/** The notice pinned at the top of the board, e.g. the custodian's cleanup proposal. */
export function SystemNoticeCard() {
  const notice = useSystemNotice().data ?? null;
  const dismiss = useDismissSystemNotice();
  const noticeId = notice?.id ?? null;
  useEffect(() => {
    if (noticeId) markSystemNoticeSeen(noticeId);
  }, [noticeId]);

  if (!notice) return null;
  return (
    <div className={`${BOARD_COLUMN_CLASS} px-4 pt-4`}>
      <article
        className="rounded-xl border border-border bg-card shadow-sm"
        data-testid="system-notice-card"
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0 border-l-2 border-primary/50 pl-2.5">
            <p className="text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              System notice
            </p>
            <h2 className="text-base leading-snug font-semibold">{notice.title}</h2>
            {notice.publisher_label && (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">
                Published by {notice.publisher_label}
              </p>
            )}
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Dismiss system notice"
            title="Dismiss"
            disabled={dismiss.isPending}
            onClick={() => dismiss.mutate()}
          >
            <XIcon aria-hidden />
          </Button>
        </header>
        <div className="space-y-3 p-4">
          {dismiss.error && (
            <p role="alert" className="text-xs text-destructive">
              {dismiss.error.message}
            </p>
          )}
          {notice.body_kind === "html" && notice.body ? (
            // A new notice gets a fresh frame and forgets the clicks already sent.
            <HtmlNotice key={notice.id} notice={notice} html={notice.body} />
          ) : (
            <>
              {notice.body && (
                <div className="text-ui break-words">
                  <MessageResponse allowGitHubAttachmentImages>{notice.body}</MessageResponse>
                </div>
              )}
              {notice.actions.map((action) => (
                // A new notice gets fresh forms, so its buttons start enabled.
                <ActionForm key={`${notice.id}:${action.id}`} action={action} />
              ))}
            </>
          )}
        </div>
      </article>
    </div>
  );
}

/** An HTML notice: its buttons are in the frame, and each action is sent once. */
function HtmlNotice({ notice, html }: { notice: SystemNotice; html: string }) {
  const click = useClickSystemNoticeAction();
  const [sentIds, setSentIds] = useState<string[]>([]);
  // The actions sent or being sent; the frame can relay a click again before a render.
  const handled = useRef(new Set<string>());
  const labels = new Map(notice.actions.map((action) => [action.id, action.label ?? action.id]));
  const send = (actionId: string, inputs: SystemNoticeValues) => {
    if (handled.current.has(actionId)) return;
    handled.current.add(actionId);
    void click.mutateAsync({ actionId, inputs }).then(
      () => setSentIds((ids) => [...ids, actionId]),
      () => handled.current.delete(actionId),
    );
  };

  return (
    <div className="space-y-2">
      <HtmlNoticeBody html={html} actionIds={new Set(labels.keys())} onAction={send} />
      {labels.size > 0 && (
        <p className="text-xs text-muted-foreground">
          Buttons inside the notice send their action to the custodian.
        </p>
      )}
      {sentIds.map((actionId) => (
        <p key={actionId} role="status" className="text-xs text-muted-foreground">
          Sent to the custodian: {labels.get(actionId)}.
        </p>
      ))}
      {click.error && (
        <p role="alert" className="text-xs text-destructive">
          {click.error.message}
        </p>
      )}
    </div>
  );
}

/** One action as a form: its inputs, a switch per toggle group, and its button. */
function ActionForm({ action }: { action: SystemNoticeAction }) {
  const click = useClickSystemNoticeAction();
  const [values, setValues] = useState(() => initialValues(action));
  const change = (changes: SystemNoticeValues) =>
    setValues((current) => ({ ...current, ...changes }));
  const label = action.label ?? action.id;
  const sections = inputSections(action.inputs);
  const allToggles = toggleIds(action.inputs);
  // One group holding every toggle already has a switch for all of them.
  const oneGroupHoldsAll = sections.some(
    ({ group, inputs }) => group !== null && toggleIds(inputs).length === allToggles.length,
  );

  return (
    <section
      className="space-y-3 rounded-lg border border-border bg-muted/20 p-3"
      data-testid={`notice-action-${action.id}`}
    >
      {!oneGroupHoldsAll && (
        <SwitchAll name={`All of ${label}`} ids={allToggles} values={values} onChange={change} />
      )}
      {sections.map(({ group, inputs }) => (
        <div key={group ?? ""} className="space-y-2">
          {group !== null && (
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
                {group}
              </span>
              <SwitchAll
                name={`All in ${group}`}
                ids={toggleIds(inputs)}
                values={values}
                onChange={change}
              />
            </div>
          )}
          {inputs.map((input) => (
            <NoticeInputField
              key={input.id}
              input={input}
              value={values[input.id]}
              onChange={(value) => change({ [input.id]: value })}
            />
          ))}
        </div>
      ))}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          loading={click.isPending}
          disabled={click.isSuccess}
          onClick={() => click.mutate({ actionId: action.id, inputs: values })}
        >
          {label}
        </Button>
        {click.isSuccess && (
          <p role="status" className="text-xs text-muted-foreground">
            Sent to the custodian.
          </p>
        )}
      </div>
      {click.error && (
        <p role="alert" className="text-xs text-destructive">
          {click.error.message}
        </p>
      )}
    </section>
  );
}

/** A switch for two or more toggles: on while all of them are on; flipping it sets them all. */
function SwitchAll({
  name,
  ids,
  values,
  onChange,
}: {
  name: string;
  ids: string[];
  values: SystemNoticeValues;
  onChange: (changes: SystemNoticeValues) => void;
}) {
  if (ids.length < 2) return null;
  return (
    <label className="flex items-center gap-2 text-xs text-muted-foreground">
      <Switch
        aria-label={name}
        checked={ids.every((id) => values[id] === true)}
        onCheckedChange={(checked) => onChange(Object.fromEntries(ids.map((id) => [id, checked])))}
      />
      All
    </label>
  );
}

function NoticeInputField({
  input,
  value,
  onChange,
}: {
  input: SystemNoticeInput;
  value: boolean | string | undefined;
  onChange: (value: boolean | string) => void;
}) {
  if (input.kind === "toggle") {
    return (
      <label className="flex items-center gap-2.5 text-sm">
        <Switch checked={value === true} onCheckedChange={onChange} />
        <span className="min-w-0 break-words">{input.label ?? input.id}</span>
      </label>
    );
  }
  const text = typeof value === "string" ? value : "";
  const placeholder = input.placeholder ?? undefined;
  return (
    <label className="flex flex-col gap-1.5">
      {input.label && (
        <span className="text-xs font-medium text-muted-foreground">
          {input.label}
          {input.required ? " *" : ""}
        </span>
      )}
      {input.kind === "textarea" ? (
        <Textarea
          value={text}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          value={text}
          placeholder={placeholder}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
    </label>
  );
}

function initialValues(action: SystemNoticeAction): SystemNoticeValues {
  return Object.fromEntries(
    action.inputs.map((input) => [
      input.id,
      input.kind === "toggle" ? (input.default ?? false) : "",
    ]),
  );
}

/** The inputs by group, each group where its first input is; ``null`` holds the ungrouped. */
function inputSections(
  inputs: SystemNoticeInput[],
): { group: string | null; inputs: SystemNoticeInput[] }[] {
  const sections = new Map<string | null, SystemNoticeInput[]>();
  for (const input of inputs) {
    const group = input.group ?? null;
    sections.set(group, [...(sections.get(group) ?? []), input]);
  }
  return [...sections].map(([group, members]) => ({ group, inputs: members }));
}

function toggleIds(inputs: SystemNoticeInput[]): string[] {
  return inputs.filter((input) => input.kind === "toggle").map((input) => input.id);
}
