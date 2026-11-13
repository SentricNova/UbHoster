"""Lightweight RawUpdateHandler & Webhook dispatcher for hosted bots & userbots.

- Tracks unique users, groups, and channels into peer_tracker.
- Auto-downloads incoming timer-based (ttl_seconds) media to Saved Messages for userbots.
- Supports sendWidget (Bot API 10.4) for hosted bots via pooled aiohttp.
"""

from __future__ import annotations

import asyncio
import html
import importlib
import json
import logging
import pkgutil
import random
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp
from aiohttp import ClientSession
from pyrogram import Client, raw, types
from pyrogram.handlers import RawUpdateHandler

from config import Config
from Main.Client.Mongo import bot_db
from Main.utils.error_handler import run_safe
from Main.utils.peer_tracker import peer_tracker

log = logging.getLogger(__name__)

HandlerFn = Callable[[Any], Awaitable[None]]

BOT_COMMANDS: dict[str, HandlerFn] = {}
USERBOT_COMMANDS: dict[str, HandlerFn] = {}
USERBOT_SLASH_ALIASES: frozenset[str] = frozenset(
    {"steal", "export", "info", "scrape", "confirm", "cancel", "spy", "unspy"}
)

_PLUGINS_LOADED = False
_BG_TASKS: set[asyncio.Task[Any]] = set()


def save_raw_info_copy(client_id: str, target_id: int, json_text: str) -> Path:
    """Persist a copy of /info raw Telegram JSON into the project directory."""
    Config.RAW_INFO_DIR.mkdir(parents=True, exist_ok=True)
    safe_cid = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in client_id)
    out_path = Config.RAW_INFO_DIR / f"{safe_cid}_msg_{target_id}.json"
    out_path.write_text(json_text, encoding="utf-8")
    return out_path


def bot_command(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a slash command (e.g. /start) for hosted bots."""
    cmd = name.lower().lstrip("/")

    def decorator(func: HandlerFn) -> HandlerFn:
        BOT_COMMANDS[cmd] = func
        return func

    return decorator


def userbot_command(name: str) -> Callable[[HandlerFn], HandlerFn]:
    """Register a dot command (e.g. .ping, .steal) for hosted userbots."""
    cmd = name.lower().lstrip(".")

    def decorator(func: HandlerFn) -> HandlerFn:
        USERBOT_COMMANDS[cmd] = func
        return func

    return decorator


@dataclass(slots=True)
class RawContext:
    """Minimal raw MTProto update context without high-level Pyrogram model overhead."""

    client: Client
    client_id: str
    client_type: str
    owner_id: int
    sender_id: int
    chat_id: int
    chat_type: str
    msg_id: int
    reply_to_msg_id: int | None
    text: str
    command: str
    args: str
    peer: raw.base.InputPeer
    is_outgoing: bool
    stats: dict[str, Any]
    sender_name: str = "User"
    sender_username: str = ""
    raw_update: Any = None

    async def ping_ms(self) -> float:
        t0 = time.perf_counter()
        await self.client.invoke(raw.functions.Ping(ping_id=random.randint(1, 2**62)))
        return (time.perf_counter() - t0) * 1000.0

    async def reply_raw(
        self, text: str, reply: bool = True, parse_mode: str | None = None, **kwargs
    ) -> Any:
        if parse_mode:
            from pyrogram import enums
            pm = enums.ParseMode.HTML if str(parse_mode).lower() == "html" else enums.ParseMode.MARKDOWN
            return await self.client.send_message(
                self.chat_id,
                text,
                parse_mode=pm,
                reply_parameters=(
                    types.ReplyParameters(message_id=self.msg_id) if reply else None
                ),
                **kwargs,
            )
        reply_to = (
            raw.types.InputReplyToMessage(reply_to_msg_id=self.msg_id)
            if reply
            else None
        )
        return await self.client.invoke(
            raw.functions.messages.SendMessage(
                peer=self.peer,
                message=text,
                random_id=random.randint(1, 2**62),
                reply_to=reply_to,
                no_webpage=True,
            )
        )

    async def edit_raw(self, text: str, parse_mode: str | None = None, **kwargs) -> Any:
        if parse_mode:
            try:
                from pyrogram import enums
                pm = enums.ParseMode.HTML if str(parse_mode).lower() == "html" else enums.ParseMode.MARKDOWN
                return await self.client.edit_message_text(
                    chat_id=self.chat_id,
                    message_id=self.msg_id,
                    text=text,
                    parse_mode=pm,
                    **kwargs,
                )
            except Exception:
                pass
        return await self.client.invoke(
            raw.functions.messages.EditMessage(
                peer=self.peer,
                id=self.msg_id,
                message=text,
                no_webpage=True,
            )
        )

    async def send_widget_raw(self, html: str, reply: bool = True) -> dict[str, Any]:
        from Main.utils.bot_webhook import bot_webhook_manager

        doc = await bot_db.get_client(self.client_id)
        bot_token = doc.get("bot_token", "") if doc else ""
        if not bot_token:
            return {"ok": False, "description": "Bot token not available for sendWidget."}

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "html": html,
        }
        if reply and self.msg_id:
            payload["reply_parameters"] = {
                "message_id": self.msg_id,
                "allow_sending_without_reply": True,
            }
        _, data = await bot_webhook_manager._tg_api(bot_token, "sendWidget", payload)
        return data

    async def reply_raw_json(
        self, json_text: str, filename: str = "raw_info.json", *, edit_for_userbot: bool = False
    ) -> Any:
        target_reply_id = self.reply_to_msg_id or self.msg_id
        saved_path = await asyncio.to_thread(
            save_raw_info_copy, self.client_id, target_reply_id, json_text
        )
        log.info("Saved raw JSON copy to %s", saved_path)

        if len(json_text) <= 3900:
            entities = [
                raw.types.MessageEntityPre(
                    offset=0,
                    length=len(json_text.encode("utf-16-le")) // 2,
                    language="json",
                )
            ]
            if edit_for_userbot and self.is_outgoing:
                return await self.client.invoke(
                    raw.functions.messages.EditMessage(
                        peer=self.peer,
                        id=self.msg_id,
                        message=json_text,
                        entities=entities,
                        no_webpage=True,
                    )
                )
            return await self.client.invoke(
                raw.functions.messages.SendMessage(
                    peer=self.peer,
                    message=json_text,
                    entities=entities,
                    random_id=random.randint(1, 2**62),
                    reply_to=raw.types.InputReplyToMessage(reply_to_msg_id=target_reply_id),
                    no_webpage=True,
                )
            )

        bio = BytesIO(json_text.encode("utf-8"))
        bio.name = filename
        if edit_for_userbot and self.is_outgoing:
            try:
                await self.client.delete_messages(self.chat_id, self.msg_id)
            except Exception:
                pass
        return await self.client.send_document(
            chat_id=self.chat_id,
            document=bio,
            caption=f"Raw Telegram JSON ({len(json_text)} chars)\nSaved: {saved_path.name}",
            reply_parameters=types.ReplyParameters(message_id=target_reply_id),
        )

    async def fetch_raw_json(self) -> str:
        """Fetch the whole raw MTProto JSON for the replied message (or current update)."""
        if self.reply_to_msg_id:
            msg_ids = [raw.types.InputMessageID(id=self.reply_to_msg_id)]
            if isinstance(self.peer, raw.types.InputPeerChannel):
                res = await self.client.invoke(
                    raw.functions.channels.GetMessages(
                        channel=raw.types.InputChannel(
                            channel_id=self.peer.channel_id,
                            access_hash=self.peer.access_hash,
                        ),
                        id=msg_ids,
                    )
                )
            else:
                res = await self.client.invoke(
                    raw.functions.messages.GetMessages(id=msg_ids)
                )
            return str(res)
        return str(self.raw_update) if self.raw_update is not None else "{}"


@dataclass(slots=True)
class BotWebhookContext:
    """Zero-socket Bot API context for 10,000+ webhook-hosted bots."""

    http_session: ClientSession
    bot_token: str
    client_id: str
    owner_id: int
    sender_id: int
    chat_id: int
    chat_type: str
    msg_id: int
    reply_to_msg_id: int | None
    text: str
    command: str
    args: str
    stats: dict[str, Any]
    sender_name: str = "User"
    sender_username: str = ""
    raw_update: dict[str, Any] | None = None

    async def ping_ms(self) -> float:
        t0 = time.perf_counter()
        async with self.http_session.get(
            f"https://api.telegram.org/bot{self.bot_token}/getMe"
        ) as resp:
            await resp.read()
        return (time.perf_counter() - t0) * 1000.0

    async def reply_raw(
        self, text: str, reply: bool = True, parse_mode: str | None = None
    ) -> Any:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply and self.msg_id:
            payload["reply_parameters"] = {
                "message_id": self.msg_id,
                "allow_sending_without_reply": True,
            }
        async with self.http_session.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json=payload,
        ) as resp:
            return await resp.json()

    async def send_widget_raw(self, html: str, reply: bool = True) -> dict[str, Any]:
        from Main.utils.bot_webhook import bot_webhook_manager

        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "html": html,
        }
        if reply and self.msg_id:
            payload["reply_parameters"] = {
                "message_id": self.msg_id,
                "allow_sending_without_reply": True,
            }
        _, data = await bot_webhook_manager._tg_api(self.bot_token, "sendWidget", payload)
        return data

    async def reply_raw_json(
        self, json_text: str, filename: str = "raw_info.json", *, edit_for_userbot: bool = False
    ) -> Any:
        target_reply_id = self.reply_to_msg_id or self.msg_id
        saved_path = await asyncio.to_thread(
            save_raw_info_copy, self.client_id, target_reply_id, json_text
        )
        log.info("Saved raw JSON copy to %s", saved_path)

        if len(json_text) <= 3800:
            payload: dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": f'<pre><code class="language-json">{html.escape(json_text)}</code></pre>',
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            }
            if target_reply_id:
                payload["reply_parameters"] = {
                    "message_id": target_reply_id,
                    "allow_sending_without_reply": True,
                }
            async with self.http_session.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=payload,
            ) as resp:
                return await resp.json()

        form = aiohttp.FormData()
        form.add_field("chat_id", str(self.chat_id))
        form.add_field(
            "caption", f"Raw Telegram JSON ({len(json_text)} chars)\nSaved: {saved_path.name}"
        )
        if target_reply_id:
            form.add_field(
                "reply_parameters",
                f'{{"message_id":{target_reply_id},"allow_sending_without_reply":true}}',
            )
        form.add_field(
            "document",
            json_text.encode("utf-8"),
            filename=filename,
            content_type="application/json",
        )
        async with self.http_session.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendDocument",
            data=form,
        ) as resp:
            return await resp.json()

    async def fetch_raw_json(self) -> str:
        """Return the raw Bot API JSON for the replied message (or full update)."""
        if isinstance(self.raw_update, dict):
            msg = self.raw_update.get("message")
            if isinstance(msg, dict) and isinstance(msg.get("reply_to_message"), dict):
                return json.dumps(msg["reply_to_message"], indent=2, ensure_ascii=False)
            return json.dumps(self.raw_update, indent=2, ensure_ascii=False)
        return "{}"


async def _resolve_input_peer(
    client: Client,
    peer_id: raw.base.Peer | int,
    users: dict[int, raw.base.User],
    chats: dict[int, raw.base.Chat],
) -> raw.base.InputPeer | None:
    if isinstance(peer_id, raw.types.PeerUser):
        u = users.get(peer_id.user_id)
        if u is not None and hasattr(u, "access_hash"):
            return raw.types.InputPeerUser(user_id=u.id, access_hash=u.access_hash)
        return await client.resolve_peer(peer_id.user_id)

    if isinstance(peer_id, raw.types.PeerChat):
        return raw.types.InputPeerChat(chat_id=peer_id.chat_id)

    if isinstance(peer_id, raw.types.PeerChannel):
        c = chats.get(peer_id.channel_id)
        if c is not None and hasattr(c, "access_hash"):
            return raw.types.InputPeerChannel(channel_id=c.id, access_hash=c.access_hash)
        return await client.resolve_peer(int(f"-100{peer_id.channel_id}"))

    if isinstance(peer_id, int):
        u = users.get(peer_id)
        if u is not None and hasattr(u, "access_hash"):
            return raw.types.InputPeerUser(user_id=u.id, access_hash=u.access_hash)
        return await client.resolve_peer(peer_id)

    return None


def _compute_chat_id_and_type(
    raw_peer: raw.base.Peer | int | None,
    is_chat_id: bool,
    chats: dict[int, raw.base.Chat],
) -> tuple[int, str]:
    if is_chat_id and isinstance(raw_peer, int):
        return -raw_peer, "group"
    if isinstance(raw_peer, int):
        return raw_peer, "private"
    if isinstance(raw_peer, raw.types.PeerUser):
        return raw_peer.user_id, "private"
    if isinstance(raw_peer, raw.types.PeerChat):
        return -raw_peer.chat_id, "group"
    if isinstance(raw_peer, raw.types.PeerChannel):
        c = chats.get(raw_peer.channel_id)
        is_broadcast = bool(getattr(c, "broadcast", False)) if c else True
        return int(f"-100{raw_peer.channel_id}"), ("channel" if is_broadcast else "group")
    return 0, "private"


def _track_mtproto_peer(
    client_id: str,
    raw_peer: raw.base.Peer | int | None,
    is_chat_id: bool,
    users: dict[int, raw.base.User],
    chats: dict[int, raw.base.Chat],
) -> None:
    if raw_peer is None:
        return
    if is_chat_id and isinstance(raw_peer, int):
        c = chats.get(raw_peer)
        peer_tracker.record(
            client_id=client_id,
            chat_id=-raw_peer,
            peer_type="group",
            title=getattr(c, "title", "") if c else "",
        )
        return

    if isinstance(raw_peer, int):
        u = users.get(raw_peer)
        if u and getattr(u, "bot", False):
            return
        name = " ".join(
            filter(None, [getattr(u, "first_name", ""), getattr(u, "last_name", "")])
        ) if u else ""
        peer_tracker.record(
            client_id=client_id,
            chat_id=raw_peer,
            peer_type="user",
            title=name,
            username=(getattr(u, "username", "") or "") if u else "",
        )
        return

    if isinstance(raw_peer, raw.types.PeerUser):
        u = users.get(raw_peer.user_id)
        if u and getattr(u, "bot", False):
            return
        name = " ".join(
            filter(None, [getattr(u, "first_name", ""), getattr(u, "last_name", "")])
        ) if u else ""
        peer_tracker.record(
            client_id=client_id,
            chat_id=raw_peer.user_id,
            peer_type="user",
            title=name,
            username=(getattr(u, "username", "") or "") if u else "",
        )
    elif isinstance(raw_peer, raw.types.PeerChat):
        c = chats.get(raw_peer.chat_id)
        peer_tracker.record(
            client_id=client_id,
            chat_id=-raw_peer.chat_id,
            peer_type="group",
            title=getattr(c, "title", "") if c else "",
        )
    elif isinstance(raw_peer, raw.types.PeerChannel):
        c = chats.get(raw_peer.channel_id)
        is_broadcast = bool(getattr(c, "broadcast", False)) if c else True
        peer_tracker.record(
            client_id=client_id,
            chat_id=int(f"-100{raw_peer.channel_id}"),
            peer_type="channel" if is_broadcast else "group",
            title=getattr(c, "title", "") if c else "",
            username=(getattr(c, "username", "") or "") if c else "",
        )


def _extract_reply_to_id(reply_to: Any) -> int | None:
    if reply_to is None:
        return None
    return getattr(reply_to, "reply_to_msg_id", None)


def _extract_raw_message(
    update: raw.base.Update,
) -> tuple[str, bool, int, raw.base.Peer | int | None, bool, int, int | None] | None:
    """Return (text, is_outgoing, msg_id, peer_or_id, is_chat_id, sender_id, reply_to_msg_id) or None."""
    if isinstance(update, (raw.types.UpdateNewMessage, raw.types.UpdateNewChannelMessage)):
        msg = update.message
        if not isinstance(msg, raw.types.Message) or not msg.message:
            return None
        sender_id = 0
        if isinstance(msg.from_id, raw.types.PeerUser):
            sender_id = msg.from_id.user_id
        elif isinstance(msg.peer_id, raw.types.PeerUser):
            sender_id = msg.peer_id.user_id
        reply_to_id = _extract_reply_to_id(getattr(msg, "reply_to", None))
        return msg.message, bool(msg.out), msg.id, msg.peer_id, False, sender_id, reply_to_id

    if isinstance(update, raw.types.UpdateShortMessage):
        if not update.message:
            return None
        reply_to_id = _extract_reply_to_id(getattr(update, "reply_to", None))
        return (
            update.message,
            bool(update.out),
            update.id,
            update.user_id,
            False,
            update.user_id,
            reply_to_id,
        )

    if isinstance(update, raw.types.UpdateShortChatMessage):
        if not update.message:
            return None
        reply_to_id = _extract_reply_to_id(getattr(update, "reply_to", None))
        return (
            update.message,
            bool(update.out),
            update.id,
            update.chat_id,
            True,
            update.from_id,
            reply_to_id,
        )

    return None


def load_client_plugins(force: bool = False) -> None:
    """Discover and import all plugins under for_bots and for_userbots."""
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED and not force:
        return

    base_pkg = Path(__file__).resolve().parent
    for subpkg in ("for_bots", "for_userbots"):
        sub_path = base_pkg / subpkg
        if not sub_path.is_dir():
            continue
        for mod_info in pkgutil.iter_modules([str(sub_path)]):
            if mod_info.name.startswith("_"):
                continue
            mod_name = f"Main.ClientPlugin.{subpkg}.{mod_info.name}"
            importlib.import_module(mod_name)
            log.debug("Loaded client plugin: %s", mod_name)

    _PLUGINS_LOADED = True
    log.info(
        "Client plugins loaded: %d bot cmds, %d userbot cmds",
        len(BOT_COMMANDS),
        len(USERBOT_COMMANDS),
    )


def attach_client_plugins(
    client: Client,
    *,
    client_id: str,
    client_type: str,
    owner_id: int,
    stats: dict[str, Any],
    on_dirty: Callable[[str], None] | None = None,
) -> RawUpdateHandler:
    """Attach a single ultra-lightweight RawUpdateHandler to an MTProto client."""
    load_client_plugins()
    is_userbot = client_type == "userbot"
    registry = USERBOT_COMMANDS if is_userbot else BOT_COMMANDS

    async def _raw_update_router(
        cli: Client,
        update: raw.base.Update,
        users: dict[int, raw.base.User],
        chats: dict[int, raw.base.Chat],
    ) -> None:
        # Auto-capture timer-based media (ttl_seconds > 0) for userbots before text filtering
        if is_userbot and isinstance(
            update, (raw.types.UpdateNewMessage, raw.types.UpdateNewChannelMessage)
        ):
            raw_msg = update.message
            if isinstance(raw_msg, raw.types.Message) and not raw_msg.out:
                media = getattr(raw_msg, "media", None)
                ttl = getattr(media, "ttl_seconds", None)
                if isinstance(ttl, int) and ttl > 0 and stats.get("auto_steal", True):
                    stats["updates_handled"] += 1
                    if on_dirty is not None:
                        on_dirty(client_id)
                    from Main.ClientPlugin.for_userbots.steal import auto_save_timer_media

                    t = asyncio.create_task(
                        auto_save_timer_media(cli, client_id, raw_msg, users, chats, ttl)
                    )
                    _BG_TASKS.add(t)
                    t.add_done_callback(_BG_TASKS.discard)

        extracted = _extract_raw_message(update)
        if extracted is None:
            return

        stats["updates_handled"] += 1
        if on_dirty is not None:
            on_dirty(client_id)

        text, is_outgoing, msg_id, raw_peer, is_chat_id, sender_id, reply_to_id = extracted
        _track_mtproto_peer(client_id, raw_peer, is_chat_id, users, chats)
        chat_id, chat_type = _compute_chat_id_and_type(raw_peer, is_chat_id, chats)

        # Incoming hooks: Spy monitor, Raid auto-reply & Userbot DM Guard
        if not is_outgoing and sender_id:
            from Main.utils.spy_manager import is_target_monitored, on_user_activity

            if is_target_monitored(sender_id):
                u_obj = users.get(sender_id)
                s_name = (
                    " ".join(
                        filter(None, [getattr(u_obj, "first_name", ""), getattr(u_obj, "last_name", "")])
                    )
                    if u_obj
                    else "User"
                )
                s_uname = getattr(u_obj, "username", None)
                c_obj = chats.get(abs(chat_id)) or chats.get(chat_id)
                c_title = getattr(c_obj, "title", None) or getattr(c_obj, "first_name", None) or str(chat_id)
                c_uname = getattr(c_obj, "username", None)
                t_spy = asyncio.create_task(
                    on_user_activity(
                        target_id=sender_id,
                        chat_id=chat_id,
                        chat_title=c_title,
                        chat_username=c_uname,
                        msg_id=msg_id,
                        sender_name=s_name,
                        sender_username=s_uname,
                        client=cli,
                    )
                )
                _BG_TASKS.add(t_spy)
                t_spy.add_done_callback(_BG_TASKS.discard)

            from Main.utils.client_actions import FLIRT_LINES, ROAST_LINES, get_raid_mode

            raid_mode = get_raid_mode(client_id, sender_id)
            if raid_mode is not None:
                try:
                    peer = (
                        raw.types.InputPeerChat(chat_id=raw_peer)
                        if (is_chat_id and isinstance(raw_peer, int))
                        else await _resolve_input_peer(cli, raw_peer, users, chats)
                    )
                    if peer is not None:
                        line = random.choice(FLIRT_LINES if raid_mode == "fraid" else ROAST_LINES)
                        await cli.invoke(
                            raw.functions.messages.SendMessage(
                                peer=peer,
                                message=line,
                                random_id=random.randint(1, 2**62),
                                reply_to=raw.types.InputReplyToMessage(reply_to_msg_id=msg_id),
                            )
                        )
                except Exception:
                    pass

            if is_userbot and chat_type == "private":
                sender_obj = users.get(sender_id)
                if not (sender_obj and getattr(sender_obj, "bot", False)):
                    try:
                        peer = await _resolve_input_peer(cli, raw_peer, users, chats)
                        if peer is not None:
                            from Main.ClientPlugin.for_userbots.dm_manager import (
                                handle_incoming_dm_guard,
                            )

                            t_dm = asyncio.create_task(
                                handle_incoming_dm_guard(cli, client_id, sender_id, peer)
                            )
                            _BG_TASKS.add(t_dm)
                            t_dm.add_done_callback(_BG_TASKS.discard)
                    except Exception:
                        pass

        if is_userbot and not is_outgoing:
            # Only userbot account itself or main bot owner (hidden lord of access) can command userbot
            if sender_id not in Config.OWNER_IDS:
                return
        if not is_userbot and is_outgoing:
            return

        stripped = text.strip()
        if not stripped:
            return

        first = stripped[0]
        if is_userbot:
            if first not in (".", "/"):
                return
            head, _, args = stripped[1:].partition(" ")
            cmd_name = head.split("@", 1)[0].lower()
            if first == "/" and cmd_name not in USERBOT_SLASH_ALIASES:
                return
        else:
            if first != "/":
                return
            head, _, args = stripped[1:].partition(" ")
            cmd_name = head.split("@", 1)[0].lower()

        handler = registry.get(cmd_name)
        if handler is None:
            return

        if is_chat_id and isinstance(raw_peer, int):
            peer: raw.base.InputPeer | None = raw.types.InputPeerChat(chat_id=raw_peer)
        elif raw_peer is not None:
            peer = await _resolve_input_peer(cli, raw_peer, users, chats)
        else:
            peer = None

        if peer is None:
            return

        sender_u = users.get(sender_id)
        s_name = (
            " ".join(
                filter(None, [getattr(sender_u, "first_name", ""), getattr(sender_u, "last_name", "")])
            )
            if sender_u
            else "User"
        ) or "User"
        s_uname = (getattr(sender_u, "username", "") or "") if sender_u else ""

        stats["commands_handled"] += 1
        ctx = RawContext(
            client=cli,
            client_id=client_id,
            client_type=client_type,
            owner_id=owner_id,
            sender_id=sender_id,
            chat_id=chat_id,
            chat_type=chat_type,
            msg_id=msg_id,
            reply_to_msg_id=reply_to_id,
            text=stripped,
            command=cmd_name,
            args=args.strip(),
            peer=peer,
            is_outgoing=is_outgoing,
            stats=stats,
            sender_name=s_name,
            sender_username=s_uname,
            raw_update=update,
        )
        ok, _, err = await run_safe(
            handler, ctx, context_label=f"{client_id}:{cmd_name}"
        )
        if not ok and err is not None:
            try:
                await ctx.reply_raw(f"⚠️ {err.short}: {err.description}", reply=True)
            except Exception:
                pass

    handler = RawUpdateHandler(_raw_update_router)
    client.add_handler(handler)
    return handler


def _track_webhook_chat(client_id: str, chat: dict[str, Any]) -> None:
    chat_id = chat.get("id")
    if not isinstance(chat_id, int):
        return
    ctype = chat.get("type", "")
    if ctype == "private":
        peer_type = "user"
        title = " ".join(filter(None, [chat.get("first_name", ""), chat.get("last_name", "")]))
    elif ctype in ("group", "supergroup"):
        peer_type = "group"
        title = chat.get("title", "")
    elif ctype == "channel":
        peer_type = "channel"
        title = chat.get("title", "")
    else:
        return

    peer_tracker.record(
        client_id=client_id,
        chat_id=chat_id,
        peer_type=peer_type,
        title=title or "",
        username=chat.get("username", "") or "",
    )


async def dispatch_bot_webhook_update(
    *,
    http_session: ClientSession,
    bot_token: str,
    client_id: str,
    owner_id: int,
    update_dict: dict[str, Any],
    stats: dict[str, Any],
    on_dirty: Callable[[str], None] | None = None,
) -> None:
    """Route a Bot API webhook JSON update directly to Main/ClientPlugin/for_bots."""
    load_client_plugins()

    my_member = update_dict.get("my_chat_member")
    if isinstance(my_member, dict) and isinstance(my_member.get("chat"), dict):
        _track_webhook_chat(client_id, my_member["chat"])

    msg = update_dict.get("message")
    if not isinstance(msg, dict):
        return

    stats["updates_handled"] += 1
    if on_dirty is not None:
        on_dirty(client_id)

    chat = msg.get("chat") or {}
    if isinstance(chat, dict):
        _track_webhook_chat(client_id, chat)

    text = msg.get("text")
    if not isinstance(text, str):
        return

    chat_id = chat.get("id")
    chat_type = str(chat.get("type", "private"))
    msg_id = msg.get("message_id", 0)
    reply_msg = msg.get("reply_to_message")
    reply_to_id = reply_msg.get("message_id") if isinstance(reply_msg, dict) else None
    from_user = msg.get("from") or {}
    sender_id = int(from_user.get("id") or 0)
    sender_name = " ".join(
        filter(None, [from_user.get("first_name", ""), from_user.get("last_name", "")])
    ) or "User"
    sender_username = from_user.get("username", "") or ""
    if not isinstance(chat_id, int):
        return

    # Webhook Raid auto-reply hook
    if sender_id:
        from Main.utils.client_actions import FLIRT_LINES, ROAST_LINES, get_raid_mode

        raid_mode = get_raid_mode(client_id, sender_id)
        if raid_mode is not None:
            line = random.choice(FLIRT_LINES if raid_mode == "fraid" else ROAST_LINES)
            try:
                await http_session.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": line,
                        "reply_parameters": {
                            "message_id": int(msg_id),
                            "allow_sending_without_reply": True,
                        },
                    },
                )
            except Exception:
                pass

    stripped = text.strip()
    if not stripped.startswith("/"):
        return

    head, _, args = stripped[1:].partition(" ")
    cmd_name = head.split("@", 1)[0].lower()
    handler = BOT_COMMANDS.get(cmd_name)
    if handler is None:
        return

    stats["commands_handled"] += 1
    ctx = BotWebhookContext(
        http_session=http_session,
        bot_token=bot_token,
        client_id=client_id,
        owner_id=owner_id,
        sender_id=sender_id,
        chat_id=chat_id,
        chat_type=chat_type,
        msg_id=int(msg_id),
        reply_to_msg_id=int(reply_to_id) if reply_to_id else None,
        text=stripped,
        command=cmd_name,
        args=args.strip(),
        stats=stats,
        sender_name=sender_name,
        sender_username=sender_username,
        raw_update=update_dict,
    )
    ok, _, err = await run_safe(
        handler, ctx, context_label=f"wh:{client_id}:{cmd_name}"
    )
    if not ok and err is not None:
        try:
            await ctx.reply_raw(f"⚠️ {err.short}: {err.description}", reply=True)
        except Exception:
            pass
