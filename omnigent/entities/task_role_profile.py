"""PuppyGarden role bindings and the sessions bound to singleton roles."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TaskRoleProfile:
    """Bind a PuppyGarden role key to a hidden PromptProfile manual.

    Role sessions always use OmniHarness. Runtime fields remain the resolved
    launch placement for the role session; they are not user-selectable role
    identity and carry no role prompt.
    """

    role: str
    kind: str
    created_at: int
    prompt_profile_id: str | None = None
    agent_profile_id: str | None = None
    harness: str | None = None
    model: str | None = None
    host_id: str | None = None
    workspace: str | None = None
    description: str | None = None
    updated_at: int | None = None


@dataclass
class UserRoleSession:
    """One user's live conversation with a singleton PuppyGarden role."""

    user_id: str
    role: str
    created_at: int
    conversation_id: str | None = None
    updated_at: int | None = None
