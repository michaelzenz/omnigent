"""Tests for the broker packager (poll-based batching)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from omnigent.agent_tasks.agent_builtins import TASK_BROKER_ROLE
from omnigent.agent_tasks.broker_inbox import cluster_events_by_similarity
from omnigent.agent_tasks.ingress import ingress_event
from omnigent.agent_tasks.queue.packagers import (
    DEFAULT_PACKAGER_AGE_THRESHOLD_S,
    DEFAULT_PACKAGER_POLL_INTERVAL_S,
    BrokerPackager,
    _StatusReader,
    configure_broker_packager,
    get_broker_packager,
)
from omnigent.db.utils import generate_agent_id
from omnigent.entities import AgentQueueKey, EventTag, TaskEvent, TaskTag
from omnigent.runtime import init as init_runtime
from omnigent.runtime.agent_cache import AgentCache
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.stores.agent_queue_store.sqlalchemy_store import SqlAlchemyAgentQueueStore
from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
from omnigent.stores.artifact_store.local import LocalArtifactStore
from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
from omnigent.stores.host_store import HostStore
from omnigent.stores.task_event_store.sqlalchemy_store import SqlAlchemyTaskEventStore
from omnigent.stores.task_role_profile_store.sqlalchemy_store import (
    SqlAlchemyTaskRoleProfileStore,
)
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.user_role_session_store.sqlalchemy_store import (
    SqlAlchemyUserRoleSessionStore,
)
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


class _StaticStatusReader(_StatusReader):
    """Reports a fixed status for every session."""

    def __init__(self, status: str | None = "idle") -> None:
        self.status = status

    def status_for(self, session_id: str) -> str | None:
        return self.status


@pytest.fixture
def broker_setup(db_uri: str) -> dict:
    agent_store = SqlAlchemyAgentStore(db_uri)
    task_store = SqlAlchemyTaskStore(db_uri)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    worker_store = SqlAlchemyWorkerStore(db_uri)
    conversation_store = SqlAlchemyConversationStore(db_uri)
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    session_store = SqlAlchemyUserRoleSessionStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)
    manager_agent_id = generate_agent_id()
    agent_store.create(
        manager_agent_id, name="task-manager-agent", bundle_location="test:///bundle"
    )
    user_id = "__anonymous__"
    broker_conv = conversation_store.create_conversation(
        title="Task broker",
        agent_id=manager_agent_id,
        host_id=_uid("host_broker"),
        workspace="/tmp/broker",
    )
    profile_store.upsert(
        TASK_BROKER_ROLE,
        agent_profile_id=manager_agent_id,
        host_id=_uid("host_broker"),
        workspace="/tmp/broker",
    )
    session_store.set_conversation(user_id, TASK_BROKER_ROLE, broker_conv.id)
    status_reader = _StaticStatusReader("idle")
    packager = BrokerPackager(
        store=queue_store,
        task_event_store=event_store,
        task_role_profile_store=profile_store,
        user_role_session_store=session_store,
        task_store=task_store,
        status_reader=status_reader,
        # Negative threshold so freshly-created events qualify immediately when
        # idle (age 0 > -1). Tests that need "wait because young" raise it.
        age_threshold_s=-1.0,
        batch_size=10,
    )
    configure_broker_packager(packager)
    return {
        "agent_store": agent_store,
        "task_store": task_store,
        "event_store": event_store,
        "worker_store": worker_store,
        "conversation_store": conversation_store,
        "profile_store": profile_store,
        "session_store": session_store,
        "queue_store": queue_store,
        "user_id": user_id,
        "broker_conv_id": broker_conv.id,
        "packager": packager,
        "status_reader": status_reader,
    }


@pytest.fixture(autouse=True)
def _clear_packager() -> None:
    yield
    configure_broker_packager(None)


def _key(user_id: str) -> AgentQueueKey:
    return AgentQueueKey(role=TASK_BROKER_ROLE, owner_user_id=user_id)


def _lazy_packager(
    db_uri: str,
    tmp_path: Path,
    *,
    with_live_host: bool,
) -> tuple[BrokerPackager, SqlAlchemyTaskRoleProfileStore, SqlAlchemyUserRoleSessionStore]:
    """Build a broker packager wired for on-demand session bootstrap, with no role."""
    agent_store = SqlAlchemyAgentStore(db_uri)
    agent_store.create(generate_agent_id(), name="task-broker", bundle_location="test:///bundle")
    conversation_store = SqlAlchemyConversationStore(db_uri)
    artifact_store = LocalArtifactStore(str(tmp_path / "artifacts"))
    init_runtime(
        conversation_store=conversation_store,
        agent_store=agent_store,
        agent_cache=AgentCache(artifact_store=artifact_store, cache_dir=tmp_path / ".cache"),
        artifact_store=artifact_store,
    )
    host_store = HostStore(db_uri)
    if with_live_host:
        host_store.upsert_on_connect(_uid("lazy_host"), "lazy-host", RESERVED_USER_LOCAL)
    profile_store = SqlAlchemyTaskRoleProfileStore(db_uri)
    session_store = SqlAlchemyUserRoleSessionStore(db_uri)
    queue_store = SqlAlchemyAgentQueueStore(db_uri)

    async def _mock_session_creator(*, body, request, user_id, **kwargs):
        return conversation_store.create_conversation(
            title=body.title or "Task broker",
            agent_id=body.agent_id,
            host_id=body.host_id,
            workspace=body.workspace,
        )

    from types import SimpleNamespace

    packager = BrokerPackager(
        store=queue_store,
        task_event_store=SqlAlchemyTaskEventStore(db_uri),
        task_role_profile_store=profile_store,
        user_role_session_store=session_store,
        task_store=SqlAlchemyTaskStore(db_uri),
        status_reader=_StaticStatusReader("idle"),
        conversation_store=conversation_store,
        agent_store=agent_store,
        host_store=host_store,
        session_creator=_mock_session_creator,
        app_state=SimpleNamespace(),
        age_threshold_s=-1.0,
        batch_size=10,
    )
    return packager, profile_store, session_store


@pytest.mark.asyncio
async def test_packager_boots_broker_session_on_demand(db_uri: str, tmp_path: Path) -> None:
    """The broker has no UI rail, so the packager provisions its session itself."""
    packager, profile_store, session_store = _lazy_packager(db_uri, tmp_path, with_live_host=True)
    event_store = SqlAlchemyTaskEventStore(db_uri)
    assert profile_store.get(TASK_BROKER_ROLE) is None

    event_store.create_event(
        _uid("lazy_evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )
    await packager.scan_once()

    assert profile_store.get(TASK_BROKER_ROLE) is not None
    session = session_store.get("__anonymous__", TASK_BROKER_ROLE)
    assert session is not None
    assert session.conversation_id is not None
    assert len(packager._store.list_items(_key("__anonymous__"))) == 1


@pytest.mark.asyncio
async def test_packager_leaves_events_queued_without_a_live_host(
    db_uri: str,
    tmp_path: Path,
) -> None:
    """No host means no broker to boot, so events stay in awaiting_grouping."""
    packager, profile_store, session_store = _lazy_packager(db_uri, tmp_path, with_live_host=False)
    event_store = SqlAlchemyTaskEventStore(db_uri)

    event_store.create_event(
        _uid("lazy_evt_nohost"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id="__anonymous__",
    )
    await packager.scan_once()

    assert profile_store.get(TASK_BROKER_ROLE) is None
    assert session_store.get("__anonymous__", TASK_BROKER_ROLE) is None
    assert packager._store.list_items(_key("__anonymous__")) == []


@pytest.mark.asyncio
async def test_full_batch_sends_regardless_of_agent_state(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "running"  # agent busy
    packager._batch_size = 3

    for i in range(3):
        event_store.create_event(
            _uid(f"evt{i}"),
            "build.finished",
            f"Ambiguous {i}",
            state="awaiting_grouping",
            owner_user_id=broker_setup["user_id"],
        )
    await packager.scan_once()

    assert len(queue_store.list_items(_key(broker_setup["user_id"]))) == 1


@pytest.mark.asyncio
async def test_partial_batch_waits_when_agent_busy(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "running"
    packager._age_threshold_s = -1.0  # age floor would otherwise force a send

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()

    assert queue_store.list_items(_key(broker_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_partial_batch_sends_when_idle_and_age_exceeded(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0  # oldest age > 0 immediately

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    assert "[System: please triage and route these events]" in items[0].payload


@pytest.mark.asyncio
async def test_partial_batch_waits_when_idle_but_young(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = 3600  # far above any real age

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()

    assert queue_store.list_items(_key(broker_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_stall_via_ingress_is_picked_up_by_poll(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_id = _uid("stall_event")
    event = event_store.create_event(
        event_id,
        "build.finished",
        "Ambiguous build",
        state="received",
    )
    updated = await ingress_event(
        event=event,
        task_store=broker_setup["task_store"],
        task_event_store=event_store,
        worker_store=broker_setup["worker_store"],
        conversation_store=broker_setup["conversation_store"],
        owner_user_id=broker_setup["user_id"],
    )
    assert updated.state == "awaiting_grouping"
    assert updated.owner_user_id == broker_setup["user_id"]
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    assert items[0].source_ids == [event_id]


@pytest.mark.asyncio
async def test_claimed_events_are_not_repackaged(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_store.create_event(
        _uid("evt"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()  # packages it
    await packager.scan_once()  # should not duplicate

    assert len(queue_store.list_items(_key(broker_setup["user_id"]))) == 1


@pytest.mark.asyncio
async def test_stale_events_routed_away_are_filtered(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event = event_store.create_event(
        _uid("stale"),
        "build.finished",
        "Already routed",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    event_store.update_event(event.id, state="routed")
    await packager.scan_once()

    assert queue_store.list_items(_key(broker_setup["user_id"])) == []


@pytest.mark.asyncio
async def test_no_live_broker_holds_events(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    # A user with no broker session binding.
    event_store.create_event(
        _uid("orphan"),
        "build.finished",
        "Ambiguous",
        state="awaiting_grouping",
        owner_user_id="nobody",
    )
    await packager.scan_once()

    assert queue_store.list_items(_key("nobody")) == []


def test_defaults_are_configurable_constants() -> None:
    assert DEFAULT_PACKAGER_POLL_INTERVAL_S == 5.0
    assert DEFAULT_PACKAGER_AGE_THRESHOLD_S == 15


@pytest.mark.asyncio
async def test_orphan_session_event_is_packaged_like_any_stall(
    broker_setup: dict,
) -> None:
    """An orphan session becomes an awaiting_grouping event the packager polls."""
    from omnigent.agent_tasks.adoption import (
        SessionAdoptionContext,
        configure_session_adoption,
        enqueue_orphan_session,
    )
    from omnigent.agent_tasks.event_types import SESSION_ORPHAN_EVENT_TYPE

    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    configure_session_adoption(
        SessionAdoptionContext(
            task_store=broker_setup["task_store"],
            task_event_store=event_store,
            worker_store=broker_setup["worker_store"],
            conversation_store=broker_setup["conversation_store"],
        )
    )
    conv = broker_setup["conversation_store"].create_conversation(
        title="Mystery session",
        agent_id=broker_setup["agent_store"].get_by_name("task-manager-agent").id,
        host_id=_uid("host_orphan"),
        workspace="/tmp/mystery",
    )
    await enqueue_orphan_session(conv.id, owner_user_id=broker_setup["user_id"])
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    assert "session.orphan" in items[0].payload
    assert "Mystery session" in items[0].payload
    assert "routing_repo" in items[0].payload  # orphan-specific guidance
    # The packaged source is the orphan event, not the session id directly.
    orphan_events = event_store.list_events(
        state="awaiting_grouping", event_type=SESSION_ORPHAN_EVENT_TYPE
    )
    assert items[0].source_ids == [orphan_events[0].id]


def test_get_broker_packager_returns_configured(broker_setup: dict) -> None:
    assert get_broker_packager() is broker_setup["packager"]


def test_get_broker_packager_none_when_unconfigured() -> None:
    configure_broker_packager(None)
    assert get_broker_packager() is None


# ── Level 2: tag-similarity clustering, orphan isolation, self-contained notice ──


def _evt(
    eid: str,
    *,
    tags: list[EventTag] | None = None,
    created_at: int = 0,
    event_type: str = "build.finished",
    title: str = "x",
) -> TaskEvent:
    return TaskEvent(
        id=eid,
        event_type=event_type,
        title=title,
        state="awaiting_grouping",
        created_at=created_at,
        tags=tags,
    )


def test_cluster_events_by_similarity_groups_overlap_and_keeps_oldest_first() -> None:
    shared = [
        EventTag(tag_type="repo", tag="r"),
        EventTag(tag_type="branch", tag="b"),
        EventTag(tag_type="file", tag="f"),
        EventTag(tag_type="line", tag="1"),
    ]
    e_old = _evt("old", tags=[*shared, EventTag(tag_type="severity", tag="high")], created_at=100)
    e_new = _evt("new", tags=[*shared, EventTag(tag_type="severity", tag="low")], created_at=200)
    # 4 of 5 tags shared → overlap 0.8 ≥ threshold → one cluster, oldest first.
    clusters = cluster_events_by_similarity([e_new, e_old], threshold=0.8)
    assert len(clusters) == 1
    assert [e.id for e in clusters[0].events] == ["old", "new"]


def test_cluster_events_by_similarity_separates_low_overlap() -> None:
    e1 = _evt("a", tags=[EventTag(tag_type="repo", tag="x")], created_at=100)
    e2 = _evt("b", tags=[EventTag(tag_type="repo", tag="y")], created_at=200)
    clusters = cluster_events_by_similarity([e1, e2], threshold=0.8)
    assert len(clusters) == 2


def test_cluster_events_by_similarity_buckets_tagless() -> None:
    e1 = _evt("a", tags=None, created_at=100)
    e2 = _evt("b", tags=None, created_at=200)
    e3 = _evt("c", tags=[EventTag(tag_type="repo", tag="r")], created_at=300)
    clusters = cluster_events_by_similarity([e1, e2, e3], threshold=0.8)
    assert len(clusters) == 2
    tagless = [c for c in clusters if not c.events[0].tags]
    assert {e.id for e in tagless[0].events} == {"a", "b"}


@pytest.mark.asyncio
async def test_similar_events_packaged_into_one_notice_with_candidates(
    broker_setup: dict,
) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    task_store: SqlAlchemyTaskStore = broker_setup["task_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    task_id = _uid("task")
    task_store.create(
        task_id,
        "Widget CI",
        "widget ci green",
        state="pending",
        tags=[TaskTag(task_id=task_id, tag_type="repo", tag="acme/widgets")],
    )
    shared = [
        EventTag(tag_type="repo", tag="acme/widgets"),
        EventTag(tag_type="branch", tag="main"),
        EventTag(tag_type="file", tag="a"),
        EventTag(tag_type="line", tag="1"),
    ]
    for i, sev in enumerate(("high", "low")):
        event_store.create_event(
            _uid(f"sim{i}"),
            "build.finished",
            f"Build {i}",
            state="awaiting_grouping",
            owner_user_id=broker_setup["user_id"],
            tags=[*shared, EventTag(tag_type="severity", tag=sev)],
        )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    payload = json.loads(items[0].payload)
    assert "possible clusters waiting for route/reconcile" in payload["prompt"]
    assert len(payload["clusters"]) == 1
    cluster_events = payload["clusters"][0]["events"]
    assert len(cluster_events) == 2
    assert task_id in payload["candidate_task_ids"]


@pytest.mark.asyncio
async def test_orphan_is_isolated_from_routed_events(broker_setup: dict) -> None:
    from omnigent.agent_tasks.adoption import (
        SessionAdoptionContext,
        configure_session_adoption,
        enqueue_orphan_session,
    )

    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    configure_session_adoption(
        SessionAdoptionContext(
            task_store=broker_setup["task_store"],
            task_event_store=event_store,
            worker_store=broker_setup["worker_store"],
            conversation_store=broker_setup["conversation_store"],
        )
    )
    event_store.create_event(
        _uid("routed"),
        "build.finished",
        "Routed event",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    conv = broker_setup["conversation_store"].create_conversation(
        title="Mystery session",
        agent_id=broker_setup["agent_store"].get_by_name("task-manager-agent").id,
        host_id=_uid("host_orphan"),
        workspace="/tmp/mystery",
    )
    await enqueue_orphan_session(conv.id, owner_user_id=broker_setup["user_id"])
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 2
    by_kind = {json.loads(it.payload).get("candidate_task_ids") is not None for it in items}
    # One notice carries candidates (routed), the other does not (orphan).
    assert by_kind == {True, False}
    orphan_item = next(
        it for it in items if json.loads(it.payload).get("candidate_task_ids") is None
    )
    orphan_payload = json.loads(orphan_item.payload)
    assert "routing_repo" in orphan_payload["prompt"]
    assert orphan_payload["events"][0]["event_type"] == "session.orphan"


@pytest.mark.asyncio
async def test_cluster_larger_than_batch_size_is_capped_and_defers_rest(
    broker_setup: dict,
) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0
    packager._batch_size = 2

    tags = [EventTag(tag_type="repo", tag="acme/widgets")]
    for i in range(3):
        event_store.create_event(
            _uid(f"cap{i}"),
            "build.finished",
            f"Build {i}",
            state="awaiting_grouping",
            owner_user_id=broker_setup["user_id"],
            tags=tags,
        )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    payload = json.loads(items[0].payload)
    # Capped at batch_size=2; one event deferred.
    assert len(payload["clusters"][0]["events"]) == 2
    # The deferred event is still awaiting_grouping and ships on the next poll.
    await packager.scan_once()
    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 2
    second = json.loads(items[1].payload)
    assert len(second["clusters"][0]["events"]) == 1


@pytest.mark.asyncio
async def test_small_clusters_fill_into_one_notice(broker_setup: dict) -> None:
    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    event_store.create_event(
        _uid("fill_a"),
        "build.finished",
        "A",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
        tags=[EventTag(tag_type="repo", tag="x")],
    )
    event_store.create_event(
        _uid("fill_b"),
        "build.finished",
        "B",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
        tags=[EventTag(tag_type="repo", tag="y")],
    )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    payload = json.loads(items[0].payload)
    # Two dissimilar events → two clusters, packed into one notice.
    assert len(payload["clusters"]) == 2
    assert {c["events"][0]["id"] for c in payload["clusters"]} == {
        _uid("fill_a"),
        _uid("fill_b"),
    }


# ── External session discovered ──────────────────────────────────────


@pytest.mark.asyncio
async def test_discovered_session_is_packaged_as_orphan_style(
    broker_setup: dict,
) -> None:
    """An external.session.discovered event is packaged one-per-notice."""
    from omnigent.agent_tasks.event_types import EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE

    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    payload = json.dumps(
        {
            "session_hint": "codex-abc",
            "path": "/home/user/.codex/sessions/abc",
            "tool": "codex",
            "history_hash": "h1",
            "transcript_snippet": "user asked about fixing a bug in the parser",
        }
    )
    event_store.create_event(
        _uid("disc"),
        EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
        "External session discovered",
        payload=payload,
        source="session_watcher",
        source_key="codex-abc",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 1
    notice = json.loads(items[0].payload)
    # Discovered sessions use the orphan-style flat events list (no clusters).
    assert "clusters" not in notice
    assert "candidate_task_ids" not in notice
    assert notice["events"][0]["event_type"] == "external.session.discovered"
    # The prompt mentions the three triage outcomes.
    assert "transcript_snippet" in notice["prompt"]
    assert "propose-adoption" in notice["prompt"]
    assert "fyi" in notice["prompt"].lower()


@pytest.mark.asyncio
async def test_discovered_session_is_isolated_from_routed_events(
    broker_setup: dict,
) -> None:
    """Discovered sessions and routed events produce separate notices."""
    from omnigent.agent_tasks.event_types import EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE

    event_store: SqlAlchemyTaskEventStore = broker_setup["event_store"]
    queue_store: SqlAlchemyAgentQueueStore = broker_setup["queue_store"]
    packager: BrokerPackager = broker_setup["packager"]
    broker_setup["status_reader"].status = "idle"
    packager._age_threshold_s = -1.0

    # A routed event
    event_store.create_event(
        _uid("routed_disc"),
        "build.finished",
        "Build failed",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
        tags=[EventTag(tag_type="repo", tag="myrepo")],
    )
    task_store: SqlAlchemyTaskStore = broker_setup["task_store"]
    task_store.create(
        _uid("task_disc"),
        "Disc task",
        "disc goal",
        tags=[TaskTag(task_id=_uid("task_disc"), tag_type="repo", tag="myrepo")],
    )
    # A discovered session
    event_store.create_event(
        _uid("disc2"),
        EXTERNAL_SESSION_DISCOVERED_EVENT_TYPE,
        "External session discovered",
        payload=json.dumps({"session_hint": "codex-xyz", "transcript_snippet": "..."}),
        source="session_watcher",
        source_key="codex-xyz",
        state="awaiting_grouping",
        owner_user_id=broker_setup["user_id"],
    )
    await packager.scan_once()

    items = queue_store.list_items(_key(broker_setup["user_id"]))
    assert len(items) == 2
    # One notice has clusters (routed), the other has flat events (discovered).
    has_clusters = [it for it in items if "clusters" in json.loads(it.payload)]
    has_events = [
        it
        for it in items
        if "events" in json.loads(it.payload) and "clusters" not in json.loads(it.payload)
    ]
    assert len(has_clusters) == 1
    assert len(has_events) == 1
    assert (
        json.loads(has_events[0].payload)["events"][0]["event_type"]
        == "external.session.discovered"
    )
