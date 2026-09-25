"""Tests for the pmv2 board search read model and its chat scan."""

from __future__ import annotations

import uuid

from omnigent.agent_tasks.board_search import search_board_tasks
from omnigent.stores.task_asset_store.sqlalchemy_store import SqlAlchemyTaskAssetStore
from omnigent.stores.task_item_store.sqlalchemy_store import SqlAlchemyTaskItemStore
from omnigent.stores.task_store.sqlalchemy_store import SqlAlchemyTaskStore
from omnigent.stores.worker_store.sqlalchemy_store import SqlAlchemyWorkerStore


def _uid(seed: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_DNS, seed).hex


class BoardFixture:
    """One task with stores wired up, mirroring the board-search route flow."""

    def __init__(self, db_uri: str, seed: str) -> None:
        self.task_store = SqlAlchemyTaskStore(db_uri)
        self.item_store = SqlAlchemyTaskItemStore(db_uri)
        self.worker_store = SqlAlchemyWorkerStore(db_uri)
        self.asset_store = SqlAlchemyTaskAssetStore(db_uri)
        self.task_id = _uid(f"{seed}_task")
        self.task_store.create(self.task_id, "Anchor title", "anchor goal", state="active")

    def add_item(
        self,
        seed: str,
        *,
        title: str,
        description: str | None = None,
        instructions: str | None = None,
        worker_id: str | None = None,
        state: str = "pending",
    ) -> str:
        item_id = _uid(f"{seed}_item")
        self.item_store.create_item(
            item_id,
            self.task_id,
            title,
            state=state,
            description=description,
            instructions=instructions,
            worker_id=worker_id,
        )
        return item_id

    def add_worker(
        self,
        seed: str,
        *,
        title: str | None = None,
        provider_name: str | None = None,
        target_id: str | None = None,
        state: str = "idle",
    ) -> str:
        worker_id = _uid(f"{seed}_worker")
        self.worker_store.create_worker(
            worker_id,
            self.task_id,
            title=title,
            provider_name=provider_name,
            target_id=target_id,
            state=state,
        )
        return worker_id

    def add_asset(self, seed: str, *, title: str, url: str) -> int:
        asset = self.asset_store.create_asset(self.task_id, kind="url", title=title, url=url)
        return asset.id

    def search(self, query: str) -> list[dict]:
        """Run the same pipeline the board-search route runs (bulk loads)."""
        tasks = [t for t in self.task_store.list() if t.state != "archived"]
        task_ids = [t.id for t in tasks]
        items = self.item_store.list_items_for_tasks(task_ids)
        # Mirror the route: terminated/deleted workers are out of scope.
        workers = [
            w
            for w in self.worker_store.list_workers_for_tasks(task_ids)
            if w.state not in ("terminated", "deleted")
        ]
        assets = self.asset_store.list_assets_for_tasks(task_ids)
        return search_board_tasks(
            tasks,
            query=query,
            items=items,
            workers=workers,
            assets=assets,
        )


def test_matches_task_fields(db_uri: str) -> None:
    board = BoardFixture(db_uri, "task_fields")
    results = board.search("anchor title")
    assert len(results) == 1
    assert results[0]["task_id"] == board.task_id
    assert results[0]["matched_in"] == ["task"]
    assert results[0]["item_ids"] == []
    assert results[0]["asset_ids"] == []
    assert results[0]["worker_ids"] == []


def test_matches_item_text(db_uri: str) -> None:
    board = BoardFixture(db_uri, "item_text")
    item_id = board.add_item(
        "i1",
        title="Retries",
        description="fix the upload retries",
        instructions=None,
    )
    results = board.search("upload retries")
    assert len(results) == 1
    assert results[0]["matched_in"] == ["item"]
    assert results[0]["item_ids"] == [item_id]


def test_matches_asset_title_and_url(db_uri: str) -> None:
    board = BoardFixture(db_uri, "asset_match")
    asset_id = board.add_asset("a1", title="Hotfix PR", url="https://git/pr/123")
    results = board.search("hotfix pr")
    assert results[0]["asset_ids"] == [asset_id]
    by_url = board.search("git/pr/123")
    assert by_url[0]["asset_ids"] == [asset_id]


def test_matches_worker_lane_text(db_uri: str) -> None:
    board = BoardFixture(db_uri, "worker_lane")
    worker_id = board.add_worker("w1", title="Frontend worker", provider_name="claude")
    results = board.search("frontend worker")
    assert results[0]["worker_ids"] == [worker_id]
    assert results[0]["matched_in"] == ["worker"]


def test_nonmatching_query_excludes_task(db_uri: str) -> None:
    board = BoardFixture(db_uri, "no_match")
    board.add_item("i1", title="Unrelated item")
    assert board.search("zzz-no-such-text") == []


def test_empty_query_returns_no_results(db_uri: str) -> None:
    board = BoardFixture(db_uri, "empty_query")
    assert board.search("") == []
    assert board.search("   ") == []


def test_results_are_in_board_order(db_uri: str) -> None:
    """Results follow the board's sort-order column (queue_rank desc, id desc) —
    the board filters its cards in place, so search must not reorder them."""
    board = BoardFixture(db_uri, "order")
    first = board.task_store.create(_uid("order_t1"), "Sync report", "sync goal", state="active")
    second = board.task_store.create(_uid("order_t2"), "Sync report", "sync goal", state="active")
    third = board.task_store.create(_uid("order_t3"), "Sync report", "sync goal", state="active")
    assert [t.queue_rank for t in (first, second, third)] == [1, 2, 3]

    results = board.search("sync")
    assert [r["task_id"] for r in results] == [third.id, second.id, first.id]


def test_terminated_worker_is_out_of_scope(db_uri: str) -> None:
    """Terminated workers are untracked and never rendered — their lane text
    must not put a task in the results, mirroring the dashboard's exclusion."""
    board = BoardFixture(db_uri, "terminated_worker")
    board.add_worker("w1", title="Ghost worker", state="terminated")
    assert board.search("ghost worker") == []
