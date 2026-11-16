"""Folders that a session's agent stamped for the user to open in an editor.

The list is one JSON document on the session's metadata row
(``omnigent_conversation_metadata.open_folders``), so it goes when the session
goes. A stamp adds new paths and retitles stamped ones, a removal drops paths,
and a stamp that changes nothing does not write. An unreadable document reads
as empty, and the next stamp replaces it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from omnigent.db import compression
from omnigent.db.db_models import SqlConversationMetadata, current_workspace_id
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)

_logger = logging.getLogger(__name__)

# Below the 16,384-byte limit on a stored column value, after compression.
_MAX_STORED_BYTES = 16_000
# Headroom for compression framing on incompressible text.
_FRAMING_BYTES = 32


@dataclass(frozen=True)
class OpenFolder:
    """A stamped folder: the title the user sees and its absolute path on the session's host."""

    title: str
    path: str


class OpenFolderLimitError(Exception):
    """The session cannot keep the folders that a stamp would leave."""


def merge_folders(
    current: Sequence[OpenFolder], stamped: Sequence[OpenFolder], removed: Sequence[str]
) -> list[OpenFolder]:
    """Drop ``removed`` paths, retitle stamped paths in place, and add new ones last."""
    dropped = set(removed)
    merged = [folder for folder in current if folder.path not in dropped]
    positions = {folder.path: index for index, folder in enumerate(merged)}
    for folder in stamped:
        position = positions.get(folder.path)
        if position is None:
            positions[folder.path] = len(merged)
            merged.append(folder)
        else:
            merged[position] = folder
    return merged


def _decode(text: str | None) -> list[OpenFolder]:
    if not text:
        return []
    try:
        entries = json.loads(text)
        return [OpenFolder(title=entry["title"], path=entry["path"]) for entry in entries]
    except (ValueError, TypeError, KeyError):
        _logger.warning("Unreadable session open folder list", exc_info=True)
        return []


def _encode(folders: Sequence[OpenFolder]) -> str | None:
    if not folders:
        return None
    entries = [{"title": folder.title, "path": folder.path} for folder in folders]
    return json.dumps(entries, ensure_ascii=False, separators=(",", ":"))


def _fits(text: str | None) -> bool:
    """Whether ``text`` stores in the column's byte budget once compressed."""
    if text is None or len(text.encode()) + _FRAMING_BYTES <= _MAX_STORED_BYTES:
        return True
    return len(compression.encode(text) or b"") <= _MAX_STORED_BYTES


class SessionOpenFolderStore:
    """SQLAlchemy-backed store of the folders stamped in each session."""

    def __init__(self, storage_location: str) -> None:
        """Open the store.

        :param storage_location: SQLAlchemy URI of the database holding
            ``omnigent_conversation_metadata``, e.g. ``"sqlite:///omnigent.db"``.
        """
        self.storage_location = storage_location
        engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            engine, query_name_prefix="omnigent.session_open_folder_store"
        )
        self._session_immediate = make_named_managed_session_maker(
            engine, query_name_prefix="omnigent.session_open_folder_store", immediate=True
        )

    def _load(
        self, session: Session, conversation_id: str, *, lock: bool
    ) -> list[OpenFolder] | None:
        stmt = select(SqlConversationMetadata.open_folders).where(
            SqlConversationMetadata.workspace_id == current_workspace_id(),
            SqlConversationMetadata.id == conversation_id,
        )
        row = session.execute(stmt.with_for_update() if lock else stmt).one_or_none()
        return None if row is None else _decode(row.open_folders)

    def read(self, conversation_id: str) -> list[OpenFolder] | None:
        """Return the session's stamped folders, or ``None`` when the session does not exist.

        :param conversation_id: The session, e.g. ``"0123456789abcdef0123456789abcdef"``.
        """
        with self._session("get_session_open_folders") as session:
            return self._load(session, conversation_id, lock=False)

    def stamp(
        self,
        conversation_id: str,
        folders: Sequence[OpenFolder],
        remove: Sequence[str] = (),
        *,
        max_folders: int,
    ) -> list[OpenFolder] | None:
        """Apply one stamp in a single transaction and return the resulting list.

        :param conversation_id: The session, e.g. ``"0123456789abcdef0123456789abcdef"``.
        :param folders: Folders to add, or to retitle when their path is stamped.
        :param remove: Stamped paths to drop.
        :param max_folders: The most folders the session may keep, e.g. ``20``.
        :returns: The resulting folders, or ``None`` when the session does not exist.
        :raises OpenFolderLimitError: The list would pass ``max_folders`` folders or
            the column's byte budget; nothing is written.
        """

        def write(session: Session) -> list[OpenFolder] | None:
            current = self._load(session, conversation_id, lock=True)
            if current is None:
                return None
            merged = merge_folders(current, folders, remove)
            if merged == current:
                return current
            if len(merged) > max_folders:
                raise OpenFolderLimitError(
                    f"A session keeps at most {max_folders} folders; remove some first."
                )
            text = _encode(merged)
            if not _fits(text):
                raise OpenFolderLimitError(
                    "The folder list is too long to store; remove some folders first."
                )
            session.execute(
                update(SqlConversationMetadata)
                .where(
                    SqlConversationMetadata.workspace_id == current_workspace_id(),
                    SqlConversationMetadata.id == conversation_id,
                )
                .values(open_folders=text)
            )
            return merged

        return cast(
            "list[OpenFolder] | None",
            run_write_transaction(self._session_immediate, "stamp_session_open_folders", write),
        )
