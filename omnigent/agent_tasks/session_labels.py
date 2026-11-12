"""Conversation labels that mark GlobalHub role sessions."""

from __future__ import annotations

# Server-owned: clients cannot set it, because it selects the role manual.
ROLE_LABEL = "omnigent.role"
BROKER_ROLE_VALUE = "task_broker"
MANAGER_ROLE_VALUE = "task_manager"
CUSTODIAN_ROLE_VALUE = "task_custodian"
