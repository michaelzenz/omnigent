"""SQLAlchemy-backed store of the vectors of the GlobalHub board search.

Every database stores each vector as bytes. On Postgres with pgvector, the
migration adds an ``embedding vector(1024)`` column; the store also writes that
column, and the database computes the similarities. On every other database,
the store scores the owner's vectors in Python. Sessions can live in another
database, so the store reads their change times through a callback.
"""

from __future__ import annotations

import builtins
import threading
import time
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ColumnElement, Select, and_, delete, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.types import UserDefinedType

from omnigent.agent_tasks.embeddings import (
    EMBEDDING_DIMENSIONS,
    similarity,
    vector_from_bytes,
    vector_to_bytes,
)
from omnigent.db.db_models import (
    SqlTask,
    SqlTaskEmbedding,
    SqlWorker,
    Uuid16,
    current_workspace_id,
)
from omnigent.db.enum_codecs import encode_task_state
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities import LIVE_TASK_STATES
from omnigent.stores.task_embedding_store import (
    SessionChanges,
    SourceVector,
    StaleSource,
    TaskEmbeddingStore,
    TaskVectorScores,
    WorkerScore,
)

_LIVE_TASK_CODES = sorted(encode_task_state(state) for state in LIVE_TASK_STATES)
_VECTOR_COLUMN_RECHECK_S = 600
_MAX_SCORED_ROWS = 2_000
_TASK = "task"
_SESSION = "session"


class _PgVector(UserDefinedType[str]):
    """The pgvector column type, bound as text such as ``[0.1,0.2]``."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        del kw
        return f"vector({EMBEDDING_DIMENSIONS})"


_VECTOR_ROWS = sa.table(
    "task_embeddings",
    sa.column("workspace_id", sa.BigInteger()),
    sa.column("source_id", Uuid16()),
    sa.column("embedding", _PgVector()),
)
_VECTOR_COLUMN = sa.literal_column("task_embeddings.embedding")


def _vector_value(vector: Sequence[float]) -> ColumnElement[str]:
    text = "[" + ",".join(repr(float(value)) for value in vector) + "]"
    return sa.cast(sa.literal(text), _PgVector())


def _source_exists(session: Session, source: StaleSource) -> bool:
    """Whether the task still exists, or the session still works on a task."""
    if source.source == _TASK:
        query = select(SqlTask.id).where(
            SqlTask.workspace_id == source.workspace_id, SqlTask.id == source.source_id
        )
    else:
        query = (
            select(SqlWorker.id)
            .where(
                SqlWorker.workspace_id == source.workspace_id,
                SqlWorker.session_id == source.source_id,
            )
            .limit(1)
        )
    return session.execute(query).first() is not None


def _is_stale(row: Any, *, model: str, changed_at: int, read_at: int) -> bool:
    """Whether a stored vector, read from a source that changed at ``changed_at``, is stale."""
    return (
        row.content_sha256 is None
        or row.model != model
        or row.source_changed_at != changed_at
        or (row.read_at <= row.source_changed_at and row.read_at < read_at)
        or bool(row.vector_missing)
    )


class SqlAlchemyTaskEmbeddingStore(TaskEmbeddingStore):
    """
    SQLAlchemy-backed implementation of :class:`TaskEmbeddingStore`.

    A search reads only the owner's vectors in the current workspace, and only
    those of live tasks and of the sessions that work on them.
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
            query_name_prefix="omnigent.task_embedding_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.task_embedding_store",
            immediate=True,
        )
        self._vector_column: bool | None = None
        self._vector_column_checked_at = 0.0
        self._vector_column_lock = threading.Lock()

    def _has_vector_column(self) -> bool:
        """Whether the table has the pgvector column; a missing column is checked again later."""
        if self._engine.dialect.name != "postgresql":
            return False
        with self._vector_column_lock:
            now = time.monotonic()
            if self._vector_column is True or (
                self._vector_column is False
                and now - self._vector_column_checked_at < _VECTOR_COLUMN_RECHECK_S
            ):
                return self._vector_column
            with self._session("find_task_embedding_vector_column") as session:
                found = session.execute(
                    sa.text(
                        "SELECT 1 FROM information_schema.columns"
                        " WHERE table_schema = current_schema()"
                        " AND table_name = 'task_embeddings' AND column_name = 'embedding'"
                    )
                ).first()
            self._vector_column = found is not None
            self._vector_column_checked_at = now
            return self._vector_column

    def _vector_missing(self) -> ColumnElement[bool]:
        """Whether a source with text has no pgvector value; always false without the column."""
        if not self._has_vector_column():
            return sa.false()
        return and_(_VECTOR_COLUMN.is_(None), func.length(SqlTaskEmbedding.embedding_bytes) > 0)

    def stale_sources_all_workspaces(
        self, *, model: str, limit: int, session_changes: SessionChanges
    ) -> builtins.list[StaleSource]:
        """Return up to ``limit`` live tasks and sessions, everywhere, with a stale vector."""
        if limit <= 0:
            return []
        read_at = now_epoch()
        vector_missing = self._vector_missing()
        sources = [
            *self._stale_tasks(model, limit, read_at, vector_missing),
            *self._stale_sessions(model, limit, read_at, vector_missing, session_changes),
        ]
        sources.sort(key=lambda source: (-source.changed_at, source.source_id))
        return sources[:limit]

    def _stale_tasks(
        self, model: str, limit: int, read_at: int, vector_missing: ColumnElement[bool]
    ) -> builtins.list[StaleSource]:
        changed_at = func.coalesce(SqlTask.updated_at, SqlTask.created_at)
        query = (
            select(
                SqlTask.workspace_id,
                SqlTask.id,
                SqlTask.owner_user_id,
                SqlTask.title,
                SqlTask.description,
                changed_at.label("changed_at"),
                SqlTaskEmbedding.content_sha256,
                SqlTaskEmbedding.model,
                vector_missing.label("vector_missing"),
            )
            .outerjoin(
                SqlTaskEmbedding,
                and_(
                    SqlTaskEmbedding.workspace_id == SqlTask.workspace_id,
                    SqlTaskEmbedding.source_id == SqlTask.id,
                ),
            )
            .where(
                SqlTask.state.in_(_LIVE_TASK_CODES),
                or_(
                    SqlTaskEmbedding.source_id.is_(None),
                    SqlTaskEmbedding.model != model,
                    SqlTaskEmbedding.source_changed_at != changed_at,
                    and_(
                        SqlTaskEmbedding.read_at <= SqlTaskEmbedding.source_changed_at,
                        SqlTaskEmbedding.read_at < read_at,
                    ),
                    vector_missing,
                ),
            )
            .order_by(changed_at.desc(), SqlTask.id)
            .limit(limit)
        )
        with self._session("list_stale_task_texts_in_all_workspaces") as session:
            rows = session.execute(query).all()
        return [
            StaleSource(
                workspace_id=row.workspace_id,
                source=_TASK,
                source_id=row.id,
                owner_user_id=row.owner_user_id,
                changed_at=row.changed_at,
                read_at=read_at,
                content_sha256=row.content_sha256,
                needs_vector=(
                    row.content_sha256 is None or row.model != model or bool(row.vector_missing)
                ),
                title=row.title,
                description=row.description,
            )
            for row in rows
        ]

    def _stale_sessions(
        self,
        model: str,
        limit: int,
        read_at: int,
        vector_missing: ColumnElement[bool],
        session_changes: SessionChanges,
    ) -> builtins.list[StaleSource]:
        query = (
            select(
                SqlWorker.workspace_id,
                SqlWorker.session_id,
                SqlTask.owner_user_id,
                SqlTaskEmbedding.content_sha256,
                SqlTaskEmbedding.model,
                SqlTaskEmbedding.source_changed_at,
                SqlTaskEmbedding.read_at,
                vector_missing.label("vector_missing"),
            )
            .join(
                SqlTask,
                and_(
                    SqlTask.workspace_id == SqlWorker.workspace_id, SqlTask.id == SqlWorker.task_id
                ),
            )
            .outerjoin(
                SqlTaskEmbedding,
                and_(
                    SqlTaskEmbedding.workspace_id == SqlWorker.workspace_id,
                    SqlTaskEmbedding.source_id == SqlWorker.session_id,
                ),
            )
            .where(SqlTask.state.in_(_LIVE_TASK_CODES))
        )
        with self._session("list_sessions_of_live_tasks_in_all_workspaces") as session:
            rows = session.execute(query).all()
        rows_by_workspace: defaultdict[int, dict[str, Any]] = defaultdict(dict)
        for row in rows:
            rows_by_workspace[row.workspace_id].setdefault(row.session_id, row)
        stale = []
        for workspace_id, rows_by_session in rows_by_workspace.items():
            changes = session_changes(workspace_id, list(rows_by_session))
            for session_id, row in rows_by_session.items():
                changed_at = changes.get(session_id)
                if changed_at is None or not _is_stale(
                    row, model=model, changed_at=changed_at, read_at=read_at
                ):
                    continue
                stale.append(
                    StaleSource(
                        workspace_id=workspace_id,
                        source=_SESSION,
                        source_id=session_id,
                        owner_user_id=row.owner_user_id,
                        changed_at=changed_at,
                        read_at=read_at,
                        content_sha256=row.content_sha256,
                        needs_vector=(
                            row.content_sha256 is None
                            or row.model != model
                            or bool(row.vector_missing)
                        ),
                    )
                )
        stale.sort(key=lambda source: (-source.changed_at, source.source_id))
        return stale[:limit]

    def save_all(self, vectors: Sequence[SourceVector], *, model: str) -> None:
        """Store each new vector, made with ``model``, for the change time of its source."""
        if not vectors:
            return
        has_vector_column = self._has_vector_column()

        def write(session: Session) -> None:
            saved = [item for item in vectors if _source_exists(session, item.source)]
            for item in saved:
                session.merge(
                    SqlTaskEmbedding(
                        workspace_id=item.source.workspace_id,
                        source_id=item.source.source_id,
                        source=item.source.source,
                        owner_user_id=item.source.owner_user_id,
                        model=model,
                        content_sha256=item.content_sha256,
                        embedding_bytes=vector_to_bytes(item.vector),
                        source_changed_at=item.source.changed_at,
                        read_at=item.source.read_at,
                    )
                )
            if not has_vector_column or not saved:
                return
            session.flush()
            for item in saved:
                session.execute(
                    update(_VECTOR_ROWS)
                    .where(
                        _VECTOR_ROWS.c.workspace_id == item.source.workspace_id,
                        _VECTOR_ROWS.c.source_id == item.source.source_id,
                    )
                    .values(embedding=_vector_value(item.vector) if item.vector else sa.null())
                )

        run_write_transaction(self._session_immediate, "save_task_embeddings", write)

    def touch_all(self, sources: Sequence[StaleSource]) -> None:
        """Keep the vector of each source, whose text did not change, for its new change time."""
        if not sources:
            return

        def write(session: Session) -> None:
            for source in sources:
                session.execute(
                    update(SqlTaskEmbedding)
                    .where(
                        SqlTaskEmbedding.workspace_id == source.workspace_id,
                        SqlTaskEmbedding.source_id == source.source_id,
                    )
                    .values(source_changed_at=source.changed_at, read_at=source.read_at)
                    .execution_options(synchronize_session=False)
                )

        run_write_transaction(self._session_immediate, "keep_unchanged_task_embeddings", write)

    def delete_orphans_all_workspaces(self) -> None:
        """Delete the vectors of deleted tasks and of sessions that work on no task. Blocking."""
        task_exists = (
            select(SqlTask.id)
            .where(
                SqlTask.workspace_id == SqlTaskEmbedding.workspace_id,
                SqlTask.id == SqlTaskEmbedding.source_id,
            )
            .exists()
        )
        session_works = (
            select(SqlWorker.id)
            .where(
                SqlWorker.workspace_id == SqlTaskEmbedding.workspace_id,
                SqlWorker.session_id == SqlTaskEmbedding.source_id,
            )
            .exists()
        )

        def write(session: Session) -> None:
            session.execute(
                delete(SqlTaskEmbedding)
                .where(
                    or_(
                        and_(SqlTaskEmbedding.source == _TASK, ~task_exists),
                        and_(SqlTaskEmbedding.source == _SESSION, ~session_works),
                    )
                )
                .execution_options(synchronize_session=False)
            )

        run_write_transaction(
            self._session_immediate, "delete_orphan_task_embeddings_in_all_workspaces", write
        )

    def task_scores(
        self, *, owner_user_id: str | None, vector: Sequence[float], model: str
    ) -> dict[str, TaskVectorScores]:
        """Return how near each of the owner's live tasks in the workspace is to ``vector``."""
        workspace_id = current_workspace_id()
        live_task = and_(
            SqlTask.workspace_id == workspace_id,
            SqlTask.owner_user_id == owner_user_id,
            SqlTask.state.in_(_LIVE_TASK_CODES),
        )
        owned = and_(
            SqlTaskEmbedding.workspace_id == workspace_id,
            SqlTaskEmbedding.owner_user_id == owner_user_id,
            SqlTaskEmbedding.model == model,
        )
        tasks_query = (
            select(SqlTaskEmbedding.source_id.label("task_id"))
            .join(SqlTask, and_(SqlTask.id == SqlTaskEmbedding.source_id, live_task))
            .where(owned, SqlTaskEmbedding.source == _TASK)
        )
        sessions_query = (
            select(
                SqlWorker.task_id,
                SqlWorker.id.label("worker_id"),
                SqlTaskEmbedding.source_id.label("session_id"),
            )
            .select_from(SqlTaskEmbedding)
            .join(
                SqlWorker,
                and_(
                    SqlWorker.workspace_id == SqlTaskEmbedding.workspace_id,
                    SqlWorker.session_id == SqlTaskEmbedding.source_id,
                ),
            )
            .join(SqlTask, and_(SqlTask.id == SqlWorker.task_id, live_task))
            .where(owned, SqlTaskEmbedding.source == _SESSION)
        )
        with self._session("score_task_embeddings_against_query_vector") as session:
            task_rows = self._scored(session, tasks_query, vector)
            session_rows = self._scored(session, sessions_query, vector)
        title_scores = {row.task_id: score for row, score in task_rows}
        workers_by_task: defaultdict[str, list[WorkerScore]] = defaultdict(list)
        for row, score in session_rows:
            workers_by_task[row.task_id].append(
                WorkerScore(worker_id=row.worker_id, session_id=row.session_id, score=score)
            )
        return {
            task_id: TaskVectorScores(
                task_id=task_id,
                title_score=title_scores.get(task_id),
                workers=tuple(sorted(workers_by_task[task_id], key=lambda worker: -worker.score)),
            )
            for task_id in {*title_scores, *workers_by_task}
        }

    def _scored(
        self, session: Session, query: Select[Any], vector: Sequence[float]
    ) -> builtins.list[tuple[Any, float]]:
        """Run ``query`` with each row's similarity to ``vector``, on pgvector or in Python."""
        if self._has_vector_column():
            distance = _VECTOR_COLUMN.op("<=>")(_vector_value(vector))
            rows = session.execute(
                query.add_columns(distance.label("distance"))
                .where(_VECTOR_COLUMN.is_not(None))
                .order_by(distance)
                .limit(_MAX_SCORED_ROWS)
            ).all()
            return [(row, 1.0 - float(row.distance)) for row in rows]
        rows = session.execute(
            query.add_columns(SqlTaskEmbedding.embedding_bytes).where(
                func.length(SqlTaskEmbedding.embedding_bytes) > 0
            )
        ).all()
        return [(row, similarity(vector, vector_from_bytes(row.embedding_bytes))) for row in rows]
