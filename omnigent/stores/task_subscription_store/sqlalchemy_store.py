"""SQLAlchemy-backed store of the keys that GlobalHub tasks follow."""

from __future__ import annotations

import builtins

from sqlalchemy import ColumnElement, and_, asc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.db_models import SqlTask, SqlTaskSubscription, SqlWorker, current_workspace_id
from omnigent.db.enum_codecs import encode_task_state
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import LIVE_TASK_STATES, SESSION_EVENT_SOURCE, TaskSubscription
from omnigent.stores.task_subscription_store import (
    MAX_SOURCE_KEY_CHARS,
    Follower,
    SourceKeyError,
    TaskSubscriptionStore,
)
from omnigent.util.uuid import normalize_uuid

_LIVE_TASK_CODES = frozenset(encode_task_state(state) for state in LIVE_TASK_STATES)


def _to_entity(row: SqlTaskSubscription) -> TaskSubscription:
    return TaskSubscription(
        id=row.id,
        owner_user_id=row.owner_user_id,
        task_id=row.task_id,
        source_key=row.source_key,
        created_at=row.created_at,
        source=row.source,
    )


def _owned(owner_user_id: str | None) -> ColumnElement[bool]:
    return and_(
        SqlTaskSubscription.workspace_id == current_workspace_id(),
        SqlTaskSubscription.owner_user_id == owner_user_id,
    )


def live_followers(
    session: Session, *, owner_user_id: str | None, source: str | None, source_key: str
) -> builtins.list[Follower]:
    """
    Return the owner's live tasks that follow an event, each once, within ``session``.

    The key matches exactly. Another store calls it inside its own transaction,
    e.g. to route the events that it records together with a change.
    """
    if not source_key:
        return []
    workspace_id = current_workspace_id()
    task_ids: dict[str, None] = {}
    subscribed = (
        select(SqlTaskSubscription.task_id)
        .where(_owned(owner_user_id), SqlTaskSubscription.source_key == source_key)
        .order_by(asc(SqlTaskSubscription.created_at), asc(SqlTaskSubscription.id))
    )
    task_ids.update(dict.fromkeys(session.execute(subscribed).scalars()))
    # Only the server records session events, so their key is a session id.
    if source == SESSION_EVENT_SOURCE:
        working = (
            select(SqlWorker.task_id)
            .where(
                SqlWorker.workspace_id == workspace_id,
                SqlWorker.owner_user_id == owner_user_id,
                SqlWorker.session_id == source_key,
            )
            .order_by(asc(SqlWorker.created_at), asc(SqlWorker.id))
        )
        task_ids.update(dict.fromkeys(session.execute(working).scalars()))
    if not task_ids:
        return []
    live = session.execute(
        select(SqlTask.id, SqlTask.manager_id).where(
            SqlTask.workspace_id == workspace_id,
            SqlTask.owner_user_id == owner_user_id,
            SqlTask.id.in_(list(task_ids)),
            SqlTask.state.in_(_LIVE_TASK_CODES),
        )
    ).all()
    managers = {row.id: row.manager_id for row in live}
    return [
        Follower(task_id=task_id, manager_id=managers[task_id])
        for task_id in task_ids
        if task_id in managers
    ]


class SqlAlchemyTaskSubscriptionStore(TaskSubscriptionStore):
    """
    SQLAlchemy-backed implementation of :class:`TaskSubscriptionStore`.

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
            query_name_prefix="omnigent.task_subscription_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.task_subscription_store",
            immediate=True,
        )

    def subscribe(
        self,
        subscription_id: str,
        *,
        owner_user_id: str | None,
        task_id: str,
        source_key: str,
        source: str | None = None,
    ) -> TaskSubscription:
        """Make the owner's task follow a key, or return the subscription it has."""
        if not source_key or len(source_key) > MAX_SOURCE_KEY_CHARS:
            raise SourceKeyError(f"a key has 1 to {MAX_SOURCE_KEY_CHARS} characters")
        task = normalize_uuid(task_id) or task_id
        now = now_epoch()

        def write(session: Session) -> TaskSubscription:
            require_active_account(session, owner_user_id)
            row = session.execute(
                select(SqlTaskSubscription).where(
                    _owned(owner_user_id),
                    SqlTaskSubscription.task_id == task,
                    SqlTaskSubscription.source_key == source_key,
                )
            ).scalar_one_or_none()
            if row is None:
                row = SqlTaskSubscription(
                    id=subscription_id,
                    owner_user_id=owner_user_id,
                    task_id=task,
                    source_key=source_key,
                    source=source,
                    created_at=now,
                )
                session.add(row)
            elif source is not None and row.source is None:
                row.source = source
            return _to_entity(row)

        try:
            return run_write_transaction(self._session_immediate, "subscribe_task", write)
        except IntegrityError:
            # A concurrent call subscribed the task to the key first. Retry in a
            # fresh transaction, which finds and returns that subscription.
            return run_write_transaction(self._session_immediate, "subscribe_task", write)

    def list_for_task(
        self, task_id: str, *, owner_user_id: str | None
    ) -> builtins.list[TaskSubscription]:
        """List the keys that the owner's task follows, in the order it started to follow them."""
        stmt = (
            select(SqlTaskSubscription)
            .where(
                _owned(owner_user_id),
                SqlTaskSubscription.task_id == (normalize_uuid(task_id) or task_id),
            )
            .order_by(asc(SqlTaskSubscription.created_at), asc(SqlTaskSubscription.id))
        )
        with self._session("list_task_subscriptions") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def list_live(self, *, owner_user_id: str | None) -> builtins.list[TaskSubscription]:
        """List the keys that the owner's live tasks follow, oldest first."""
        stmt = (
            select(SqlTaskSubscription)
            .join(
                SqlTask,
                and_(
                    SqlTask.workspace_id == SqlTaskSubscription.workspace_id,
                    SqlTask.id == SqlTaskSubscription.task_id,
                ),
            )
            .where(
                _owned(owner_user_id),
                SqlTask.owner_user_id == owner_user_id,
                SqlTask.state.in_(_LIVE_TASK_CODES),
            )
            .order_by(asc(SqlTaskSubscription.created_at), asc(SqlTaskSubscription.id))
        )
        with self._session("list_live_task_subscriptions") as session:
            return [_to_entity(row) for row in session.execute(stmt).scalars().all()]

    def delete(self, subscription_id: str, *, owner_user_id: str | None, task_id: str) -> bool:
        """Stop the owner's task from following a key; return whether the subscription existed."""

        def write(session: Session) -> bool:
            require_active_account(session, owner_user_id)
            row = session.get(
                SqlTaskSubscription,
                (current_workspace_id(), normalize_uuid(subscription_id) or subscription_id),
            )
            if (
                row is None
                or row.owner_user_id != owner_user_id
                or row.task_id != normalize_uuid(task_id)
            ):
                return False
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_task_subscription", write)

    def followers(
        self, *, owner_user_id: str | None, source: str | None, source_key: str
    ) -> builtins.list[Follower]:
        """Return the owner's live tasks that follow an event, each once."""
        with self._session("list_task_followers") as session:
            return live_followers(
                session, owner_user_id=owner_user_id, source=source, source_key=source_key
            )
