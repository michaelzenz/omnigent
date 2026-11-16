"""Folder-stamping tool schema, validation and runner/native dispatch."""

import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from omnigent.runner.tool_dispatch import (
    build_native_relay_tool_schemas,
    execute_tool,
    should_dispatch_locally,
)
from omnigent.spec.types import AgentSpec
from omnigent.tools.builtins.ui import FolderStamp, StampFoldersRequest, UiStampFoldersTool


@pytest.mark.parametrize("spec", [AgentSpec(spec_version=1), None])
def test_stamp_folders_is_available_to_native_and_local_dispatch(spec: AgentSpec | None) -> None:
    schema = next(
        tool
        for tool in build_native_relay_tool_schemas(spec)
        if tool["name"] == "ui_stamp_folders"
    )
    assert set(schema["parameters"]["properties"]) == {"folders", "remove"}
    assert "$ref" not in json.dumps(schema)
    assert should_dispatch_locally("ui_stamp_folders")
    assert "Stamp the folders you work in" in UiStampFoldersTool.description()


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/home/me/repo/", "/home/me/repo"),
        ("/", "/"),
        ("C:\\work\\repo\\", "C:\\work\\repo"),
        ("C:/work/repo", "C:/work/repo"),
        ("/home/me/my repo", "/home/me/my repo"),
    ],
)
def test_stamp_keeps_one_spelling_of_each_absolute_path(path: str, expected: str) -> None:
    assert FolderStamp(title="Repo", path=path).path == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "repo",
        "~/repo",
        "/a/../b",
        "/a/./b",
        "//server/share",
        "/a//b",
        " /a",
        "/a\nb",
        "\\\\server\\share",
        "/" + "a" * 1024,
    ],
)
def test_stamp_rejects_paths_that_are_not_plain_absolute_folders(path: str) -> None:
    with pytest.raises(ValidationError):
        FolderStamp(title="Repo", path=path)


def test_stamp_trims_titles_and_refuses_unclear_requests() -> None:
    assert FolderStamp(title="  Main repo ", path="/repo").title == "Main repo"
    for title in ("", "   ", "two\nlines", "x" * 81):
        with pytest.raises(ValidationError):
            FolderStamp(title=title, path="/repo")
    with pytest.raises(ValidationError):
        StampFoldersRequest(folders=[FolderStamp(title="Repo", path="/repo")], remove=["/repo/"])
    with pytest.raises(ValidationError):
        StampFoldersRequest.model_validate({"folders": [], "session_id": "other"})
    with pytest.raises(ValidationError):
        StampFoldersRequest(folders=[FolderStamp(title="F", path=f"/f{i}") for i in range(21)])


@pytest.mark.asyncio
async def test_dispatch_checks_folders_here_before_it_saves(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sessions/current/ui/open-folders"
        body = json.loads(request.content)
        requests.append(body)
        if body["folders"] and body["folders"][0]["title"] == "Full":
            message = "A session keeps at most 20 folders; remove some first."
            return httpx.Response(
                400, json={"error": {"code": "invalid_input", "message": message}}
            )
        return httpx.Response(200, json={"folders": body["folders"]})

    async def stamp(arguments: dict) -> dict:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="http://server"
        ) as client:
            output = await execute_tool(
                tool_name="ui_stamp_folders",
                arguments=json.dumps(arguments),
                conversation_id="current",
                server_client=client,
            )
        return json.loads(output)

    missing = await stamp({"folders": [{"title": "Gone", "path": str(tmp_path / "gone")}]})
    assert "not folders on this machine" in missing["error"]
    assert requests == [], "a missing folder must not reach the server"

    saved = await stamp({"folders": [{"title": "Repo", "path": f"{repo}/"}]})
    assert saved == {"folders": [{"title": "Repo", "path": str(repo)}]}

    full = await stamp({"folders": [{"title": "Full", "path": str(repo)}]})
    assert "at most 20 folders" in full["error"]

    no_server = await execute_tool(tool_name="ui_stamp_folders", arguments='{"folders":[]}')
    assert "requires server access" in json.loads(no_server)["error"]
