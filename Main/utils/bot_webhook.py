"""High-scale Bot API Webhook manager for hosting 10,000+ bots with zero open sockets."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
import orjson

from config import Config
from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import dispatch_bot_webhook_update
from Main.utils.crypto import constant_time_equals, generate_webhook_secret, redact_secret

log = logging.getLogger(__name__)


@dataclass
class WebhookBotEntry:
    """Lightweight (~1 KB) state for one webhook-hosted Telegram bot."""

    client_id: str
    owner_id: int
    tg_id: int
    bot_token: str
    webhook_secret: str
    stats: dict[str, Any]


class BotWebhookManager:
    """Hosts 10,000+ bots over one pooled aiohttp client and incoming webhook router."""

    def __init__(self) -> None:
        self._bots: dict[str, WebhookBotEntry] = {}
        self._dirty_ids: set[str] = set()
        self._session: aiohttp.ClientSession | None = None
        self._dispatch_sem = asyncio.Semaphore(256)
        self._background_tasks: set[asyncio.Task[Any]] = set()

    @property
    def is_enabled(self) -> bool:
        return bool(Config.WEBHOOK_BASE_URL)

    @property
    def active_count(self) -> int:
        return len(self._bots)

    @property
    def active_ids(self) -> set[str]:
        return set(self._bots.keys())

    def get_entry(self, client_id: str) -> WebhookBotEntry | None:
        return self._bots.get(client_id)

    def _mark_dirty(self, client_id: str) -> None:
        self._dirty_ids.add(client_id)

    async def start_session(self) -> None:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=256,
                limit_per_host=128,
                ttl_dns_cache=300,
                enable_cleanup_closed=True,
                resolver=aiohttp.ThreadedResolver(),
                family=socket.AF_INET,
            )
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )

    async def close_session(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _tg_api(
        self, bot_token: str, method: str, payload: dict[str, Any] | None = None
    ) -> tuple[bool, dict[str, Any]]:
        await self.start_session()
        assert self._session is not None
        url = f"https://api.telegram.org/bot{bot_token}/{method}"
        try:
            body = orjson.dumps(payload or {})
            async with self._session.post(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
            ) as resp:
                try:
                    data = orjson.loads(await resp.read())
                except Exception:
                    return False, {
                        "ok": False,
                        "error_code": resp.status,
                        "description": f"Unexpected HTTP {resp.status} from Telegram Bot API.",
                    }
                if not isinstance(data, dict):
                    return False, {"ok": False, "description": "Invalid Bot API response."}
                return bool(data.get("ok")), data
        except asyncio.TimeoutError:
            return False, {"ok": False, "description": "Request to Telegram Bot API timed out."}
        except aiohttp.ClientError as exc:
            return False, {
                "ok": False,
                "description": f"Network error reaching Telegram Bot API: {redact_secret(str(exc))}",
            }
        except Exception as exc:
            return False, {"ok": False, "description": redact_secret(str(exc))}

    async def register_bot(
        self, doc: dict[str, Any], *, sync_webhook: bool = True
    ) -> tuple[bool, str]:
        client_id = doc["client_id"]
        bot_token = doc.get("bot_token", "")
        if not bot_token:
            return False, "Missing bot token."

        secret = doc.get("webhook_secret") or generate_webhook_secret()
        stats = {
            "started_at": time.time(),
            "updates_handled": int(doc.get("updates_handled", 0)),
            "commands_handled": int(doc.get("commands_handled", 0)),
        }

        if sync_webhook and Config.WEBHOOK_BASE_URL:
            webhook_url = f"{Config.WEBHOOK_BASE_URL}/webhook/{client_id}"
            ok, data = await self._tg_api(
                bot_token,
                "setWebhook",
                {
                    "url": webhook_url,
                    "secret_token": secret,
                    "allowed_updates": ["message", "my_chat_member"],
                    "drop_pending_updates": True,
                    "max_connections": 40,
                },
            )
            if not ok:
                err = redact_secret(str(data.get("description", "setWebhook failed")))
                await bot_db.update_client_runtime(client_id, status="error", last_error=err)
                return False, err

        self._bots[client_id] = WebhookBotEntry(
            client_id=client_id,
            owner_id=int(doc["owner_id"]),
            tg_id=int(doc["tg_id"]),
            bot_token=bot_token,
            webhook_secret=secret,
            stats=stats,
        )
        return True, "Webhook active"

    async def unregister_bot(
        self, client_id: str, *, delete_telegram_webhook: bool = False
    ) -> bool:
        entry = self._bots.pop(client_id, None)
        if entry is None:
            return False
        if delete_telegram_webhook and entry.bot_token:
            await self._tg_api(entry.bot_token, "deleteWebhook", {"drop_pending_updates": False})
        return True

    async def boot_all_bots(self) -> tuple[int, int]:
        """Load all enabled bots into RAM immediately (O(N) fast boot)."""
        if not self.is_enabled:
            return 0, 0
        await self.start_session()
        docs = await bot_db.get_all_enabled_clients(client_type="bot")
        ok_cnt = 0
        fail_cnt = 0
        for doc in docs:
            # Do not call setWebhook 10,000 times on restart; register immediately in memory.
            ok, _ = await self.register_bot(doc, sync_webhook=False)
            if ok:
                ok_cnt += 1
            else:
                fail_cnt += 1
        return ok_cnt, fail_cnt

    async def ping_bot(self, client_id: str) -> float | None:
        entry = self._bots.get(client_id)
        if entry is None:
            return None
        await self.start_session()
        assert self._session is not None
        t0 = time.perf_counter()
        try:
            async with self._session.get(
                f"https://api.telegram.org/bot{entry.bot_token}/getMe"
            ) as resp:
                await resp.read()
            return (time.perf_counter() - t0) * 1000.0
        except Exception:
            return None

    def drain_dirty_patches(self) -> list[dict[str, Any]]:
        if not self._dirty_ids:
            return []
        ids = list(self._dirty_ids)
        self._dirty_ids.clear()
        now = int(time.time())
        patches: list[dict[str, Any]] = []
        for cid in ids:
            entry = self._bots.get(cid)
            if entry is None:
                continue
            patches.append(
                {
                    "client_id": cid,
                    "updates_handled": entry.stats.get("updates_handled", 0),
                    "commands_handled": entry.stats.get("commands_handled", 0),
                    "updated_at": now,
                }
            )
        return patches

    async def handle_webhook_request(
        self,
        client_id: str,
        secret_header: str,
        payload: dict[str, Any],
    ) -> int:
        """Verify X-Telegram-Bot-Api-Secret-Token and dispatch asynchronously."""
        entry = self._bots.get(client_id)
        if entry is None:
            return 404

        if not secret_header or not constant_time_equals(secret_header, entry.webhook_secret):
            return 401

        if self._session is None or self._session.closed:
            await self.start_session()
        assert self._session is not None

        async def _run_dispatch() -> None:
            async with self._dispatch_sem:
                await dispatch_bot_webhook_update(
                    http_session=self._session,
                    bot_token=entry.bot_token,
                    client_id=client_id,
                    owner_id=entry.owner_id,
                    update_dict=payload,
                    stats=entry.stats,
                    on_dirty=self._mark_dirty,
                )

        task = asyncio.create_task(_run_dispatch())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return 200


bot_webhook_manager = BotWebhookManager()
