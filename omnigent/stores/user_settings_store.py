"""Per-user settings the server applies on the user's behalf.

Values live in the ``preferences`` table beside the session metadata, so any
deployment with a SQL conversation store has them without extra wiring.
Account deletion already removes a user's ``preferences`` rows.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import zstandard
from sqlalchemy import LargeBinary, delete, select, type_coerce
from sqlalchemy.orm import Session

from omnigent.db.account_authority import require_active_account
from omnigent.db.compression import decode
from omnigent.db.db_models import SqlPreference, current_workspace_id
from omnigent.db.preference_rows import upsert_preference_row
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    run_write_transaction,
)
from omnigent.server.auth import RESERVED_USER_LOCAL
from omnigent.server.server_config import (
    GLOBALHUB_HOST_ID_MAX_CHARS,
    GLOBALHUB_ROLE_DESCRIPTION_MAX_CHARS,
    GLOBALHUB_ROLE_MANUAL_MAX_CHARS,
    GLOBALHUB_ROLE_MODEL_MAX_CHARS,
    GLOBALHUB_WORKSPACE_MAX_CHARS,
    SESSION_TITLE_INSTRUCTIONS_MAX_CHARS,
    normalize_session_title_instructions,
)

_logger = logging.getLogger(__name__)

SESSION_TITLE_INSTRUCTIONS_PREFERENCE_KEY = "session_title_instructions"
# UTF-8 needs at most four bytes per character.
_MAX_DECODED_BYTES = SESSION_TITLE_INSTRUCTIONS_MAX_CHARS * 4
GLOBALHUB_PLACEMENT_PREFERENCE_KEY = "globalhub_placement"
# json.dumps escapes a character outside the Basic Multilingual Plane (an emoji,
# say) as a 12-byte surrogate pair; the keys add a little more.
_JSON_MAX_BYTES_PER_CHAR = 12
_MAX_PLACEMENT_BYTES = (
    GLOBALHUB_WORKSPACE_MAX_CHARS + GLOBALHUB_HOST_ID_MAX_CHARS
) * _JSON_MAX_BYTES_PER_CHAR + 64
GLOBALHUB_ROLE_PREFERENCE_PREFIX = "globalhub_role:"
_MAX_ROLE_OVERRIDE_BYTES = (
    GLOBALHUB_ROLE_DESCRIPTION_MAX_CHARS
    + GLOBALHUB_ROLE_MODEL_MAX_CHARS
    + GLOBALHUB_ROLE_MANUAL_MAX_CHARS
) * _JSON_MAX_BYTES_PER_CHAR + 256
GLOBALHUB_ROLE_SESSION_PREFERENCE_PREFIX = "globalhub_role_session:"
_MAX_SESSION_ID_BYTES = 256
GLOBALHUB_NEW_SESSION_REPORTS_PREFERENCE_KEY = "globalhub_new_session_reports"
# Only the "off" value is stored; no row means the reports are on.
_NEW_SESSION_REPORTS_OFF = "off"


@dataclass(frozen=True)
class GlobalHubPlacement:
    """Where a user's GlobalHub role sessions run.

    :param host_id: One of the user's hosts, e.g. ``"host_a1b2c3d4..."``.
    :param workspace: Canonical absolute path on that host, e.g.
        ``"/Users/alice/globalhub"``.
    """

    host_id: str
    workspace: str


class UserSettingsStore:
    """Read and write one user's server-applied settings.

    ``user_id=None`` is single-user mode and maps to the reserved ``local``
    namespace, matching the other per-user preference readers.
    """

    def __init__(self, storage_location: str) -> None:
        """
        :param storage_location: SQLAlchemy database URI of the Omnigent DB,
            e.g. ``"sqlite:///chat.db"``.
        """
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.user_settings_store"
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine, query_name_prefix="omnigent.user_settings_store", immediate=True
        )

    def get_session_title_instructions(self, *, user_id: str | None) -> str | None:
        """Return the user's saved title instructions, or ``None`` when unset or invalid."""
        value = self._read(
            "read_session_title_instructions",
            SESSION_TITLE_INSTRUCTIONS_PREFERENCE_KEY,
            user_id=user_id,
            max_decoded_bytes=_MAX_DECODED_BYTES,
        )
        if value is None:
            return None
        return normalize_session_title_instructions(
            value, source="saved session title instructions"
        )

    def get_globalhub_placement(self, *, user_id: str | None) -> GlobalHubPlacement | None:
        """Return where the user's GlobalHub role sessions run, or ``None`` when unset."""
        value = self._read(
            "read_globalhub_placement",
            GLOBALHUB_PLACEMENT_PREFERENCE_KEY,
            user_id=user_id,
            max_decoded_bytes=_MAX_PLACEMENT_BYTES,
        )
        if value is None:
            return None
        try:
            stored = json.loads(value)
        except ValueError:
            stored = None
        host_id = stored.get("host_id") if isinstance(stored, dict) else None
        workspace = stored.get("workspace") if isinstance(stored, dict) else None
        if not isinstance(host_id, str) or not isinstance(workspace, str):
            _logger.warning("stored GlobalHub placement is malformed — ignoring it")
            return None
        return GlobalHubPlacement(host_id=host_id, workspace=workspace)

    def save_globalhub_placement(
        self, placement: GlobalHubPlacement | None, *, user_id: str | None
    ) -> None:
        """Save or clear where the user's GlobalHub role sessions run.

        :param placement: The validated host and canonical workspace; ``None``
            clears the setting.
        :param user_id: The authenticated user, or ``None`` in single-user mode.
        """
        value = (
            json.dumps({"host_id": placement.host_id, "workspace": placement.workspace})
            if placement is not None
            else None
        )
        self._write(
            "save_globalhub_placement",
            GLOBALHUB_PLACEMENT_PREFERENCE_KEY,
            value,
            user_id=user_id,
        )

    def get_globalhub_role_overrides(
        self, role_key: str, *, user_id: str | None
    ) -> dict[str, str]:
        """Return the user's saved overrides for one GlobalHub role.

        :param role_key: The role's API key, e.g. ``"manager:default"``.
        :param user_id: The user, or ``None`` in single-user mode.
        :returns: Saved values by field name, e.g. ``{"manual": "..."}``; empty
            when the user saved none or the row is unreadable.
        """
        value = self._read(
            "read_globalhub_role_overrides",
            GLOBALHUB_ROLE_PREFERENCE_PREFIX + role_key,
            user_id=user_id,
            max_decoded_bytes=_MAX_ROLE_OVERRIDE_BYTES,
        )
        if value is None:
            return {}
        try:
            stored = json.loads(value)
        except ValueError:
            stored = None
        if not isinstance(stored, dict) or not all(
            isinstance(field, str) and isinstance(text, str) for field, text in stored.items()
        ):
            _logger.warning("stored overrides for GlobalHub role %r are malformed", role_key)
            return {}
        return stored

    def save_globalhub_role_overrides(
        self, role_key: str, overrides: Mapping[str, str], *, user_id: str | None
    ) -> None:
        """Replace the user's overrides for one GlobalHub role; empty clears them.

        :param role_key: The role's API key, e.g. ``"manager:default"``.
        :param overrides: Values by field name, e.g. ``{"model": "..."}``.
        :param user_id: The user, or ``None`` in single-user mode.
        """
        self._write(
            "save_globalhub_role_overrides",
            GLOBALHUB_ROLE_PREFERENCE_PREFIX + role_key,
            json.dumps(dict(overrides)) if overrides else None,
            user_id=user_id,
        )

    def get_globalhub_role_session(self, role_key: str, *, user_id: str | None) -> str | None:
        """Return the user's session id for a singleton GlobalHub role, or ``None``.

        Singleton roles only (the broker and custodian), which have one session
        per user. Managers have a session each and are not tracked here.

        :param role_key: The singleton role's API key, e.g. ``"broker"``.
        :param user_id: The user, or ``None`` in single-user mode.
        """
        return self._read(
            "read_globalhub_role_session",
            GLOBALHUB_ROLE_SESSION_PREFERENCE_PREFIX + role_key,
            user_id=user_id,
            max_decoded_bytes=_MAX_SESSION_ID_BYTES,
        )

    def save_globalhub_role_session(
        self, role_key: str, session_id: str, *, user_id: str | None
    ) -> None:
        """Record the user's session for a singleton GlobalHub role.

        Singleton roles only (the broker and custodian): saving again replaces
        the recorded session, so a role with several sessions cannot use this.

        :param role_key: The singleton role's API key, e.g. ``"broker"``.
        :param session_id: The role session, e.g. ``"conv_abc123"``.
        :param user_id: The user, or ``None`` in single-user mode.
        """
        self._write(
            "save_globalhub_role_session",
            GLOBALHUB_ROLE_SESSION_PREFERENCE_PREFIX + role_key,
            session_id,
            user_id=user_id,
        )

    def get_globalhub_report_new_sessions(self, *, user_id: str | None) -> bool:
        """Whether the user's sessions on no task report their finished turns to the broker.

        The reports are on until the user turns them off.
        """
        value = self._read(
            "read_globalhub_new_session_reports",
            GLOBALHUB_NEW_SESSION_REPORTS_PREFERENCE_KEY,
            user_id=user_id,
            max_decoded_bytes=len(_NEW_SESSION_REPORTS_OFF),
        )
        return value != _NEW_SESSION_REPORTS_OFF

    def save_globalhub_report_new_sessions(self, enabled: bool, *, user_id: str | None) -> None:
        """Turn the user's reports of sessions on no task to the broker on or off.

        :param enabled: Whether those sessions report their finished turns.
        :param user_id: The user, or ``None`` in single-user mode.
        """
        self._write(
            "save_globalhub_new_session_reports",
            GLOBALHUB_NEW_SESSION_REPORTS_PREFERENCE_KEY,
            None if enabled else _NEW_SESSION_REPORTS_OFF,
            user_id=user_id,
        )

    def _read(
        self, operation: str, key: str, *, user_id: str | None, max_decoded_bytes: int
    ) -> str | None:
        """Return the user's stored value for ``key``, or ``None`` when unset or unreadable."""
        preference_user_id = RESERVED_USER_LOCAL if user_id is None else user_id
        with self._session(operation) as session:
            # Decode raw bytes here so a malformed value cannot fail in the ORM result processor.
            raw = session.scalar(
                select(type_coerce(SqlPreference.value, LargeBinary)).where(
                    SqlPreference.workspace_id == current_workspace_id(),
                    SqlPreference.user_id == preference_user_id,
                    SqlPreference.key == key,
                )
            )
        if raw is None:
            return None
        try:
            return decode(raw, max_decoded_bytes=max_decoded_bytes)
        except (ValueError, zstandard.ZstdError):
            _logger.warning("stored setting %r is unreadable — ignoring it", key)
            return None

    def _write(self, operation: str, key: str, value: str | None, *, user_id: str | None) -> None:
        """Store ``value`` for the user's ``key``, or delete the row when ``value`` is ``None``."""
        preference_user_id = RESERVED_USER_LOCAL if user_id is None else user_id

        def write(session: Session) -> None:
            require_active_account(session, user_id)
            if value is not None:
                upsert_preference_row(session, user_id=preference_user_id, key=key, value=value)
                return
            session.execute(
                delete(SqlPreference).where(
                    SqlPreference.workspace_id == current_workspace_id(),
                    SqlPreference.user_id == preference_user_id,
                    SqlPreference.key == key,
                )
            )

        run_write_transaction(self._session_immediate, operation, write)

    def save_session_title_instructions(
        self, instructions: str | None, *, user_id: str | None
    ) -> str | None:
        """Save or clear the user's title instructions.

        :param instructions: New instructions; ``None`` or blank clears them.
        :param user_id: The authenticated user, or ``None`` in single-user mode.
        :returns: The stored, stripped instructions, or ``None`` when cleared.
        :raises ValueError: If the instructions exceed the character limit.
        """
        value = instructions.strip() if instructions else ""
        if len(value) > SESSION_TITLE_INSTRUCTIONS_MAX_CHARS:
            limit = SESSION_TITLE_INSTRUCTIONS_MAX_CHARS
            raise ValueError(f"Session title instructions exceed {limit} characters")
        self._write(
            "save_session_title_instructions",
            SESSION_TITLE_INSTRUCTIONS_PREFERENCE_KEY,
            value or None,
            user_id=user_id,
        )
        return value or None


# custom-lint: disable-next=workspace-scoped-cache -- keyed by database URL; queries scope rows
_stores: dict[str, UserSettingsStore] = {}
_stores_lock = threading.Lock()


def open_user_settings_store(conversation_store: object) -> UserSettingsStore | None:
    """Return the settings store beside the session metadata, or ``None`` without SQL.

    :param conversation_store: The server's conversation store; settings live
        in the database at its ``storage_location``.
    """
    location = getattr(conversation_store, "storage_location", None)
    if not isinstance(location, str) or not location:
        return None
    with _stores_lock:
        store = _stores.get(location)
        if store is None:
            try:
                store = UserSettingsStore(location)
            except Exception:
                _logger.warning("User settings storage is unavailable", exc_info=True)
                return None
            _stores[location] = store
        return store


def session_owner_title_instructions(
    store: UserSettingsStore,
    conversation_store: Any,
    *,
    single_user: bool,
) -> Callable[[str], str | None]:
    """Build a resolver from a session id to its owner's title instructions.

    :param store: Where the owner's settings live.
    :param conversation_store: Answers ``get_session_owner`` for the session.
    :param single_user: Whether the server runs without authentication; then
        every session belongs to the local user.
    :returns: A callable that returns ``None`` when the owner is unknown or
        has saved no instructions.
    """

    def resolve(session_id: str) -> str | None:
        if single_user:
            return store.get_session_title_instructions(user_id=None)
        owner = conversation_store.get_session_owner(session_id, owner_only=True)
        if owner is None:
            return None
        return store.get_session_title_instructions(user_id=owner)

    return resolve
