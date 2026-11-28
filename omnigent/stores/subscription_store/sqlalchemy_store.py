"""SQLAlchemy-backed store of the sources that GlobalHub sessions follow."""

from __future__ import annotations

import builtins
import uuid
from collections import Counter
from collections.abc import Collection
from typing import cast

from sqlalchemy import ColumnElement, and_, asc, delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import (
    SqlSourceTypeSetting,
    SqlSourceWatcher,
    SqlSubscription,
    current_workspace_id,
)
from omnigent.db.enum_codecs import decode_source_routing, encode_source_routing
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import RESERVED_SOURCE_TYPE_PREFIX, SourceWatcher, Subscription
from omnigent.stores.subscription_store import (
    SourceError,
    SourceTypeUse,
    SubscribeResult,
    SubscriptionStore,
    is_reserved_source_type,
    normalize_note,
    normalize_source_key,
    normalize_source_type,
    normalize_source_url,
)
from omnigent.util.uuid import normalize_uuid


def _to_entity(row: SqlSubscription) -> Subscription:
    return Subscription(
        id=row.id,
        owner_user_id=row.owner_user_id,
        session_id=row.session_id,
        source_type=row.source_type,
        source_key=row.source_key,
        created_at=row.created_at,
        note=row.note,
        url=row.url,
    )


def _to_source_watcher(row: SqlSourceWatcher) -> SourceWatcher:
    return SourceWatcher(
        owner_user_id=row.owner_user_id,
        source_type=row.source_type,
        watcher_id=row.watcher_id,
        assigned_at=row.assigned_at,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlSubscription.workspace_id == current_workspace_id(),
        SqlSubscription.owner_user_id == owner_user_id,
    )


def _owned_assignments(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlSourceWatcher.workspace_id == current_workspace_id(),
        SqlSourceWatcher.owner_user_id == owner_user_id,
    )


def _owned_settings(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlSourceTypeSetting.workspace_id == current_workspace_id(),
        SqlSourceTypeSetting.owner_user_id == owner_user_id,
    )


def source_routing(session: Session, *, owner_user_id: str | None, source_type: str) -> str | None:
    code = session.execute(
        select(SqlSourceTypeSetting.routing)
        .where(_owned_settings(owner_user_id), SqlSourceTypeSetting.source_type == source_type)
        .limit(1)
    ).scalar()
    return decode_source_routing(code) if code is not None else None


_OLDEST_FIRST = (asc(SqlSubscription.created_at), asc(SqlSubscription.id))


def subscriber_session_ids(
    session: Session, *, owner_user_id: str | None, source_key: str
) -> builtins.list[str]:
    """
    Return the sessions that follow a source key, within ``session``.

    Another store calls it inside its own transaction, e.g. to route the events
    that it records together with a change.
    """
    if not source_key:
        return []
    stmt = (
        select(SqlSubscription.session_id)
        .where(_owned(owner_user_id), SqlSubscription.source_key == source_key)
        .order_by(*_OLDEST_FIRST)
    )
    return list(dict.fromkeys(session.execute(stmt).scalars()))


def _deleted(result: object) -> int:
    return cast(CursorResult[tuple[object]], result).rowcount


class SqlAlchemySubscriptionStore(SubscriptionStore):
    """
    SQLAlchemy-backed implementation of :class:`SubscriptionStore`.

    Every query is scoped by ``workspace_id`` and ``owner_user_id``.
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
            query_name_prefix="omnigent.subscription_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.subscription_store",
            immediate=True,
        )

    def subscribe(
        self,
        *,
        owner_user_id: str | None,
        session_id: str,
        source_type: str,
        source_key: str,
        note: str | None = None,
        url: str | None = None,
    ) -> SubscribeResult:
        """Make a session follow a source, or update the type, note and link of the one it has."""
        kind = normalize_source_type(source_type)
        key = normalize_source_key(source_key)
        cleaned_note = normalize_note(note) if note is not None else None
        cleaned_url = normalize_source_url(url) if url is not None else None
        session_uuid = normalize_uuid(session_id) or session_id
        now = now_epoch()

        def write(session: Session) -> SubscribeResult:
            require_active_account(session, owner_user_id)
            known = (
                is_reserved_source_type(kind)
                or session.execute(
                    select(SqlSubscription.id)
                    .where(_owned(owner_user_id), SqlSubscription.source_type == kind)
                    .limit(1)
                ).first()
                is not None
                or session.execute(
                    select(SqlSourceWatcher.id)
                    .where(_owned_assignments(owner_user_id), SqlSourceWatcher.source_type == kind)
                    .limit(1)
                ).first()
                is not None
            )
            row = session.execute(
                select(SqlSubscription).where(
                    _owned(owner_user_id),
                    SqlSubscription.session_id == session_uuid,
                    SqlSubscription.source_key == key,
                )
            ).scalar_one_or_none()
            if row is None:
                row = SqlSubscription(
                    id=uuid.uuid4().hex,
                    owner_user_id=owner_user_id,
                    session_id=session_uuid,
                    source_type=kind,
                    source_key=key,
                    note=cleaned_note or None,
                    url=cleaned_url or None,
                    created_at=now,
                )
                session.add(row)
            else:
                row.source_type = kind
                if cleaned_note is not None:
                    row.note = cleaned_note or None
                if cleaned_url is not None:
                    row.url = cleaned_url or None
            return SubscribeResult(subscription=_to_entity(row), new_type=not known)

        try:
            return run_write_transaction(self._session_immediate, "subscribe_session", write)
        except IntegrityError:
            return run_write_transaction(self._session_immediate, "subscribe_session", write)

    def unsubscribe(self, *, owner_user_id: str | None, session_id: str, source_key: str) -> bool:
        """Stop a session from following a source; return whether it followed it."""
        key = normalize_source_key(source_key)
        session_uuid = normalize_uuid(session_id) or session_id

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            result = session.execute(
                delete(SqlSubscription)
                .where(
                    _owned(owner_user_id),
                    SqlSubscription.session_id == session_uuid,
                    SqlSubscription.source_key == key,
                )
                .execution_options(synchronize_session=False)
            )
            return _deleted(result) > 0

        return run_write_transaction(self._session_immediate, "unsubscribe_session", write)

    def list_for_session(
        self, session_id: str, *, owner_user_id: str | None
    ) -> builtins.list[Subscription]:
        """List the sources that a session follows, in the order it started to follow them."""
        stmt = (
            select(SqlSubscription)
            .where(
                _owned(owner_user_id),
                SqlSubscription.session_id == (normalize_uuid(session_id) or session_id),
            )
            .order_by(*_OLDEST_FIRST)
        )
        with self._session("list_session_subscriptions") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def list_for_types(
        self, source_types: Collection[str], *, owner_user_id: str | None
    ) -> builtins.list[Subscription]:
        """List the owner's subscriptions of the given types, oldest first."""
        if not source_types:
            return []
        stmt = (
            select(SqlSubscription)
            .where(_owned(owner_user_id), SqlSubscription.source_type.in_(list(source_types)))
            .order_by(*_OLDEST_FIRST)
        )
        with self._session("list_subscriptions_of_source_types") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def list_unassigned(self, *, owner_user_id: str | None) -> builtins.list[Subscription]:
        """List the owner's subscriptions whose type has no watcher, oldest first."""
        assigned = select(SqlSourceWatcher.source_type).where(_owned_assignments(owner_user_id))
        stmt = (
            select(SqlSubscription)
            .where(
                _owned(owner_user_id),
                SqlSubscription.source_type.not_in(assigned),
                SqlSubscription.source_type.not_like(f"{RESERVED_SOURCE_TYPE_PREFIX}%"),
            )
            .order_by(*_OLDEST_FIRST)
        )
        with self._session("list_subscriptions_without_watcher") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def subscribers(self, *, owner_user_id: str | None, source_key: str) -> builtins.list[str]:
        """Return the sessions that follow a source key, in the order they started to follow it."""
        with self._session("list_source_subscribers") as session:
            return subscriber_session_ids(
                session, owner_user_id=owner_user_id, source_key=normalize_source_key(source_key)
            )

    def source_types(self, *, owner_user_id: str | None) -> builtins.list[SourceTypeUse]:
        """List the owner's source types, from subscriptions and assignments, by name."""
        with self._session("list_source_types") as session:
            used = Counter(
                session.execute(
                    select(SqlSubscription.source_type).where(
                        _owned(owner_user_id),
                        SqlSubscription.source_type.not_like(f"{RESERVED_SOURCE_TYPE_PREFIX}%"),
                    )
                ).scalars()
            )
            assigned = {
                row.source_type: row.watcher_id
                for row in session.execute(
                    select(SqlSourceWatcher).where(_owned_assignments(owner_user_id))
                ).scalars()
            }
            routings = {
                row.source_type: decode_source_routing(row.routing)
                for row in session.execute(
                    select(SqlSourceTypeSetting).where(_owned_settings(owner_user_id))
                ).scalars()
            }
        return [
            SourceTypeUse(
                source_type=source_type,
                watcher_id=assigned.get(source_type),
                subscriptions=used.get(source_type, 0),
                routing=routings.get(source_type),
            )
            for source_type in sorted(set(used) | set(assigned) | set(routings))
        ]

    def copy_session(
        self, source_session_id: str, target_session_id: str, *, owner_user_id: str | None
    ) -> int:
        """Make a session follow the sources that another session follows; return how many."""
        source_uuid = normalize_uuid(source_session_id) or source_session_id
        target_uuid = normalize_uuid(target_session_id) or target_session_id

        def write(session: Session) -> int:
            require_active_account(session, owner_user_id)
            followed = set(
                session.execute(
                    select(SqlSubscription.source_key).where(
                        _owned(owner_user_id), SqlSubscription.session_id == target_uuid
                    )
                ).scalars()
            )
            rows = session.execute(
                select(SqlSubscription)
                .where(
                    _owned(owner_user_id),
                    SqlSubscription.session_id == source_uuid,
                    SqlSubscription.source_type.not_like(f"{RESERVED_SOURCE_TYPE_PREFIX}%"),
                )
                .order_by(*_OLDEST_FIRST)
            ).scalars()
            copies = [
                SqlSubscription(
                    id=uuid.uuid4().hex,
                    owner_user_id=owner_user_id,
                    session_id=target_uuid,
                    source_type=row.source_type,
                    source_key=row.source_key,
                    note=row.note,
                    url=row.url,
                    created_at=row.created_at,
                )
                for row in rows
                if row.source_key not in followed
            ]
            session.add_all(copies)
            return len(copies)

        return run_write_transaction(self._session_immediate, "copy_session_subscriptions", write)

    def delete_for_session(self, session_id: str, *, owner_user_id: str | None) -> int:
        """Delete every subscription of a session; return how many."""
        session_uuid = normalize_uuid(session_id) or session_id

        def write(session: Session) -> int:
            return _deleted(
                session.execute(
                    delete(SqlSubscription)
                    .where(_owned(owner_user_id), SqlSubscription.session_id == session_uuid)
                    .execution_options(synchronize_session=False)
                )
            )

        return run_write_transaction(
            self._session_immediate, "delete_session_subscriptions", write
        )

    def delete_for_keys(
        self,
        source_keys: Collection[str],
        *,
        owner_user_id: str | None,
        created_before: int | None = None,
    ) -> int:
        """Delete the owner's subscriptions to the given source keys; return how many."""
        if not source_keys:
            return 0
        keys = list(dict.fromkeys(normalize_source_key(key) for key in source_keys))
        stmt = delete(SqlSubscription).where(
            _owned(owner_user_id), SqlSubscription.source_key.in_(keys)
        )
        if created_before is not None:
            stmt = stmt.where(SqlSubscription.created_at <= created_before)

        def write(session: Session) -> int:
            return _deleted(session.execute(stmt.execution_options(synchronize_session=False)))

        return run_write_transaction(
            self._session_immediate, "delete_subscriptions_to_sources", write
        )

    def assign_type(
        self, *, owner_user_id: str | None, source_type: str, watcher_id: str
    ) -> SourceWatcher:
        """Assign a source type to a watcher, in place of any watcher it had."""
        kind = normalize_source_type(source_type)
        if is_reserved_source_type(kind):
            raise SourceError(f"the server records the events of {kind!r} itself")
        watcher_uuid = normalize_uuid(watcher_id) or watcher_id
        now = now_epoch()

        def write(session: Session) -> SourceWatcher:
            require_active_account(session, owner_user_id)
            rows = (
                session.execute(
                    select(SqlSourceWatcher).where(
                        _owned_assignments(owner_user_id), SqlSourceWatcher.source_type == kind
                    )
                )
                .scalars()
                .all()
            )
            row = rows[0] if rows else None
            for extra in rows[1:]:
                session.delete(extra)
            if row is None:
                row = SqlSourceWatcher(
                    id=uuid.uuid4().hex,
                    owner_user_id=owner_user_id,
                    source_type=kind,
                    watcher_id=watcher_uuid,
                    assigned_at=now,
                )
                session.add(row)
            elif row.watcher_id != watcher_uuid:
                row.watcher_id = watcher_uuid
                row.assigned_at = now
            return _to_source_watcher(row)

        return run_write_transaction(self._session_immediate, "assign_source_type", write)

    def unassign_type(self, *, owner_user_id: str | None, source_type: str) -> bool:
        """Leave a source type to the fallback watcher; return whether it had a watcher."""
        kind = normalize_source_type(source_type)

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            return (
                _deleted(
                    session.execute(
                        delete(SqlSourceWatcher)
                        .where(
                            _owned_assignments(owner_user_id), SqlSourceWatcher.source_type == kind
                        )
                        .execution_options(synchronize_session=False)
                    )
                )
                > 0
            )

        return run_write_transaction(self._session_immediate, "unassign_source_type", write)

    def source_watchers(self, *, owner_user_id: str | None) -> builtins.list[SourceWatcher]:
        """List the owner's assignments, by source type."""
        stmt = (
            select(SqlSourceWatcher)
            .where(_owned_assignments(owner_user_id))
            .order_by(asc(SqlSourceWatcher.source_type))
        )
        with self._session("list_source_watchers") as session:
            return [_to_source_watcher(row) for row in session.execute(stmt).scalars().all()]

    def types_for_watcher(
        self, watcher_id: str, *, owner_user_id: str | None
    ) -> builtins.list[str]:
        """List the source types assigned to a watcher, by name."""
        stmt = (
            select(SqlSourceWatcher.source_type)
            .where(
                _owned_assignments(owner_user_id),
                SqlSourceWatcher.watcher_id == (normalize_uuid(watcher_id) or watcher_id),
            )
            .order_by(asc(SqlSourceWatcher.source_type))
        )
        with self._session("list_source_types_of_watcher") as session:
            return list(session.execute(stmt).scalars())

    def routing(self, *, owner_user_id: str | None, source_type: str) -> str | None:
        kind = normalize_source_type(source_type)
        with self._session("read_source_type_routing") as session:
            return source_routing(session, owner_user_id=owner_user_id, source_type=kind)

    def set_routing(self, *, owner_user_id: str | None, source_type: str, routing: str) -> str:
        kind = normalize_source_type(source_type)
        if is_reserved_source_type(kind):
            raise SourceError(f"the server routes the events of {kind!r} itself")
        code = encode_source_routing(routing)
        now = now_epoch()

        def write(session: Session) -> str:
            require_active_account(session, owner_user_id)
            rows = (
                session.execute(
                    select(SqlSourceTypeSetting).where(
                        _owned_settings(owner_user_id), SqlSourceTypeSetting.source_type == kind
                    )
                )
                .scalars()
                .all()
            )
            row = rows[0] if rows else None
            for extra in rows[1:]:
                session.delete(extra)
            if row is None:
                session.add(
                    SqlSourceTypeSetting(
                        id=uuid.uuid4().hex,
                        owner_user_id=owner_user_id,
                        source_type=kind,
                        routing=code,
                        updated_at=now,
                    )
                )
            elif row.routing != code:
                row.routing = code
                row.updated_at = now
            return routing

        return run_write_transaction(self._session_immediate, "set_source_type_routing", write)

    def clear_routing(self, *, owner_user_id: str | None, source_type: str) -> bool:
        kind = normalize_source_type(source_type)

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            return (
                _deleted(
                    session.execute(
                        delete(SqlSourceTypeSetting)
                        .where(
                            _owned_settings(owner_user_id),
                            SqlSourceTypeSetting.source_type == kind,
                        )
                        .execution_options(synchronize_session=False)
                    )
                )
                > 0
            )

        return run_write_transaction(self._session_immediate, "clear_source_type_routing", write)
