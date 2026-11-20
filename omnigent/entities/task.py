"""GlobalHub board entities, persisted in the ``tasks``, ``managers``, ``workers``,
``task_events``, ``fyi_clusters``, ``task_assets``, ``system_notices``,
``agent_queue_items``, ``role_schedules``, ``watchers``, ``watch_items``,
``subscriptions`` and ``source_watchers`` tables.

A :class:`Task` is a long-lived unit of work on one user's GlobalHub board,
owned by one :class:`Manager`. Each :class:`Worker` attaches a session that
works on the task, and each :class:`TaskAsset` is a link the task carries. A
session follows a source through a :class:`Subscription`; each
:class:`TaskEvent` about the source goes to the sessions that follow it, and
the broker routes an event that no session follows to a manager, which can file
it into an :class:`FyiCluster` for the user. A :class:`SystemNotice` is pinned
on the board for the user to act on. A :class:`Watcher` runs on its schedule
and reports what changed in the things it watches, each a :class:`WatchItem`;
a :class:`SourceWatcher` assigns a source type to the watcher that checks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The task states in a manager's portfolio; an archived task is no longer in it.
LIVE_TASK_STATES = ("active", "idle", "agent-resolved")
#: The source type of the events that the server records about a session; its key is
#: the session id. A manager follows each of its workers' sessions with it.
SESSION_EVENT_SOURCE = "omnigent.session"
#: Starts the source types that only the server records events for.
RESERVED_SOURCE_TYPE_PREFIX = "omnigent."
#: Starts a watcher's role key, which names its schedule, e.g. ``"watcher:<id>"``.
WATCHER_ROLE_KEY_PREFIX = "watcher:"
#: The most offsets one tracked item keeps.
MAX_WATCH_ITEM_OFFSETS = 10
#: The longest internal note a task keeps; managers read the notes of up to 10 tasks at once.
MAX_TASK_INTERNAL_NOTE_CHARS = 2_000
#: A manager with this many live tasks is full, so the broker's manager list leaves it out.
MANAGER_TASK_CAPACITY = 30
#: The longest description a manager keeps of the scope it owns.
MAX_MANAGER_DESCRIPTION_CHARS = 3_000


@dataclass
class Task:
    """
    A task on a user's GlobalHub board.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, e.g. ``"alice@example.com"``, or
        ``None`` in single-user mode.
    :param title: Short title shown on the board card.
    :param state: ``"active"``, ``"idle"``, ``"agent-resolved"`` or
        ``"archived"``.
    :param created_at: Unix epoch seconds at row creation.
    :param manager_id: The manager that owns the task, or ``None`` before one
        is assigned.
    :param description: The user-facing overview the manager maintains.
    :param internal_note: Agent-facing context the manager maintains.
    :param priority: Priority from 0 to 3.
    :param queue_rank: Board order among the owner's tasks; higher ranks list
        first.
    :param updated_at: Unix epoch seconds of the last change, or ``None``.
    :param resolved_at: When the task entered ``agent-resolved``, or ``None``.
    :param has_update: Whether the manager flagged news on the task for the
        user; a click on the task's card clears it.
    """

    id: str
    owner_user_id: str | None
    title: str
    state: str
    created_at: int
    manager_id: str | None = None
    description: str | None = None
    internal_note: str | None = None
    priority: int = 2
    queue_rank: int = 0
    updated_at: int | None = None
    resolved_at: int | None = None
    has_update: bool = False


@dataclass
class Manager:
    """
    A manager on a user's GlobalHub board: the durable identity behind its session.

    Tasks refer to the manager. Events and subscriptions refer to its session,
    as they do for every other session.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param title: Title of the manager's session.
    :param description: The scope the manager owns, written by the manager
        itself; empty until it writes one.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last change.
    :param conversation_id: The manager's current session, or ``None`` while it
        has none.
    :param host_id: Host the manager's session runs on, or ``None`` for the
        owner's GlobalHub host.
    :param workspace: Workspace on that host, or ``None`` for the owner's
        GlobalHub workspace.
    """

    id: str
    owner_user_id: str | None
    title: str
    description: str
    created_at: int
    updated_at: int
    conversation_id: str | None = None
    host_id: str | None = None
    workspace: str | None = None


@dataclass
class Worker:
    """
    A worker: one session wrapped for one GlobalHub task.

    The session's own status (busy, waiting for the user, gone) is read from
    the session, never copied onto the worker.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param task_id: The task this worker serves. A session working on several
        tasks has one worker per task.
    :param owner_user_id: Owning user (the task's owner), or ``None`` in
        single-user mode.
    :param session_id: The wrapped session.
    :param created_at: When the session was attached to the task.
    :param title: Short label for the session's recent work on this task,
        maintained by the manager.
    :param updated_at: Unix epoch seconds of the last change, or ``None``.
    """

    id: str
    task_id: str
    owner_user_id: str | None
    session_id: str
    created_at: int
    title: str | None = None
    updated_at: int | None = None


@dataclass(frozen=True)
class EventTag:
    """
    A routing hint on a task event.

    :param tag_type: What the hint names, e.g. ``"repo"``.
    :param tag: The hint, e.g. ``"example-org/storage"``.
    """

    tag_type: str
    tag: str


@dataclass
class TaskEvent:
    """
    A signal on a user's GlobalHub board, e.g. a session finishing a turn.

    An event about a source that sessions follow is ``routed`` to one of them.
    The broker routes an ``unrouted`` event to the session of one of the
    owner's managers. A manager handles a ``routed`` event and dismisses it, or
    files it into an FYI cluster; any other session gets its event once.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param event_type: What happened, e.g. ``"session.turn.finished"``.
    :param title: One-line summary for the broker and the manager.
    :param state: ``"unrouted"``, ``"routed"``, ``"dismissed"`` or ``"fyi"``.
    :param created_at: Unix epoch seconds at row creation.
    :param payload: Details of the event.
    :param source: The source type, e.g. ``"github.pull_request"``.
    :param source_key: What the source reported on, e.g. a pull request URL.
    :param source_offset: Which report this is, e.g. a comment id. With the
        source, key and event type it identifies a repeated report.
    :param source_session_id: The session the event came from.
    :param tags: Routing hints.
    :param task_id: The task a manager assigned the event to.
    :param target_session_id: The session the event was routed to.
    :param fyi_cluster_id: The FYI cluster the event was filed into.
    :param queue_item_id: The queue item that carries the event to its role
        session; cleared when the event passes to another role.
    :param delivery_count: How many sent queue items carried the event since it
        last passed to another role.
    :param updated_at: Unix epoch seconds of the last change.
    :param routed_at: When the event was routed to its session.
    :param processed_at: When the event was dismissed or filed.
    :param workspace_id: Tenant partition key that owns this row.
    """

    id: str
    owner_user_id: str | None
    event_type: str
    title: str
    state: str
    created_at: int
    payload: dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    source_key: str | None = None
    source_offset: str | None = None
    source_session_id: str | None = None
    tags: list[EventTag] = field(default_factory=list)
    task_id: str | None = None
    target_session_id: str | None = None
    fyi_cluster_id: str | None = None
    queue_item_id: str | None = None
    delivery_count: int = 0
    updated_at: int | None = None
    routed_at: int | None = None
    processed_at: int | None = None
    workspace_id: int = 0


@dataclass
class FyiCluster:
    """
    Related events that need no task, kept on the board for the user to read.

    A manager files events that are not actionable for any task into a cluster
    under one headline. The cluster stays ``pending`` until the user dismisses
    it, which dismisses its events too.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param headline: One line that sums up the cluster's events.
    :param state: ``"pending"`` or ``"dismissed"``.
    :param created_at: Unix epoch seconds at row creation.
    :param rationale: Why the events need no task.
    :param resolved_at: When the user dismissed the cluster.
    """

    id: str
    owner_user_id: str | None
    headline: str
    state: str
    created_at: int
    rationale: str | None = None
    resolved_at: int | None = None


@dataclass
class TaskAsset:
    """
    A link a GlobalHub task carries, e.g. a design doc or pull request a session produced.

    A task has at most one asset per URL.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user (the task's owner), or ``None`` in
        single-user mode.
    :param task_id: The task the asset belongs to.
    :param category: Free-form label that groups assets on the task card, e.g.
        ``"documents"``; ``"other"`` when none was given.
    :param title: Short human-readable name of the artifact.
    :param url: Where the artifact lives.
    :param created_at: Unix epoch seconds at row creation.
    :param related_session_ids: The sessions that produced or used it.
    :param updated_at: Unix epoch seconds of the last change.
    """

    id: str
    owner_user_id: str | None
    task_id: str
    category: str
    title: str
    url: str
    created_at: int
    related_session_ids: list[str] = field(default_factory=list)
    updated_at: int | None = None


@dataclass
class SystemNotice:
    """
    The notice pinned on a user's GlobalHub board, e.g. the custodian's cleanup proposal.

    Each user has at most one ``active`` notice; publishing another resolves it.
    Its declared actions are the buttons the user can click, each optionally
    with inputs (toggles and text fields) whose values the click carries.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param title: The notice's headline.
    :param body_kind: ``"markdown"`` or ``"html"``.
    :param state: ``"active"`` or ``"resolved"``.
    :param created_at: Unix epoch seconds at row creation.
    :param body: The notice's body.
    :param actions: The declared actions, e.g. ``[{"id": "confirm", "inputs": [...]}]``.
    :param publisher_session_id: The session that published it, when confirmed.
    :param publisher_label: That session's title when it published.
    :param updated_at: Unix epoch seconds of the last change.
    :param resolved_at: When it was replaced or dismissed.
    :param changes: The staged changes the notice proposes, each with the ``id``
        of the toggle that approves it; empty for a notice published directly.
    """

    id: str
    owner_user_id: str | None
    title: str
    body_kind: str
    state: str
    created_at: int
    body: str | None = None
    actions: list[dict[str, Any]] = field(default_factory=list)
    publisher_session_id: str | None = None
    publisher_label: str | None = None
    updated_at: int | None = None
    resolved_at: int | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AgentQueueItem:
    """
    A message waiting for, or sent to, a GlobalHub role session.

    The dispatcher sends each session one item at a time, in arrival order,
    when the session is idle. It writes the message text when it sends, from
    ``payload`` and the task events that point at the item.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param target_session_id: The session the message goes to.
    :param kind: ``"events"``, ``"notice_click"``, ``"spawn_managers"``,
        ``"scheduled_run"``, ``"worker_message"`` or ``"source_types"``.
    :param state: ``"queued"``, ``"sending"``, ``"sent"`` or ``"done"``.
    :param seq: Arrival order; a session receives its items in this order.
    :param created_at: Unix epoch seconds at row creation.
    :param payload: Inputs for the message text, e.g. a notice click's values.
    :param attempts: How many sends failed.
    :param not_before: Earliest Unix epoch second for the next send after a failure.
    :param last_error: Why the last send failed.
    :param updated_at: Unix epoch seconds of the last change.
    :param sent_at: When the send succeeded.
    :param done_at: When the turn that handled the item ended.
    :param workspace_id: Tenant partition key that owns this row.
    """

    id: str
    owner_user_id: str | None
    target_session_id: str
    kind: str
    state: str
    seq: int
    created_at: int
    payload: dict[str, Any] = field(default_factory=dict)
    attempts: int = 0
    not_before: int | None = None
    last_error: str | None = None
    updated_at: int | None = None
    sent_at: int | None = None
    done_at: int | None = None
    workspace_id: int = 0


@dataclass
class RoleSchedule:
    """
    When one of a user's GlobalHub role sessions gets its scheduled run message.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param role_key: The role that runs, e.g. ``"custodian"``.
    :param rrule: An RFC 5545 recurrence rule without an end, e.g.
        ``"FREQ=DAILY;BYHOUR=9;BYMINUTE=0"``.
    :param timezone: The IANA timezone of ``rrule``, e.g. ``"America/Los_Angeles"``.
    :param next_run_at: Unix epoch seconds of the next run, or ``None`` while the
        schedule is off.
    :param created_at: Unix epoch seconds at row creation; the rule's intervals
        count from this day.
    :param updated_at: Unix epoch seconds of the last change.
    :param workspace_id: Tenant partition key that owns this row.
    """

    id: str
    owner_user_id: str | None
    role_key: str
    rrule: str
    timezone: str
    next_run_at: int | None
    created_at: int
    updated_at: int | None = None
    workspace_id: int = 0

    @property
    def enabled(self) -> bool:
        """Whether the role gets its runs."""
        return self.next_run_at is not None


@dataclass
class Watcher:
    """
    A watcher on a user's GlobalHub board: a role session that runs on its schedule.

    On each run it checks the things its manual names and reports what changed.
    Its schedule is the :class:`RoleSchedule` whose key is :attr:`role_key`.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param name: The watcher's name, also the title of its session.
    :param description: What the watcher watches, in a line or two; may be empty.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last change.
    :param manual: The user's manual, or ``None`` for the packaged watcher template.
    :param model: Model for the watcher's session, or ``None`` for the watcher
        agent's own model.
    :param conversation_id: The watcher's current session, or ``None`` before
        its first run.
    :param builtin: Whether it is the built-in fallback watcher, which checks
        the source types with no assigned watcher. The user cannot delete it.
    """

    id: str
    owner_user_id: str | None
    name: str
    description: str
    created_at: int
    updated_at: int
    manual: str | None = None
    model: str | None = None
    conversation_id: str | None = None
    builtin: bool = False

    @property
    def role_key(self) -> str:
        """The key of the watcher's schedule, e.g. ``"watcher:<id>"``."""
        return f"{WATCHER_ROLE_KEY_PREFIX}{self.id}"


@dataclass
class WatchItem:
    """
    One thing a watcher tracks, e.g. a pull request, with what it saw last.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user (the watcher's owner), or ``None`` in
        single-user mode.
    :param watcher_id: The watcher that tracks it.
    :param source: The source type, e.g. ``"github.pull_request"``.
    :param source_key: What is tracked, e.g. a pull request URL.
    :param title: A short label for events and the watcher card; may be empty.
    :param offsets: The values the watcher saw last, by name, e.g.
        ``{"state": "open", "checks": "failure@abc123"}``.
    :param status: ``"active"`` or ``"done"``.
    :param created_at: Unix epoch seconds at row creation.
    :param checked_at: When the watcher last observed it.
    :param version: How many changes it had.
    :param changed_at: When an offset last changed, or ``None`` before the first change.
    :param done_at: When the watcher marked it done, or ``None``.
    """

    id: str
    owner_user_id: str | None
    watcher_id: str
    source: str
    source_key: str
    title: str
    offsets: dict[str, str]
    status: str
    created_at: int
    checked_at: int
    version: int = 0
    changed_at: int | None = None
    done_at: int | None = None


@dataclass
class Subscription:
    """
    A source that a session follows, e.g. a pull request.

    Each event about the source key goes to every session that follows it. Any
    session can subscribe; the server also subscribes a manager to the
    sessions of its workers.

    :param id: UUID primary key (bare 32-char hex string, no dashes).
    :param owner_user_id: Owning user (the session's owner), or ``None`` in
        single-user mode.
    :param session_id: The session that follows the source.
    :param source_type: What kind of source it is, e.g. ``"github.pull_request"``.
    :param source_key: The source, e.g. a pull request URL; events match it exactly.
    :param created_at: Unix epoch seconds at row creation.
    :param note: Why the session follows it and, for a source that is hard to
        read, how a watcher reads it, e.g. a permission it needs; or ``None``.
    :param url: Where a person opens the source when its key is not a web address,
        e.g. a ticket's page; or ``None``.
    """

    id: str
    owner_user_id: str | None
    session_id: str
    source_type: str
    source_key: str
    created_at: int
    note: str | None = None
    url: str | None = None


@dataclass
class SourceWatcher:
    """
    The watcher that checks the sources of one type, e.g. the GitHub watcher.

    The custodian assigns each new source type to a watcher whose manual
    covers it. The fallback watcher checks the types with no assignment.

    :param owner_user_id: Owning user, or ``None`` in single-user mode.
    :param source_type: The source type, e.g. ``"github.pull_request"``.
    :param watcher_id: The assigned watcher.
    :param assigned_at: Unix epoch seconds of the assignment.
    """

    owner_user_id: str | None
    source_type: str
    watcher_id: str
    assigned_at: int
