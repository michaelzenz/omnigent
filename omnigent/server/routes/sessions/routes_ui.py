"""Session-scoped requests to the UI: open a file, and the folders an agent stamped."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.errors import ErrorCode, OmnigentError
from omnigent.runtime import session_stream
from omnigent.server.auth import LEVEL_EDIT, LEVEL_READ, AuthProvider
from omnigent.server.routes._auth_helpers import get_user_id, require_access_and_level
from omnigent.server.routes._errors import session_not_found
from omnigent.server.schemas import (
    SessionOpenFolder,
    SessionOpenFolderList,
    UiOpenFileEvent,
    UiOpenFoldersEvent,
)
from omnigent.stores import ConversationStore
from omnigent.stores.permission_store import PermissionStore
from omnigent.stores.session_open_folder_store import (
    OpenFolder,
    OpenFolderLimitError,
    SessionOpenFolderStore,
)
from omnigent.tools.builtins.ui import MAX_OPEN_FOLDERS, OpenFileRequest, StampFoldersRequest


class OpenFileResponse(BaseModel):
    """Delivery to live streams is not an acknowledgement from a file viewer."""

    subscribers: int


def _folder_list(folders: list[OpenFolder]) -> SessionOpenFolderList:
    return SessionOpenFolderList(
        folders=[SessionOpenFolder(title=folder.title, path=folder.path) for folder in folders]
    )


def register_ui_routes(
    router: APIRouter,
    *,
    conversation_store: ConversationStore,
    auth_provider: AuthProvider | None = None,
    permission_store: PermissionStore | None = None,
) -> None:
    """Register the agent-facing UI endpoints."""

    @router.post(
        "/sessions/{session_id}/ui/open-file",
        status_code=202,
        response_model=OpenFileResponse,
    )
    async def open_file(
        request: Request, session_id: str, body: OpenFileRequest
    ) -> OpenFileResponse:
        """Ask current viewers to open a workspace-relative file.

        Requires session edit access. Requests are transient and never replayed.
        The subscriber count measures stream delivery, not successful display:
        background subscribers may ignore the request, and the viewer retains
        its normal filesystem permissions, error handling and unsaved-edit guard.
        """
        await require_access_and_level(
            get_user_id(request, auth_provider),
            session_id,
            LEVEL_EDIT,
            permission_store,
            conversation_store,
        )
        event = UiOpenFileEvent(type="ui.open_file", path=body.path)
        return OpenFileResponse(subscribers=session_stream.publish(session_id, event.model_dump()))

    # The stamped folders live beside the session metadata.
    location = getattr(conversation_store, "storage_location", None)
    if not isinstance(location, str) or not location:
        return
    folder_store = SessionOpenFolderStore(location)

    @router.get("/sessions/{session_id}/ui/open-folders", response_model=SessionOpenFolderList)
    async def list_open_folders(request: Request, session_id: str) -> SessionOpenFolderList:
        """List the folders that the session's agent stamped, oldest first.

        Requires session read access. Each path is on the session's host; the
        browser decides whether it can open it here.
        """
        await require_access_and_level(
            get_user_id(request, auth_provider),
            session_id,
            LEVEL_READ,
            permission_store,
            conversation_store,
        )
        folders = await asyncio.to_thread(folder_store.read, session_id)
        if folders is None:
            raise session_not_found()
        return _folder_list(folders)

    @router.post("/sessions/{session_id}/ui/open-folders", response_model=SessionOpenFolderList)
    async def stamp_open_folders(
        request: Request, session_id: str, body: StampFoldersRequest
    ) -> SessionOpenFolderList:
        """Add or retitle stamped folders and drop removed paths; return the full list.

        Requires session edit access. The session's runner checks that each
        folder exists before it calls this route. Current viewers get the new
        list on the session stream.
        """
        await require_access_and_level(
            get_user_id(request, auth_provider),
            session_id,
            LEVEL_EDIT,
            permission_store,
            conversation_store,
        )
        stamped = [OpenFolder(title=folder.title, path=folder.path) for folder in body.folders]
        try:
            folders = await asyncio.to_thread(
                folder_store.stamp,
                session_id,
                stamped,
                body.remove,
                max_folders=MAX_OPEN_FOLDERS,
            )
        except OpenFolderLimitError as exc:
            raise OmnigentError(str(exc), code=ErrorCode.INVALID_INPUT) from exc
        if folders is None:
            raise session_not_found()
        result = _folder_list(folders)
        event = UiOpenFoldersEvent(type="ui.open_folders", folders=result.folders)
        session_stream.publish(session_id, event.model_dump())
        return result
