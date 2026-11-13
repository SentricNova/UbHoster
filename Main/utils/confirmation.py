"""Confirmation manager for destructive actions (.dmclean, etc.) with automatic TTL expiry."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

log = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingAction:
    client_id: str
    user_id: int
    chat_id: int
    action_type: str
    description: str
    handler: Callable[[], Coroutine[Any, Any, None]]
    expires_at: float


class ConfirmationManager:
    """Manages pending destructive operations per (client_id, user_id) with automatic TTL expiry."""

    def __init__(self, default_ttl: float = 60.0) -> None:
        self._default_ttl = default_ttl
        self._pending: dict[tuple[str, int], PendingAction] = {}

    def set_pending(
        self,
        client_id: str,
        user_id: int,
        chat_id: int,
        action_type: str,
        description: str,
        handler: Callable[[], Coroutine[Any, Any, None]],
        ttl: float | None = None,
    ) -> PendingAction:
        key = (client_id, user_id)
        action = PendingAction(
            client_id=client_id,
            user_id=user_id,
            chat_id=chat_id,
            action_type=action_type,
            description=description,
            handler=handler,
            expires_at=time.time() + (ttl or self._default_ttl),
        )
        self._pending[key] = action
        return action

    def get_pending(self, client_id: str, user_id: int) -> PendingAction | None:
        key = (client_id, user_id)
        action = self._pending.get(key)
        if action is None:
            return None
        if time.time() > action.expires_at:
            self._pending.pop(key, None)
            return None
        return action

    def cancel(self, client_id: str, user_id: int) -> PendingAction | None:
        key = (client_id, user_id)
        action = self.get_pending(client_id, user_id)
        self._pending.pop(key, None)
        return action

    async def execute_confirmed(self, client_id: str, user_id: int) -> tuple[bool, str]:
        key = (client_id, user_id)
        action = self.get_pending(client_id, user_id)
        if action is None:
            return False, "No active operation is pending confirmation, or the confirmation window expired."
        self._pending.pop(key, None)
        try:
            await action.handler()
            return True, f"Operation '{action.action_type}' confirmed and executed."
        except Exception as exc:
            log.exception("Error executing confirmed action %s: %s", action.action_type, exc)
            return False, f"Failed executing '{action.action_type}': {exc}"


confirmation_manager = ConfirmationManager(default_ttl=60.0)
