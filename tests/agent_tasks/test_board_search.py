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

    def __init__(self, db_uri: str, seed: str, *, title: str = "Anchor title") -> None:
        self.task_store = SqlAlchemyTaskStore(db_uri)
        self.item_store = SqlAlchemyTaskItemStore(db_uri)
        self.worker_store = SqlAlchemyWorkerStore(db_uri)
        self.asset_store = SqlAlchemyTaskAssetStore(db_uri)
        self.task_id = self.add_task("base", title=title)

    def add_task(self, seed: str, *, title: str, goal: str = "") -> str:
        task_id = _uid(f"{seed}_task")
        self.task_store.create(task_id, title, goal, state="active")
        return task_id

    def add_item(
        self,
        seed: str,
        *,
        title: str,
        description: str | None = None,
        instructions: str | None = None,
        worker_id: str | None = None,
        state: str = "pending",
        task_id: str | None = None,
    ) -> str:
        item_id = _uid(f"{seed}_item")
        self.item_store.create_item(
            item_id,
            task_id or self.task_id,
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


def test_token_and_matches_across_entities(db_uri: str) -> None:
    """Tokens may land in different fields: the title carries one token, an
    item title the other — the task still matches and both entities ring."""
    board = BoardFixture(db_uri, "cross_entity", title="Migrate external tables")
    item_id = board.add_item("i1", title="optimize run failed")
    results = board.search("migrate   optimize")
    assert len(results) == 1
    assert results[0]["matched_in"] == ["item", "task"]
    assert results[0]["item_ids"] == [item_id]


def test_token_and_requires_every_token(db_uri: str) -> None:
    board = BoardFixture(db_uri, "partial_token")
    board.add_item("i1", title="optimize run failed")
    assert board.search("migrate optimize") == []


def test_results_ranked_best_match_first(db_uri: str) -> None:
    """Exact phrase in the title > all tokens in the title > tokens spread
    across a title and an item title."""
    board = BoardFixture(db_uri, "ranking")
    phrase_id = board.add_task("phrase", title="migrate optimize now")
    titled_id = board.add_task("titled", title="optimize and migrate tables")
    spread_id = board.add_task("spread", title="Migrate external tables")
    board.add_item("i1", title="optimize run failed", task_id=spread_id)

    results = board.search("migrate optimize")
    assert [result["task_id"] for result in results] == [phrase_id, titled_id, spread_id]
    scores = [result["score"] for result in results]
    assert scores[0] > scores[1] > scores[2]


def test_nonmatching_query_excludes_task(db_uri: str) -> None:
    board = BoardFixture(db_uri, "no_match")
    board.add_item("i1", title="Unrelated item")
    assert board.search("zzz-no-such-text") == []


def test_empty_query_returns_no_results(db_uri: str) -> None:
    board = BoardFixture(db_uri, "empty_query")
    assert board.search("") == []
    assert board.search("   ") == []


def test_terminated_worker_is_out_of_scope(db_uri: str) -> None:
    """Terminated workers are untracked and never rendered — their lane text
    must not put a task in the results, mirroring the dashboard's exclusion."""
    board = BoardFixture(db_uri, "terminated_worker")
    board.add_worker("w1", title="Ghost worker", state="terminated")
    assert board.search("ghost worker") == []
