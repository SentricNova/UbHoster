"""Centralized, transparent user activity monitoring manager (.spy / .unspy)."""

from __future__ import annotations

import asyncio
import datetime
import html
import logging
import time
from typing import Any

from config import Config
from Main.Client.Mongo import bot_db
from Main.utils.error_handler import get_main_bot, send_to_logger_group

log = logging.getLogger(__name__)

# target_id -> set of subscriber user_ids
_TARGET_SUBSCRIBERS: dict[int, set[int]] = {}
# subscriber_id -> enabled bool
_SUBSCRIBER_ENABLED: dict[int, bool] = {}
# (target_id, chat_id) -> last notification timestamp
_NOTIFICATION_COOLDOWN: dict[tuple[int, int], float] = {}
# Cooldown duration: 24 hours (1 notification per target per chat per day)
COOLDOWN_SECONDS: float = 86400.0


async def load_spy_cache() -> None:
    """Initialize in-memory spy subscriptions from MongoDB on startup."""
    global _TARGET_SUBSCRIBERS, _SUBSCRIBER_ENABLED
    try:
        col = bot_db.engine.collection("hosting_spy_targets")
        cursor = col.find({})
        async for doc in cursor:
            tid = int(doc.get("target_id", 0))
            subs = set(int(s) for s in (doc.get("subscribers") or []))
            if tid and subs:
                _TARGET_SUBSCRIBERS[tid] = subs

        set_col = bot_db.engine.collection("hosting_spy_settings")
        set_cursor = set_col.find({})
        async for doc in set_cursor:
            uid = int(doc.get("user_id", 0))
            if uid:
                _SUBSCRIBER_ENABLED[uid] = bool(doc.get("enabled", True))
        log.info(
            "Spy manager initialized: %d monitored target(s), %d subscriber setting(s)",
            len(_TARGET_SUBSCRIBERS),
            len(_SUBSCRIBER_ENABLED),
        )
    except Exception as exc:
        log.warning("Could not load spy targets from database: %s", exc)


def is_target_monitored(target_id: int) -> bool:
    """Quick O(1) check if a target is currently registered for monitoring."""
    subs = _TARGET_SUBSCRIBERS.get(target_id)
    return bool(subs)


async def add_spy_target(subscriber_id: int, target_id: int) -> bool:
    """Add a target user ID to a subscriber's monitoring list."""
    if target_id not in _TARGET_SUBSCRIBERS:
        _TARGET_SUBSCRIBERS[target_id] = set()
    _TARGET_SUBSCRIBERS[target_id].add(subscriber_id)
    _SUBSCRIBER_ENABLED[subscriber_id] = True

    try:
        col = bot_db.engine.collection("hosting_spy_targets")
        await col.update_one(
            {"target_id": target_id},
            {
                "$addToSet": {"subscribers": subscriber_id},
                "$set": {"updated_at": int(time.time())},
            },
            upsert=True,
        )
        return True
    except Exception as exc:
        log.error("Failed adding spy target in DB: %s", exc)
        return False


async def remove_spy_target(subscriber_id: int, target_id: int) -> bool:
    """Remove a target user ID from a subscriber's monitoring list."""
    if target_id in _TARGET_SUBSCRIBERS:
        _TARGET_SUBSCRIBERS[target_id].discard(subscriber_id)
        if not _TARGET_SUBSCRIBERS[target_id]:
            _TARGET_SUBSCRIBERS.pop(target_id, None)

    try:
        col = bot_db.engine.collection("hosting_spy_targets")
        await col.update_one(
            {"target_id": target_id},
            {
                "$pull": {"subscribers": subscriber_id},
                "$set": {"updated_at": int(time.time())},
            },
        )
        return True
    except Exception as exc:
        log.error("Failed removing spy target in DB: %s", exc)
        return False


async def set_user_spy_enabled(subscriber_id: int, enabled: bool) -> None:
    """Enable or disable spy notifications for a subscriber."""
    _SUBSCRIBER_ENABLED[subscriber_id] = enabled
    try:
        col = bot_db.engine.collection("hosting_spy_settings")
        await col.update_one(
            {"user_id": subscriber_id},
            {"$set": {"enabled": enabled, "updated_at": int(time.time())}},
            upsert=True,
        )
    except Exception as exc:
        log.error("Failed updating spy settings in DB: %s", exc)


def is_user_spy_enabled(subscriber_id: int) -> bool:
    return _SUBSCRIBER_ENABLED.get(subscriber_id, True)


async def get_user_spy_targets(subscriber_id: int) -> list[int]:
    """Return all active targets monitored by a given subscriber."""
    out: list[int] = []
    for tid, subs in _TARGET_SUBSCRIBERS.items():
        if subscriber_id in subs:
            out.append(tid)
    return out


async def on_user_activity(
    target_id: int,
    chat_id: int,
    chat_title: str,
    chat_username: str | None,
    msg_id: int,
    sender_name: str,
    sender_username: str | None,
    *,
    client: Any = None,
) -> None:
    """Handle sighting of a monitored target, respecting 24h per-chat cooldown."""
    subscribers = _TARGET_SUBSCRIBERS.get(target_id)
    if not subscribers:
        return

    now = time.time()
    cooldown_key = (target_id, chat_id)
    last_notified = _NOTIFICATION_COOLDOWN.get(cooldown_key, 0.0)
    if now - last_notified < COOLDOWN_SECONDS:
        # Cooldown active (1 notification per target per chat per day)
        return

    _NOTIFICATION_COOLDOWN[cooldown_key] = now

    uname_str = f"@{sender_username}" if sender_username else "No username"
    safe_name = html.escape(sender_name or "User")
    safe_title = html.escape(chat_title or str(chat_id))
    clean_cid_str = str(chat_id).replace("-100", "")

    if chat_username:
        chat_link = f"https://t.me/{chat_username}"
        msg_link = f"https://t.me/{chat_username}/{msg_id}"
    else:
        chat_link = f"Chat ID: <code>{chat_id}</code>"
        msg_link = f"https://t.me/c/{clean_cid_str}/{msg_id}"

    time_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    report_html = (
        "👁️ <b>SPY MONITOR ALERT: Target Spotted!</b>\n\n"
        "<blockquote>"
        f"• <b>Target:</b> {safe_name} ({uname_str})\n"
        f"• <b>Target ID:</b> <code>{target_id}</code>\n"
        f"• <b>Chat:</b> {safe_title} [<code>{chat_id}</code>]\n"
        f"• <b>Chat Link:</b> {chat_link}\n"
        f"• <b>Message Link:</b> {msg_link}\n"
        f"• <b>Detected At:</b> <code>{time_str}</code>"
        "</blockquote>\n\n"
        "<i>Cooldown: 1 notification per target per chat per 24 hours.</i>"
    )

    from Main.utils.client_manager import client_manager

    # 1. Notify each authorized subscriber
    for sub_id in list(subscribers):
        if not is_user_spy_enabled(sub_id):
            continue

        delivered = False
        # Try delivering to subscriber's active userbot Saved Messages / Logger Group
        for rt in client_manager.get_user_runtimes(sub_id):
            if rt.doc.get("client_type") == "userbot":
                try:
                    await rt.client.send_message("me", report_html, parse_mode=enums_parse_mode_html())
                    lg_id = rt.doc.get("logger_group_id")
                    if lg_id:
                        try:
                            await rt.client.send_message(lg_id, report_html, parse_mode=enums_parse_mode_html())
                        except Exception:
                            pass
                    delivered = True
                    break
                except Exception as exc:
                    log.debug("Failed sending spy report to userbot %s: %s", rt.client_id, exc)

        # Also deliver via Main Bot direct message
        main_bot = get_main_bot()
        if main_bot is not None:
            try:
                await main_bot.send_message(sub_id, report_html, parse_mode=enums_parse_mode_html())
                delivered = True
            except Exception as exc:
                log.debug("Failed sending spy report via main bot to %d: %s", sub_id, exc)

    # 2. Audit log to Main Bot Owner Logger Group
    audit_html = (
        f"🛡️ <b>[AUDIT LOG] Target Activity Sighted</b>\n"
        f"• Target: {safe_name} (<code>{target_id}</code>)\n"
        f"• Chat: {safe_title} (<code>{chat_id}</code>)\n"
        f"• Subscribed Admins: <code>{list(subscribers)}</code>\n"
        f"• Time: <code>{time_str}</code>"
    )
    await send_to_logger_group(audit_html, parse_mode=enums_parse_mode_html())


def enums_parse_mode_html():
    from pyrogram import enums
    return enums.ParseMode.HTML
