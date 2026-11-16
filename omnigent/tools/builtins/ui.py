"""Agent requests to the current session's UI: open a file, or stamp folders to open."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from omnigent.tools.base import Tool

MAX_OPEN_FOLDERS = 20
MAX_OPEN_FOLDER_TITLE_CHARS = 80
MAX_OPEN_FOLDER_PATH_CHARS = 1024
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")


def _has_control_characters(text: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in text)


def normalize_folder_path(path: str) -> str:
    """Return the absolute ``path`` without trailing separators.

    :raises ValueError: The path is relative, has control characters or surrounding
        spaces, or has an empty, ``.`` or ``..`` segment.
    """
    if path != path.strip() or _has_control_characters(path):
        raise ValueError("path must be one line without surrounding spaces")
    windows = _WINDOWS_DRIVE.match(path) is not None
    if not windows and not path.startswith("/"):
        raise ValueError("path must be absolute, for example /home/me/repo or C:\\repo")
    separators = "\\/" if windows else "/"
    root_length = 3 if windows else 1
    rest = path[root_length:].rstrip(separators)
    if rest and any(
        part in ("", ".", "..") for part in re.split(r"[\\/]" if windows else "/", rest)
    ):
        raise ValueError("path must not have empty, '.' or '..' segments")
    return path[:root_length] + rest


class OpenFileRequest(BaseModel):
    """A workspace-relative file to show, without reading or modifying it."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096, description="Workspace-relative file path.")

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        """Reject absolute paths, traversal, URLs and control characters."""
        parsed = PurePosixPath(path)
        if (
            not path.strip()
            or path.startswith(("/", "~"))
            or ".." in parsed.parts
            or not parsed.parts
            or path.endswith("/")
            or any(character in path for character in ("\\", ":"))
            or any(ord(character) < 32 or ord(character) == 127 for character in path)
        ):
            raise ValueError("path must name a workspace-relative file without traversal")
        return str(parsed)


class UiOpenFileTool(Tool):
    """Schema-only tool; the runner posts the request to the session API."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "ui_open_file"

    @classmethod
    def description(cls) -> str:
        """Return the agent-facing contract."""
        return (
            "Open a workspace-relative file in Omnigent UIs currently viewing this session. "
            "Does not modify the file or switch users to another session. Requests are not "
            "replayed later. The result reports stream delivery, not confirmation that a UI "
            "displayed the file; file access errors and unsaved-edit prompts remain in the UI."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": OpenFileRequest.model_json_schema(),
            },
        }


_FolderPath = Annotated[
    str, StringConstraints(min_length=1, max_length=MAX_OPEN_FOLDER_PATH_CHARS)
]


class FolderStamp(BaseModel):
    """A folder the user can open in an editor: its title and absolute path."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_OPEN_FOLDER_TITLE_CHARS)
    path: _FolderPath

    @field_validator("title")
    @classmethod
    def validate_title(cls, title: str) -> str:
        """Keep the title on one line, without surrounding spaces."""
        cleaned = title.strip()
        if not cleaned or _has_control_characters(cleaned):
            raise ValueError("title must be visible text on one line")
        return cleaned

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        """Keep one spelling of each absolute path."""
        return normalize_folder_path(path)


class StampFoldersRequest(BaseModel):
    """Folders to add or retitle, and paths to drop, by absolute path."""

    model_config = ConfigDict(extra="forbid")

    folders: list[FolderStamp] = Field(default_factory=list, max_length=MAX_OPEN_FOLDERS)
    remove: list[_FolderPath] = Field(default_factory=list, max_length=MAX_OPEN_FOLDERS)

    @field_validator("remove")
    @classmethod
    def validate_remove(cls, paths: list[str]) -> list[str]:
        """Match removals against the stamped spelling of each path."""
        return [normalize_folder_path(path) for path in paths]

    @model_validator(mode="after")
    def validate_overlap(self) -> StampFoldersRequest:
        """Refuse a path that the same call stamps and removes."""
        if {folder.path for folder in self.folders} & set(self.remove):
            raise ValueError("a path cannot be both stamped and removed")
        return self


class UiStampFoldersTool(Tool):
    """Schema-only tool; the runner checks the folders, then posts to the session API."""

    @classmethod
    def name(cls) -> str:
        """Return the tool name."""
        return "ui_stamp_folders"

    @classmethod
    def description(cls) -> str:
        """Return the agent-facing contract."""
        return (
            "Stamp the folders you work in, so the user can open them in Cursor or VS Code "
            "from this session's Open button. Stamp each folder where you change "
            "files for this session, such as a repository checkout or a git worktree you "
            "create, as soon as you start work there. Give each a short title, for example "
            "the pr title or task name, and its absolute path on this machine; the folder "
            "must exist. A call adds new paths and retitles stamped ones, and `remove` drops "
            "folders you no longer work in. The result lists every stamped folder; a session "
            f"keeps at most {MAX_OPEN_FOLDERS}."
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the OpenAI-format tool schema, without references between definitions."""
        path = {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_OPEN_FOLDER_PATH_CHARS,
            "description": "Absolute folder path, for example /home/me/repo.",
        }
        folder = {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": MAX_OPEN_FOLDER_TITLE_CHARS,
                    "description": "Short label the user sees, for example the repository name.",
                },
                "path": path,
            },
            "required": ["title", "path"],
            "additionalProperties": False,
        }
        return {
            "type": "function",
            "function": {
                "name": self.name(),
                "description": self.description(),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "folders": {
                            "type": "array",
                            "items": folder,
                            "maxItems": MAX_OPEN_FOLDERS,
                            "description": "Folders to stamp; a stamped path gets the new title.",
                        },
                        "remove": {
                            "type": "array",
                            "items": path,
                            "maxItems": MAX_OPEN_FOLDERS,
                            "description": "Stamped paths to drop.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
        }
