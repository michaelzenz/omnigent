"""SQLAlchemy-backed store of what each GlobalHub watcher tracks."""

from __future__ import annotations

import builtins
import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from typing import Any, cast

from sqlalchemy import ColumnElement, and_, asc, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlWatchItem, current_workspace_id
from omnigent.db.enum_codecs import decode_watch_item_status, encode_watch_item_status
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)
from omnigent.entities import (
    MAX_WATCH_ITEM_OFFSETS,
    EventTag,
    PagedList,
    Watcher,
    WatchItem,
)
from omnigent.errors import StaleCursorError
from omnigent.stores.subscription_store import BROKER_ROUTING
from omnigent.stores.subscription_store.sqlalchemy_store import (
    source_routing,
    subscriber_session_ids,
)
from omnigent.stores.task_event_store.sqlalchemy_store import new_task_event_row
from omnigent.stores.watch_item_store import (
    WATCH_CHANGED_EVENT_TYPE,
    WATCH_DONE_EVENT_TYPE,
    WATCH_NEW_EVENT_TYPE,
    WATCH_REOPENED_EVENT_TYPE,
    WatchChange,
    WatchFinish,
    WatchItemLimitError,
    WatchItemStore,
    WatchObservation,
    WatchObservationResult,
)
from omnigent.util.uuid import InvalidUuidError, normalize_uuid, uuid_to_bytes

_ACTIVE = encode_watch_item_status("active")
_DONE = encode_watch_item_status("done")
_EVENT_TITLE_MAX_CHARS = 512


def _to_entity(row: SqlWatchItem) -> WatchItem:
    return WatchItem(
        id=row.id,
        owner_user_id=row.owner_user_id,
        watcher_id=row.watcher_id,
        source=row.source,
        source_key=row.source_key,
        title=row.title,
        offsets=json.loads(row.offsets),
        status=decode_watch_item_status(row.status),
        created_at=row.created_at,
        checked_at=row.checked_at,
        version=row.version,
        changed_at=row.changed_at,
        done_at=row.done_at,
    )


def _owned(watcher_id: str, owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlWatchItem.workspace_id == current_workspace_id(),
        SqlWatchItem.watcher_id == watcher_id,
        SqlWatchItem.owner_user_id == owner_user_id,
    )


def _merge(
    saved: Mapping[str, str], observed: Mapping[str, str | None]
) -> tuple[dict[str, str], list[WatchChange]]:
    """Apply observed offsets to saved ones, where ``None`` removes a name; list the changes."""
    merged = dict(saved)
    changes: list[WatchChange] = []
    for name, value in observed.items():
        before = merged.get(name)
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = value
        if before != value:
            changes.append(WatchChange(offset=name, before=before, after=value))
    return merged, changes


def _keep_offsets(offsets: dict[str, str]) -> str:
    if len(offsets) > MAX_WATCH_ITEM_OFFSETS:
        raise WatchItemLimitError(f"an item keeps at most {MAX_WATCH_ITEM_OFFSETS} offsets")
    return json.dumps(offsets, ensure_ascii=False)


def _event_title(
    row: SqlWatchItem, observation: WatchObservation, changes: Sequence[WatchChange]
) -> str:
    summary = (observation.summary or "").strip()
    label = row.title or row.source_key
    if summary:
        title = summary
    elif not changes:
        title = f"New: {label}"
    else:
        changed = "; ".join(
            f"{change.offset} {change.before or '-'} -> {change.after or '-'}"
            for change in changes
        )
        title = f"{label}: {changed}"
    return title[:_EVENT_TITLE_MAX_CHARS]


def _done_title(row: SqlWatchItem, summary: str | None) -> str:
    label = row.title or row.source_key
    reason = (summary or "").strip()
    title = f"Done: {label}: {reason}" if reason else f"Done: {label}"
    return title[:_EVENT_TITLE_MAX_CHARS]


class SqlAlchemyWatchItemStore(WatchItemStore):
    """
    SQLAlchemy-backed implementation of :class:`WatchItemStore`.

    Every query is scoped by ``workspace_id``, the watcher and ``owner_user_id``.
    """

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI, e.g.
            ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.watch_item_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.watch_item_store",
            immediate=True,
        )

    def list(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        statuses: Collection[str] = ("active",),
        source: str | None = None,
        limit: int = 100,
        after: str | None = None,
    ) -> PagedList[WatchItem]:
        """List a page of a watcher's items, in the order it started to track them."""
        stmt = select(SqlWatchItem).where(
            _owned(watcher_id, owner_user_id),
            SqlWatchItem.status.in_([encode_watch_item_status(status) for status in statuses]),
        )
        if source is not None:
            stmt = stmt.where(SqlWatchItem.source == source)
        with self._session("list_watch_items") as session:
            if after:
                try:
                    cursor_id = uuid_to_bytes(after).hex()
                except InvalidUuidError as exc:
                    raise StaleCursorError(after) from exc
                # The cursor item keeps its place even after its status changed.
                cursor_at = session.execute(
                    select(SqlWatchItem.created_at).where(
                        _owned(watcher_id, owner_user_id), SqlWatchItem.id == cursor_id
                    )
                ).scalar_one_or_none()
                if cursor_at is None:
                    raise StaleCursorError(after)
                # The non-strict bound makes the index seek start at the cursor.
                stmt = stmt.where(
                    SqlWatchItem.created_at >= cursor_at,
                    or_(
                        SqlWatchItem.created_at > cursor_at,
                        and_(SqlWatchItem.created_at == cursor_at, SqlWatchItem.id > cursor_id),
                    ),
                )
            stmt = stmt.order_by(asc(SqlWatchItem.created_at), asc(SqlWatchItem.id))
            rows = session.execute(stmt.limit(limit + 1)).scalars().all()
            page = [_to_entity(row) for row in rows[:limit]]
        return PagedList(
            data=page,
            first_id=page[0].id if page else None,
            last_id=page[-1].id if page else None,
            has_more=len(rows) > limit,
        )

    def stalest(
        self, watcher_id: str, *, owner_user_id: str | None, limit: int
    ) -> builtins.list[WatchItem]:
        """Return up to ``limit`` active items of the watcher, least recently checked first."""
        stmt = (
            select(SqlWatchItem)
            .where(_owned(watcher_id, owner_user_id), SqlWatchItem.status == _ACTIVE)
            .order_by(asc(SqlWatchItem.checked_at), asc(SqlWatchItem.id))
            .limit(limit)
        )
        with self._session("list_stalest_watch_items") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def count(
        self,
        watcher_id: str,
        *,
        owner_user_id: str | None,
        statuses: Collection[str] = ("active",),
    ) -> int:
        """Count a watcher's items in the given statuses."""
        stmt = (
            select(func.count())
            .select_from(SqlWatchItem)
            .where(
                _owned(watcher_id, owner_user_id),
                SqlWatchItem.status.in_([encode_watch_item_status(status) for status in statuses]),
            )
        )
        with self._session("count_watch_items") as session:
            return int(session.execute(stmt).scalar_one())

    def observe(
        self,
        watcher: Watcher,
        observations: Sequence[WatchObservation],
        *,
        session_id: str | None,
        now: int,
    ) -> builtins.list[WatchObservationResult]:
        """Save what the watcher sees now, and record an event for each change."""
        owner = watcher.owner_user_id

        def write(session: Session) -> builtins.list[WatchObservationResult]:
            require_active_account(session, owner)
            rows = _rows_by_key(session, watcher.id, owner, observations)
            results: list[WatchObservationResult] = []
            for observation in observations:
                key = (observation.source, observation.source_key)
                row = rows.get(key)
                if row is None:
                    row = _new_row(watcher, observation, now)
                    session.add(row)
                    rows[key] = row
                    event_ids = (
                        _record(
                            session,
                            watcher,
                            row,
                            event_type=WATCH_NEW_EVENT_TYPE,
                            title=_event_title(row, observation, ()),
                            source_offset="new",
                            details=observation.details,
                            session_id=session_id,
                            now=now,
                        )
                        if observation.announce
                        else ()
                    )
                    results.append(
                        WatchObservationResult(*key, outcome="new", event_ids=event_ids)
                    )
                    continue
                results.append(_observe_row(session, watcher, row, observation, session_id, now))
            return results

        return run_write_transaction(self._session_immediate, "observe_watch_items", write)

    def finish(
        self,
        watcher: Watcher,
        items: Sequence[WatchFinish],
        *,
        session_id: str | None,
        now: int,
    ) -> builtins.list[tuple[str, str]]:
        """Mark a watcher's items done, and tell the sessions that follow them."""
        owner = watcher.owner_user_id
        keys = [(item.source, item.source_key) for item in items]

        def write(session: Session) -> builtins.list[tuple[str, str]]:
            require_active_account(session, owner)
            rows = _rows_by_key(session, watcher.id, owner, keys)
            missing: builtins.list[tuple[str, str]] = []
            for item in items:
                row = rows.get((item.source, item.source_key))
                if row is None:
                    missing.append((item.source, item.source_key))
                    continue
                if row.status == _DONE:
                    continue
                row.status = _DONE
                row.done_at = now
                _record(
                    session,
                    watcher,
                    row,
                    event_type=WATCH_DONE_EVENT_TYPE,
                    title=_done_title(row, item.summary),
                    source_offset=f"done-v{row.version}",
                    session_id=session_id,
                    now=now,
                    to_broker=False,
                )
            return missing

        return run_write_transaction(self._session_immediate, "finish_watch_items", write)

    def find(
        self,
        watcher_id: str,
        keys: Collection[tuple[str, str]],
        *,
        owner_user_id: str | None,
    ) -> builtins.list[WatchItem]:
        """Return the watcher's items with these ``(source, source_key)`` pairs, in any status."""
        if not keys:
            return []
        with self._session("find_watch_items") as session:
            rows = _rows_by_key(session, watcher_id, owner_user_id, list(keys))
            return [_to_entity(row) for row in rows.values()]

    def list_done_before(
        self, watcher_id: str, *, owner_user_id: str | None, before: int
    ) -> builtins.list[WatchItem]:
        """List the watcher's items marked done before ``before``, oldest first."""
        stmt = (
            select(SqlWatchItem)
            .where(
                _owned(watcher_id, owner_user_id),
                SqlWatchItem.status == _DONE,
                SqlWatchItem.done_at < before,
            )
            .order_by(asc(SqlWatchItem.done_at), asc(SqlWatchItem.id))
        )
        with self._session("list_done_watch_items") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def list_quiet(
        self, watcher_id: str, *, owner_user_id: str | None, before: int
    ) -> builtins.list[WatchItem]:
        last_change = func.coalesce(SqlWatchItem.changed_at, SqlWatchItem.created_at)
        stmt = (
            select(SqlWatchItem)
            .where(
                _owned(watcher_id, owner_user_id),
                SqlWatchItem.status == _ACTIVE,
                last_change < before,
            )
            .order_by(asc(last_change), asc(SqlWatchItem.id))
        )
        with self._session("list_quiet_watch_items") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def purge_done(self, watcher_id: str, *, owner_user_id: str | None, before: int) -> int:
        """Delete the watcher's items marked done before ``before``; return how many."""

        def write(session: Session) -> int:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    delete(SqlWatchItem)
                    .where(
                        _owned(watcher_id, owner_user_id),
                        SqlWatchItem.status == _DONE,
                        SqlWatchItem.done_at < before,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(self._session_immediate, "purge_done_watch_items", write)

    def delete_source(self, watcher_id: str, *, owner_user_id: str | None, source: str) -> int:
        """Delete the watcher's items of one source type, in any status; return how many."""

        def write(session: Session) -> int:
            result = cast(
                CursorResult[tuple[object]],
                session.execute(
                    delete(SqlWatchItem)
                    .where(_owned(watcher_id, owner_user_id), SqlWatchItem.source == source)
                    .execution_options(synchronize_session=False)
                ),
            )
            return result.rowcount

        return run_write_transaction(
            self._session_immediate, "delete_watch_items_of_source", write
        )


def _rows_by_key(
    session: Session,
    watcher_id: str,
    owner_user_id: str | None,
    keys: Sequence[WatchObservation] | Sequence[tuple[str, str]],
) -> dict[tuple[str, str], SqlWatchItem]:
    """Return the watcher's rows for the given items, by ``(source, source_key)``."""
    pairs = {
        (key.source, key.source_key) if isinstance(key, WatchObservation) else key for key in keys
    }
    stmt = select(SqlWatchItem).where(
        _owned(watcher_id, owner_user_id),
        SqlWatchItem.source_key.in_({source_key for _, source_key in pairs}),
    )
    rows = session.execute(stmt).scalars().all()
    return {
        (row.source, row.source_key): row for row in rows if (row.source, row.source_key) in pairs
    }


def _new_row(watcher: Watcher, observation: WatchObservation, now: int) -> SqlWatchItem:
    offsets, _ = _merge({}, observation.offsets)
    return SqlWatchItem(
        id=uuid.uuid4().hex,
        owner_user_id=watcher.owner_user_id,
        watcher_id=watcher.id,
        source=observation.source,
        source_key=observation.source_key,
        title=observation.title or "",
        offsets=_keep_offsets(offsets),
        status=_ACTIVE,
        version=0,
        created_at=now,
        checked_at=now,
        changed_at=None,
        done_at=None,
    )


def _observe_row(
    session: Session,
    watcher: Watcher,
    row: SqlWatchItem,
    observation: WatchObservation,
    session_id: str | None,
    now: int,
) -> WatchObservationResult:
    """Apply an observation to a tracked item; record events when an offset changed."""
    if observation.title is not None:
        row.title = observation.title
    row.checked_at = now
    merged, changes = _merge(json.loads(row.offsets), observation.offsets)
    key = (row.source, row.source_key)
    if not changes:
        return WatchObservationResult(*key, outcome="unchanged")
    reopened = row.status == _DONE
    if reopened:
        row.status = _ACTIVE
        row.done_at = None
    row.offsets = _keep_offsets(merged)
    row.version += 1
    row.changed_at = now
    event_ids = _record(
        session,
        watcher,
        row,
        event_type=WATCH_REOPENED_EVENT_TYPE if reopened else WATCH_CHANGED_EVENT_TYPE,
        title=_event_title(row, observation, changes),
        source_offset=f"v{row.version}",
        changes=changes,
        details=observation.details,
        session_id=session_id,
        now=now,
    )
    return WatchObservationResult(
        *key,
        outcome="reopened" if reopened else "changed",
        changes=tuple(changes),
        event_ids=event_ids,
    )


def _record(
    session: Session,
    watcher: Watcher,
    row: SqlWatchItem,
    *,
    event_type: str,
    title: str,
    source_offset: str,
    changes: Sequence[WatchChange] = (),
    details: Mapping[str, Any] | None = None,
    session_id: str | None,
    now: int,
    to_broker: bool = True,
) -> tuple[str, ...]:
    """
    Add the events for an item; return their ids.

    Each session that follows the item's key gets an event, except the
    reporting session. When no session follows it, one event goes to the
    broker, or none when ``to_broker`` is false. An event of a source type with
    ``broker`` routing goes only to the broker, or nowhere when ``to_broker`` is
    false.
    """
    payload: dict[str, Any] = {
        "watcher": {"id": watcher.id, "name": watcher.name},
        "source": row.source,
        "source_key": row.source_key,
        "title": row.title,
        "offsets": json.loads(row.offsets),
        "changes": [
            {"offset": change.offset, "before": change.before, "after": change.after}
            for change in changes
        ],
    }
    if details is not None:
        payload["details"] = dict(details)
    to_broker_only = (
        source_routing(session, owner_user_id=row.owner_user_id, source_type=row.source)
        == BROKER_ROUTING
    )
    followers = (
        []
        if to_broker_only
        else subscriber_session_ids(
            session, owner_user_id=row.owner_user_id, source_key=row.source_key
        )
    )
    reporter = normalize_uuid(session_id) or session_id if session_id is not None else None
    targets: list[str | None] = [follower for follower in followers if follower != reporter]
    if not targets:
        if followers or not to_broker:
            return ()
        targets = [None]
    event_ids: list[str] = []
    for target in targets:
        event_id = uuid.uuid4().hex
        session.add(
            new_task_event_row(
                event_id,
                owner_user_id=row.owner_user_id,
                event_type=event_type,
                title=title,
                now=now,
                payload=payload,
                source=row.source,
                source_key=row.source_key,
                source_offset=source_offset,
                source_session_id=session_id,
                # The broker clusters unrouted events by their tags.
                tags=(
                    ()
                    if target is not None
                    else (
                        EventTag(tag_type="source", tag=row.source),
                        EventTag(tag_type="watcher", tag=watcher.name),
                    )
                ),
                target_session_id=target,
            )
        )
        event_ids.append(event_id)
    return tuple(event_ids)
