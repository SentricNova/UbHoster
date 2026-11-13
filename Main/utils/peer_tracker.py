"""Buffered audience tracker (users/groups/channels) & rate-limited broadcast engine."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from pyrogram import Client, raw, types
from pyrogram.errors import (
    ChannelPrivate,
    ChatWriteForbidden,
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    UserIsBlocked,
)

from Main.Client.Mongo import bot_db

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BroadcastResult:
    total: int
    sent: int
    failed: int
    pruned: int
    elapsed_seconds: float


class PeerTracker:
    """Tracks unique users/groups/channels per hosted client without DB spam."""

    def __init__(self, max_seen_cache: int = 100_000) -> None:
        self._max_seen = max_seen_cache
        self._seen: OrderedDict[tuple[str, int], int] = OrderedDict()
        self._pending: dict[tuple[str, int], dict[str, Any]] = {}
        self._flush_lock = asyncio.Lock()

    def record(
        self,
        *,
        client_id: str,
        chat_id: int,
        peer_type: str,
        title: str = "",
        username: str = "",
    ) -> None:
        if peer_type not in ("user", "group", "channel"):
            return
        now = int(time.time())
        key = (client_id, int(chat_id))

        last_ts = self._seen.get(key)
        # Refresh metadata at most once per hour per chat to keep writes tiny
        if last_ts is not None and (now - last_ts) < 3600:
            self._seen.move_to_end(key)
            return

        self._seen[key] = now
        self._seen.move_to_end(key)
        while len(self._seen) > self._max_seen:
            self._seen.popitem(last=False)

        self._pending[key] = {
            "client_id": client_id,
            "chat_id": int(chat_id),
            "peer_type": peer_type,
            "title": (title or "")[:120],
            "username": (username or "")[:64],
            "last_seen": now,
        }

    def forget_client(self, client_id: str) -> None:
        for k in [k for k in self._pending if k[0] == client_id]:
            self._pending.pop(k, None)
        for k in [k for k in self._seen if k[0] == client_id]:
            self._seen.pop(k, None)

    async def flush(self) -> int:
        async with self._flush_lock:
            if not self._pending:
                return 0
            batch = list(self._pending.values())
            self._pending.clear()
            return await bot_db.bulk_upsert_client_peers(batch)

    async def get_counts(self, client_id: str) -> dict[str, int]:
        """Return total tracked users/groups/channels for a client from MongoDB."""
        return await bot_db.get_client_peer_counts(client_id)


peer_tracker = PeerTracker()


async def broadcast_to_client_audience(
    *,
    client_id: str,
    client_doc: dict[str, Any],
    peer_type: str | None = None,  # None = all, or "user" / "group" / "channel"
    text: str | None = None,
    source_bot: Client | None = None,
    source_message: types.Message | None = None,
    mtproto_client: Client | None = None,
) -> BroadcastResult:
    """Broadcast a text or message to tracked users/groups/channels of a hosted client."""
    t0 = time.perf_counter()
    # Flush any recently discovered peers before querying recipients
    await peer_tracker.flush()

    chat_ids = await bot_db.get_all_broadcast_chat_ids(client_id, peer_type=peer_type)
    total = len(chat_ids)
    sent = 0
    failed = 0
    pruned_ids: list[int] = []

    if total == 0:
        return BroadcastResult(0, 0, 0, 0, 0.0)

    is_bot = client_doc.get("client_type") == "bot"
    bot_token = client_doc.get("bot_token", "")

    # 1. Preferred fast path for bots with Bot API token (supports webhook bots + text/media!)
    if is_bot and bot_token and (text is not None or (source_message and source_message.text)):
        from Main.utils.bot_webhook import bot_webhook_manager

        msg_text = text if text is not None else (source_message.text or "")
        await bot_webhook_manager.start_session()
        assert bot_webhook_manager._session is not None
        session = bot_webhook_manager._session
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        for cid in chat_ids:
            try:
                async with session.post(
                    url,
                    json={
                        "chat_id": cid,
                        "text": msg_text,
                        "link_preview_options": {"is_disabled": True},
                    },
                ) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        sent += 1
                    else:
                        code = data.get("error_code")
                        desc = str(data.get("description", "")).lower()
                        if code == 429:
                            retry_after = int(
                                (data.get("parameters") or {}).get("retry_after", 3)
                            )
                            await asyncio.sleep(retry_after + 0.5)
                            async with session.post(
                                url,
                                json={
                                    "chat_id": cid,
                                    "text": msg_text,
                                    "link_preview_options": {"is_disabled": True},
                                },
                            ) as retry_resp:
                                retry_data = await retry_resp.json()
                                if retry_data.get("ok"):
                                    sent += 1
                                else:
                                    failed += 1
                        elif code in (400, 403) and any(
                            k in desc
                            for k in ("blocked", "deactivated", "not found", "kicked", "no rights")
                        ):
                            failed += 1
                            pruned_ids.append(cid)
                        else:
                            failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.04)  # ~25 msg/s safe Telegram Bot API pacing

    # 2. MTProto Client path (userbots, MTProto bots, or copying rich media messages)
    elif mtproto_client is not None:
        delay = 0.05 if is_bot else 0.35  # Conservative anti-flood pacing for userbots
        for cid in chat_ids:
            try:
                if source_message is not None and text is None:
                    # Copy message if same client, else send extracted text/caption
                    if source_bot is mtproto_client:
                        await source_message.copy(cid)
                    else:
                        fallback_text = source_message.text or source_message.caption or ""
                        if not fallback_text:
                            failed += 1
                            continue
                        peer = await mtproto_client.resolve_peer(cid)
                        await mtproto_client.invoke(
                            raw.functions.messages.SendMessage(
                                peer=peer,
                                message=fallback_text,
                                random_id=random.randint(1, 2**62),
                                no_webpage=True,
                            )
                        )
                else:
                    peer = await mtproto_client.resolve_peer(cid)
                    await mtproto_client.invoke(
                        raw.functions.messages.SendMessage(
                            peer=peer,
                            message=text or "",
                            random_id=random.randint(1, 2**62),
                            no_webpage=True,
                        )
                    )
                sent += 1
            except FloodWait as fw:
                await asyncio.sleep(fw.value + 1)
                try:
                    peer = await mtproto_client.resolve_peer(cid)
                    await mtproto_client.invoke(
                        raw.functions.messages.SendMessage(
                            peer=peer,
                            message=text or (source_message.text if source_message else "") or "",
                            random_id=random.randint(1, 2**62),
                            no_webpage=True,
                        )
                    )
                    sent += 1
                except Exception:
                    failed += 1
            except (UserIsBlocked, InputUserDeactivated, PeerIdInvalid, ChannelPrivate, ChatWriteForbidden):
                failed += 1
                pruned_ids.append(cid)
            except Exception:
                failed += 1
            await asyncio.sleep(delay)

    pruned = 0
    if pruned_ids:
        pruned = await bot_db.delete_client_peers_bulk(client_id, pruned_ids)

    return BroadcastResult(
        total=total,
        sent=sent,
        failed=failed,
        pruned=pruned,
        elapsed_seconds=round(time.perf_counter() - t0, 2),
    )
