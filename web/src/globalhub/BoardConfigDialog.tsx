import { useState } from "react";
import { BanIcon, BotIcon, RotateCcwIcon, Trash2Icon } from "lucide-react";
import { SessionStateBadge } from "@/components/SessionStateBadge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import {
  type BoardRole,
  useBoardRoles,
  useDeleteManager,
  useDismissRoleWaiting,
  useResetRoleSession,
} from "@/hooks/useGlobalHubRoles";
import { getSessionState } from "@/hooks/useSessionState";
import { ConfirmDialog } from "./ConfirmDialog";
import { OpenSessionLink } from "./OpenSessionLink";

type RoleAction = "dismiss" | "reset" | "delete";

/** The page-header button that opens the Board configuration dialog. */
export function BoardConfigButton() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Board configuration"
          title="Board configuration"
          data-testid="globalhub-board-config-button"
        >
          <BotIcon className="size-4" />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Board configuration</DialogTitle>
          <DialogDescription>
            The roles that run your board, and the work waiting for each.
          </DialogDescription>
        </DialogHeader>
        {/* The dialog mounts its content only while open, so the roles refresh only then. */}
        <RoleRows />
      </DialogContent>
    </Dialog>
  );
}

function RoleRows() {
  const roles = useBoardRoles();
  const [confirming, setConfirming] = useState<{ action: RoleAction; row: BoardRole } | null>(null);
  const dismiss = useDismissRoleWaiting();
  const reset = useResetRoleSession();
  const remove = useDeleteManager();
  const close = () => setConfirming(null);

  if (roles.data === undefined) {
    return roles.isError ? (
      <p role="alert" className="text-sm text-destructive">
        Could not load the roles: {roles.error.message}
      </p>
    ) : (
      <div className="flex justify-center p-4">
        <Spinner className="size-5 text-muted-foreground" aria-label="Loading roles" />
      </div>
    );
  }

  const row = confirming?.row;
  const action = confirming?.action;
  const mutation = action === "dismiss" ? dismiss : action === "reset" ? reset : remove;
  return (
    <>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {roles.data.map((role) => (
          <RoleRow
            key={role.manager_id ?? role.role}
            row={role}
            onAction={(next) => setConfirming({ action: next, row: role })}
          />
        ))}
      </ul>
      {row && action && (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) close();
          }}
          {...confirmText(action, row)}
          destructive
          pending={mutation.isPending}
          error={mutation.error}
          onConfirm={() => {
            if (action === "delete" && row.manager_id) {
              remove.mutate(row.manager_id, { onSuccess: close });
            } else if (action === "reset") {
              reset.mutate(row, { onSuccess: close });
            } else {
              dismiss.mutate(row, { onSuccess: close });
            }
          }}
        />
      )}
    </>
  );
}

function RoleRow({ row, onAction }: { row: BoardRole; onAction: (action: RoleAction) => void }) {
  const state = row.session ? getSessionState(row.session) : null;
  const isManager = row.manager_id !== null;
  return (
    <li
      className="flex items-center gap-3 px-3 py-2"
      data-testid={`board-role-${row.manager_id ?? row.role}`}
    >
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{row.title}</p>
        <p className="text-xs text-muted-foreground">
          {roleKind(row)}
          {row.session === null && " · No session"}
        </p>
      </div>
      {state && <SessionStateBadge state={state} />}
      <span
        className="shrink-0 text-xs text-muted-foreground tabular-nums"
        title={waitingTitle(row)}
      >
        {row.waiting} waiting
      </span>
      <div className="flex shrink-0 items-center gap-1">
        {row.session_id && (
          <OpenSessionLink sessionId={row.session_id} label={`Open the ${row.title} session`} />
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={row.waiting === 0}
          aria-label={`Dismiss the work waiting for ${row.title}`}
          title="Dismiss all waiting"
          onClick={() => onAction("dismiss")}
        >
          <BanIcon className="text-destructive" aria-hidden />
        </Button>
        {isManager ? (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Delete manager ${row.title}`}
            title="Delete the manager and its session"
            onClick={() => onAction("delete")}
          >
            <Trash2Icon className="text-destructive" aria-hidden />
          </Button>
        ) : (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Reset ${row.title}`}
            title="Delete the session and start a fresh one"
            onClick={() => onAction("reset")}
          >
            <RotateCcwIcon className="text-destructive" aria-hidden />
          </Button>
        )}
      </div>
    </li>
  );
}

function roleKind(row: BoardRole): string {
  if (row.manager_id !== null) return "Manager";
  return row.role === "custodian" ? "Custodian" : "Broker";
}

function waitingTitle(row: BoardRole): string {
  if (row.manager_id !== null) return "Events routed to this manager";
  return row.role === "custodian"
    ? "Clicks and the scheduled cleanup queued for the custodian"
    : "Events and requests for managers waiting for the broker";
}

function confirmText(
  action: RoleAction,
  row: BoardRole,
): { title: string; description: string; confirmLabel: string } {
  if (action === "delete") {
    return {
      title: `Delete manager ${row.title}?`,
      description:
        "The manager and its session are deleted, and its tasks have no manager. The events waiting for it go back to the broker. This cannot be undone.",
      confirmLabel: "Delete manager",
    };
  }
  if (action === "reset") {
    return {
      title: `Reset ${row.title}?`,
      description:
        row.role === "custodian"
          ? "Its session is deleted and a fresh one starts. The clicks and the scheduled cleanup queued for it are dropped."
          : "Its session is deleted and a fresh one starts. The events it was handling go to the new session.",
      confirmLabel: "Reset",
    };
  }
  const plural = row.waiting === 1 ? "" : "s";
  if (row.role === "broker" && row.manager_id === null) {
    return {
      title: `Dismiss ${row.waiting} waiting item${plural} for ${row.title}?`,
      description:
        "The waiting events are dismissed, and the queued requests for managers are dropped. A notice or request the broker is already handling goes on. This cannot be undone.",
      confirmLabel: "Dismiss all",
    };
  }
  const custodian = row.role === "custodian" && row.manager_id === null;
  const unit = custodian ? "item" : "event";
  return {
    title: `Dismiss ${row.waiting} waiting ${unit}${plural} for ${row.title}?`,
    description: custodian
      ? "The queued clicks and the queued scheduled cleanup are dropped. A message the custodian is already handling goes on. This cannot be undone."
      : "The waiting events are dismissed. A notice the role is already handling goes on. This cannot be undone.",
    confirmLabel: "Dismiss all",
  };
}
