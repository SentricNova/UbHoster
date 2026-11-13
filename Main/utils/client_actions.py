"""Shared, RAM-friendly execution engines for hosted bot & userbot plugins."""

from __future__ import annotations

import asyncio
import html
import logging
import random
import re
import time
from typing import Any

from pyrogram import Client, enums, raw, types
from pyrogram.errors import FloodWait, RPCError, UserNotParticipant

from config import Config
from Main.Client.Mongo import bot_db
from Main.utils.error_handler import (
    _MAIN_BOT_REF,
    classify_error,
    report_code_error_to_logger,
    run_safe,
)

log = logging.getLogger(__name__)

ALL_FEATURES: tuple[str, ...] = (
    "spam",
    "tag",
    "moderation",
    "forward",
    "raid",
    "steal",
    "broadcast",
    "scrape",
    "dm_guard",
    "help",
    "info",
)

ROAST_LINES: tuple[str, ...] = (
    "You bring everyone so much joy when you leave the chat.",
    "I'd agree with you, but then we'd both be wrong.",
    "You're the reason the gene pool needs a lifeguard.",
    "Your secrets are always safe with me. I wasn't even listening.",
    "Some people just need a high-five. In the face. With a chair.",
    "I'm not insulting you, I'm describing you.",
    "If brains were dynamite, you wouldn't have enough to blow your nose.",
    "You have the right to remain silent because whatever you say probably makes no sense.",
    "Light travels faster than sound, which is why you seemed bright until you spoke.",
    "Even autocorrect gave up on fixing your logic.",
)

FLIRT_LINES: tuple[str, ...] = (
    "Are you a magician? Because whenever I look at you, everyone else disappears.",
    "Do you have a map? I just got lost in your eyes.",
    "Is your name Wi-Fi? Because I'm really feeling a connection.",
    "If beauty were time, you'd be an eternity.",
    "Are you made of copper and tellurium? Because you're Cu-Te.",
    "I'm no photographer, but I can definitely picture us together.",
    "Did the sun come out, or did you just smile at me?",
    "Excuse me, I think you dropped something: my jaw.",
    "You must be tired, because you've been running through my mind all day.",
    "If I had a star for every time you brightened my day, I'd hold a galaxy.",
)

# Active cancellable tasks per client: (client_id, task_kind) -> asyncio.Task
_ACTIVE_TASKS: dict[tuple[str, str], asyncio.Task[Any]] = {}
_STOP_FLAGS: dict[str, bool] = {}

# In-memory Raid target registry: client_id -> {target_user_id: "raid" | "fraid"}
_RAID_TARGETS: dict[str, dict[int, str]] = {}


def is_feature_enabled(doc: dict[str, Any] | None, feature: str) -> bool:
    if doc is None:
        return True
    features = doc.get("features") or {}
    return bool(features.get(feature, True))


def set_raid_target(client_id: str, user_id: int, mode: str) -> None:
    _RAID_TARGETS.setdefault(client_id, {})[int(user_id)] = mode


def remove_raid_target(client_id: str, user_id: int | None = None) -> int:
    targets = _RAID_TARGETS.get(client_id)
    if not targets:
        return 0
    if user_id is None:
        cnt = len(targets)
        targets.clear()
        return cnt
    return 1 if targets.pop(int(user_id), None) is not None else 0


def get_raid_mode(client_id: str, user_id: int) -> str | None:
    return _RAID_TARGETS.get(client_id, {}).get(int(user_id))


def register_client_task(client_id: str, kind: str, coro: Any) -> asyncio.Task[Any]:
    key = (client_id, kind)
    old = _ACTIVE_TASKS.pop(key, None)
    if old is not None and not old.done():
        old.cancel()
    _STOP_FLAGS[client_id] = False
    task = asyncio.create_task(coro)
    _ACTIVE_TASKS[key] = task

    def _cleanup(t: asyncio.Task[Any]) -> None:
        if _ACTIVE_TASKS.get(key) is t:
            _ACTIVE_TASKS.pop(key, None)

    task.add_done_callback(_cleanup)
    return task


def stop_client_tasks(client_id: str) -> int:
    _STOP_FLAGS[client_id] = True
    stopped = 0
    for key in [k for k in _ACTIVE_TASKS if k[0] == client_id]:
        task = _ACTIVE_TASKS.pop(key, None)
        if task is not None and not task.done():
            task.cancel()
            stopped += 1
    return stopped


def should_stop(client_id: str) -> bool:
    return _STOP_FLAGS.get(client_id, False)


async def log_to_client_logger(
    doc: dict[str, Any] | None,
    text: str,
    *,
    mtproto_client: Client | None = None,
) -> None:
    """Send info/warning/error/report to the client owner's configured logger group."""
    if not doc:
        return
    logger_id = doc.get("logger_group_id")
    if not logger_id:
        return
    try:
        if doc.get("client_type") == "bot" and doc.get("bot_token"):
            from Main.utils.bot_webhook import bot_webhook_manager

            await bot_webhook_manager._tg_api(
                doc["bot_token"],
                "sendMessage",
                {
                    "chat_id": int(logger_id),
                    "text": text[:4000],
                    "link_preview_options": {"is_disabled": True},
                },
            )
        elif mtproto_client is not None:
            await mtproto_client.send_message(int(logger_id), text[:4000])
    except Exception as exc:
        log.debug("Client logger send failed for %s: %s", doc.get("client_id"), exc)


async def check_client_access(
    ctx: Any,
    feature: str | None = None,
    *,
    owner_only: bool = True,
    allow_group_admin: bool = False,
) -> tuple[bool, dict[str, Any] | None, str]:
    """Enforce maintenance mode, feature toggle, and owner/sudo/admin permissions."""
    sender_id = int(getattr(ctx, "sender_id", 0) or 0)
    is_main_owner = sender_id in Config.OWNER_IDS

    # 1. Global maintenance check (only main bot owner unrestricted)
    if not is_main_owner:
        maint = await bot_db.get_maintenance()
        if maint.get("enabled"):
            reason = maint.get("reason") or "Bot is currently under maintenance."
            return False, None, f"🔧 {reason}"

    doc = await bot_db.get_client(ctx.client_id)
    if doc is None:
        return False, None, "Client configuration not found."

    # 2. Per-client feature toggle
    if feature and not is_feature_enabled(doc, feature):
        return False, doc, f"The `{feature}` feature is disabled by the client owner."

    # 3. Main bot owner has silent access to any connected client (never reveal ID)
    if is_main_owner:
        return True, doc, ""

    # 4. Client owner or sudo user
    sudo_users = set(doc.get("sudo_users") or [])
    if sender_id == int(ctx.owner_id) or sender_id in sudo_users or getattr(ctx, "is_outgoing", False):
        return True, doc, ""

    # 5. Group admin access for basic moderation commands
    if allow_group_admin and getattr(ctx, "chat_type", "private") != "private":
        try:
            if hasattr(ctx, "http_session") and doc.get("bot_token"):
                from Main.utils.bot_webhook import bot_webhook_manager

                ok, data = await bot_webhook_manager._tg_api(
                    doc["bot_token"],
                    "getChatMember",
                    {"chat_id": ctx.chat_id, "user_id": sender_id},
                )
                status = ((data.get("result") or {}).get("status") or "").lower()
                if ok and status in ("administrator", "creator"):
                    return True, doc, ""
            elif hasattr(ctx, "client"):
                member = await ctx.client.get_chat_member(ctx.chat_id, sender_id)
                if member.status in (
                    enums.ChatMemberStatus.ADMINISTRATOR,
                    enums.ChatMemberStatus.OWNER,
                ):
                    return True, doc, ""
        except Exception:
            pass

    if owner_only:
        return False, doc, "Only the owner or sudo users of this client can use this command."
    return True, doc, ""


async def check_force_join(
    user_id: int, *, is_main_bot: bool = False
) -> tuple[bool, list[dict[str, Any]]]:
    """Verify user membership across configured force-join chats using main bot + 24h LMDB cache."""
    if user_id in Config.OWNER_IDS:
        return True, []

    fj = await bot_db.get_force_join()
    scope = fj.get("scope", "off")
    chats = fj.get("chats") or []
    if scope == "off" or not chats:
        return True, []
    if scope == "main" and not is_main_bot:
        return True, []

    if _MAIN_BOT_REF is None:
        return True, []

    missing: list[dict[str, Any]] = []
    for ch in chats:
        cid = int(ch["chat_id"])
        if bot_db.is_force_join_cached(cid, user_id):
            continue
        try:
            member = await _MAIN_BOT_REF.get_chat_member(cid, user_id)
            if member.status in (
                enums.ChatMemberStatus.LEFT,
                enums.ChatMemberStatus.BANNED,
            ):
                missing.append(ch)
            else:
                bot_db.cache_force_join_member(cid, user_id, ttl_seconds=86400)
        except UserNotParticipant:
            missing.append(ch)
        except Exception as exc:
            # Skip broken channel & report to main bot logger group
            await report_code_error_to_logger(
                exc, context_label=f"force_join chat={cid}"
            )
            continue

    return (len(missing) == 0), missing


def format_welcome_text(
    template: str,
    *,
    first_name: str,
    last_name: str = "",
    username: str = "",
    user_id: int = 0,
    chat_title: str = "",
    bot_name: str = "Bot",
) -> str:
    fullname = " ".join(filter(None, [first_name, last_name])) or "User"
    mention = f'<a href="tg://user?id={user_id}">{html.escape(fullname)}</a>'
    uname = f"@{username}" if username else fullname
    mapping = {
        "fullname": fullname,
        "firstname": first_name or "User",
        "lastname": last_name or "",
        "username": uname,
        "mention": mention,
        "id": str(user_id),
        "chat_title": chat_title or "Private",
        "bot_name": bot_name,
    }
    result = template
    for key, val in mapping.items():
        result = result.replace(f"{{{key}}}", val)
    return result


# ----------------------------------------------------------------------
# Low-level unified send helper (works for Webhook Bot & MTProto Client)
# ----------------------------------------------------------------------
async def _ctx_send_text(
    ctx: Any,
    chat_id: int,
    text: str,
    *,
    reply_to_msg_id: int | None = None,
    parse_mode: str | None = None,
) -> tuple[bool, int]:
    if hasattr(ctx, "http_session"):
        from Main.utils.bot_webhook import bot_webhook_manager

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to_msg_id:
            payload["reply_parameters"] = {
                "message_id": reply_to_msg_id,
                "allow_sending_without_reply": True,
            }
        ok, data = await bot_webhook_manager._tg_api(ctx.bot_token, "sendMessage", payload)
        if not ok and data.get("error_code") == 429:
            wait_s = int((data.get("parameters") or {}).get("retry_after", 2))
            await asyncio.sleep(min(wait_s + 0.5, 30))
        return ok, int((data.get("result") or {}).get("message_id", 0)) if ok else 0

    try:
        res = await ctx.client.send_message(
            chat_id,
            text,
            reply_parameters=(
                types.ReplyParameters(message_id=reply_to_msg_id) if reply_to_msg_id else None
            ),
        )
        return True, getattr(res, "id", 0)
    except FloodWait as fw:
        await asyncio.sleep(min(fw.value + 1, 30))
        return False, 0
    except Exception:
        return False, 0


# ----------------------------------------------------------------------
# Spam Engine (/spam, /fspam, /tspam)
# ----------------------------------------------------------------------
async def run_spam_task(
    ctx: Any,
    doc: dict[str, Any],
    *,
    mode: str,  # "spam" | "fspam" | "tspam"
    args: str,
) -> None:
    tokens = args.split()
    target_chat_id = ctx.chat_id
    count: int | None = None
    mention_target = ""
    custom_text = ""

    if mode == "tspam":
        if not tokens:
            await ctx.reply_raw("Usage: /tspam [count] <text>", reply=True)
            return
        if tokens[0].isdigit():
            count = max(1, int(tokens[0]))
            custom_text = " ".join(tokens[1:]).strip()
        else:
            custom_text = args.strip()
        if not custom_text:
            await ctx.reply_raw("Please provide text to spam.", reply=True)
            return
    else:
        for tok in tokens:
            if tok.startswith("@"):
                mention_target = tok
            elif tok.lstrip("-").isdigit():
                val = int(tok)
                if val < 0 or abs(val) > 100_000:
                    target_chat_id = val
                else:
                    count = max(1, val)

    duration_limit = 3600.0  # 1 hour max when count is omitted
    delay = 0.08 if doc.get("client_type") == "bot" else 0.45

    async def _worker() -> None:
        t0 = time.monotonic()
        sent = 0
        failed = 0
        lines = FLIRT_LINES if mode == "fspam" else ROAST_LINES
        idx = 0

        try:
            while not should_stop(ctx.client_id):
                if count is not None and (sent + failed) >= count:
                    break
                if count is None and (time.monotonic() - t0) >= duration_limit:
                    break

                if mode == "tspam":
                    msg_text = custom_text
                else:
                    base = lines[idx % len(lines)]
                    idx += 1
                    msg_text = f"{mention_target} {base}".strip() if mention_target else base

                ok, _ = await _ctx_send_text(ctx, target_chat_id, msg_text)
                if ok:
                    sent += 1
                else:
                    failed += 1
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            pass
        finally:
            report = (
                f"📊 **Spam Report ({mode})**\n"
                f"• **Target Chat:** `{target_chat_id}`\n"
                f"• **Sent:** `{sent}` | **Failed:** `{failed}`"
            )
            await _ctx_send_text(ctx, ctx.chat_id, report)
            await log_to_client_logger(
                doc, report, mtproto_client=getattr(ctx, "client", None)
            )

    register_client_task(ctx.client_id, "spam", _worker())
    mode_desc = f"{count} msgs" if count else "1hr (use /stop to end)"
    await ctx.reply_raw(f"🚀 Started {mode} in `{target_chat_id}` for {mode_desc}.", reply=True)


# ----------------------------------------------------------------------
# Tag Engine (/tagall, /tagadmins, /tagusers, /tagactive)
# ----------------------------------------------------------------------
async def _fetch_members_for_tag(
    ctx: Any, doc: dict[str, Any], chat_id: int, mode: str
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    if hasattr(ctx, "client"):
        filt = (
            enums.ChatMembersFilter.ADMINISTRATORS
            if mode == "tagadmins"
            else enums.ChatMembersFilter.SEARCH
        )
        async for m in ctx.client.get_chat_members(chat_id, filter=filt):
            u = m.user
            if u is None or u.is_bot or u.is_deleted:
                continue
            if mode == "tagactive":
                if u.status not in (
                    enums.UserStatus.ONLINE,
                    enums.UserStatus.RECENTLY,
                ):
                    continue
            if mode == "tagusers" and m.status in (
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.OWNER,
            ):
                continue
            members.append({"id": u.id, "name": u.first_name or "User"})
    elif doc.get("bot_token") and mode == "tagadmins":
        from Main.utils.bot_webhook import bot_webhook_manager

        ok, data = await bot_webhook_manager._tg_api(
            doc["bot_token"], "getChatAdministrators", {"chat_id": chat_id}
        )
        if ok:
            for m in data.get("result") or []:
                u = m.get("user") or {}
                if u.get("is_bot"):
                    continue
                members.append({"id": u["id"], "name": u.get("first_name", "User")})
    return members


async def run_tag_task(
    ctx: Any,
    doc: dict[str, Any],
    *,
    mode: str,  # "tagall" | "tagadmins" | "tagusers" | "tagactive"
    args: str,
) -> None:
    if getattr(ctx, "chat_type", "private") == "private":
        await ctx.reply_raw("Tag commands can only be used in groups or channels.", reply=True)
        return

    ok, members, err = await run_safe(
        _fetch_members_for_tag,
        ctx,
        doc,
        ctx.chat_id,
        mode,
        context_label=f"{mode} fetch",
    )
    if not ok:
        await ctx.reply_raw(f"⚠️ **{err.short}:** {err.description}", reply=True)
        return

    if not members:
        await ctx.reply_raw(
            "No matching members found (or bot API mode only supports /tagadmins; use MTProto mode for full member listing).",
            reply=True,
        )
        return

    custom_msg = args.strip()
    reply_id = getattr(ctx, "reply_to_msg_id", None)

    async def _worker() -> None:
        tagged = 0
        batches = 0
        try:
            for i in range(0, len(members), 5):
                if should_stop(ctx.client_id):
                    break
                chunk = members[i : i + 5]
                mentions = " ".join(
                    f'<a href="tg://user?id={m["id"]}">{html.escape(m["name"][:20])}</a>'
                    for m in chunk
                )
                body = f"{custom_msg}\n\n{mentions}".strip() if custom_msg else mentions
                sent_ok, _ = await _ctx_send_text(
                    ctx,
                    ctx.chat_id,
                    body,
                    reply_to_msg_id=reply_id,
                    parse_mode="HTML",
                )
                if sent_ok:
                    tagged += len(chunk)
                    batches += 1
                await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            pass
        finally:
            report = (
                f"🏷 **Tag Report ({mode})**\n"
                f"• **Chat:** `{ctx.chat_id}`\n"
                f"• **Mentioned:** `{tagged}` / `{len(members)}` in `{batches}` messages"
            )
            await _ctx_send_text(ctx, ctx.chat_id, report)
            await log_to_client_logger(
                doc, report, mtproto_client=getattr(ctx, "client", None)
            )

    register_client_task(ctx.client_id, "tag", _worker())
    await ctx.reply_raw(
        f"🏷 Tagging `{len(members)}` members (5 per message). Send /stop to cancel.",
        reply=True,
    )


# ----------------------------------------------------------------------
# Moderation Engine (/ban, /unban, /mute, /unmute, /kick, /banall, /unbanall, /kickall)
# ----------------------------------------------------------------------
async def resolve_target_user_id(ctx: Any, target_arg: str | None = None) -> int | None:
    """Universal target user resolver returning integer user_id or None."""
    uid, _ = await resolve_target_user(ctx, target_arg)
    return uid


_resolve_target_user_id = resolve_target_user_id


async def resolve_target_user(
    ctx: Any, target_arg: str | None = None
) -> tuple[int | None, str | None]:
    """Resolve target user ID and display label from args, reply, or message entities.
    Supports IDs, usernames with/without @ (e.g. AYUSH), mention links, URLs, and reply messages."""
    raw_str = (target_arg or "").strip()
    arg = raw_str.split()[0] if raw_str else ""

    # 1. Telegram tg://user?id=12345678 mention link
    tg_link = re.search(r"tg://user\?id=(-?\d+)", raw_str)
    if tg_link:
        uid = int(tg_link.group(1))
        return uid, str(uid)

    # 2. t.me/username link
    tme_link = re.search(r"(?:https?://)?(?:t|telegram)\.me/([a-zA-Z0-9_]{4,32})", raw_str)
    if tme_link:
        arg = f"@{tme_link.group(1)}"

    # 3. Direct numeric ID (e.g. 12345678 or -10012345678)
    if arg and arg.lstrip("-").isdigit():
        uid = int(arg)
        return uid, str(uid)

    # 4. Check replied-to message
    if getattr(ctx, "reply_to_msg_id", None) and hasattr(ctx, "client") and ctx.client:
        try:
            replied = await ctx.client.get_messages(ctx.chat_id, ctx.reply_to_msg_id)
            if replied:
                if replied.from_user:
                    u = replied.from_user
                    uname = f"@{u.username}" if u.username else (u.first_name or str(u.id))
                    return u.id, uname
                if replied.sender_chat:
                    return replied.sender_chat.id, replied.sender_chat.title or str(replied.sender_chat.id)
        except Exception as exc:
            log.debug("Failed getting replied message in resolve_target_user: %s", exc)

    if isinstance(getattr(ctx, "raw_update", None), dict):
        rep = (ctx.raw_update.get("message") or {}).get("reply_to_message") or {}
        uid = (rep.get("from") or {}).get("id") or (rep.get("sender_chat") or {}).get("id")
        if uid:
            u_label = (rep.get("from") or {}).get("username") or str(uid)
            return int(uid), u_label

    # 5. Check raw_update message entities for embedded user ID
    if hasattr(ctx, "raw_update") and ctx.raw_update:
        raw_msg = getattr(ctx.raw_update, "message", None)
        if raw_msg is not None:
            entities = getattr(raw_msg, "entities", None) or []
            for ent in entities:
                ent_uid = getattr(ent, "user_id", None)
                if ent_uid:
                    return int(ent_uid), str(ent_uid)

    # 6. Username or mention (@username or bare username e.g. "AYUSH")
    if arg:
        clean_name = arg.lstrip("@").strip()
        if clean_name:
            # If MTProto client is available
            if hasattr(ctx, "client") and ctx.client:
                for test_name in (f"@{clean_name}", clean_name):
                    try:
                        u = await ctx.client.get_users(test_name)
                        if u and getattr(u, "id", None):
                            label = (
                                f"@{u.username}"
                                if getattr(u, "username", None)
                                else (getattr(u, "first_name", "") or str(u.id))
                            )
                            return u.id, label
                    except Exception:
                        pass

                # If inside a group chat, search chat members by query
                if getattr(ctx, "chat_id", None):
                    try:
                        async for member in ctx.client.get_chat_members(
                            ctx.chat_id, query=clean_name, limit=5
                        ):
                            if member and member.user:
                                m_uname = (member.user.username or "").lower()
                                m_fname = (member.user.first_name or "").lower()
                                if m_uname == clean_name.lower() or m_fname == clean_name.lower():
                                    lbl = (
                                        f"@{member.user.username}"
                                        if member.user.username
                                        else (member.user.first_name or str(member.user.id))
                                    )
                                    return member.user.id, lbl
                    except Exception:
                        pass

            # If Webhook context is available
            if hasattr(ctx, "bot_token") and ctx.bot_token:
                from Main.utils.bot_webhook import bot_webhook_manager

                for test_name in (f"@{clean_name}", clean_name):
                    try:
                        ok, data = await bot_webhook_manager._tg_api(
                            ctx.bot_token, "getChat", {"chat_id": test_name}
                        )
                        if ok and data.get("result"):
                            res = data["result"]
                            lbl = f"@{res.get('username') or clean_name}"
                            return int(res["id"]), lbl
                    except Exception:
                        pass

    return None, None


async def run_moderation(
    ctx: Any,
    doc: dict[str, Any],
    *,
    action: str,  # "ban" | "unban" | "mute" | "unmute" | "kick"
    args: str,
) -> None:
    if getattr(ctx, "chat_type", "private") == "private":
        await ctx.reply_raw("Moderation commands work in groups/channels only.", reply=True)
        return

    target_id, target_label = await resolve_target_user(ctx, args)
    if not target_id:
        await ctx.reply_raw(
            f"⚠️ Reply to a user or pass a user ID/username/mention to execute <code>/{action}</code>.",
            reply=True,
            parse_mode="html",
        )
        return

    async def _do_action() -> str:
        if hasattr(ctx, "client"):
            cli: Client = ctx.client
            if action == "ban":
                await cli.ban_chat_member(ctx.chat_id, target_id)
            elif action == "unban":
                await cli.unban_chat_member(ctx.chat_id, target_id)
            elif action == "kick":
                await cli.ban_chat_member(ctx.chat_id, target_id)
                await cli.unban_chat_member(ctx.chat_id, target_id)
            elif action == "mute":
                await cli.restrict_chat_member(
                    ctx.chat_id, target_id, types.ChatPermissions()
                )
            elif action == "unmute":
                await cli.restrict_chat_member(
                    ctx.chat_id,
                    target_id,
                    types.ChatPermissions(
                        can_send_messages=True,
                        can_send_media_messages=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True,
                    ),
                )
        else:
            from Main.utils.bot_webhook import bot_webhook_manager

            token = doc["bot_token"]
            if action == "ban":
                ok, data = await bot_webhook_manager._tg_api(
                    token, "banChatMember", {"chat_id": ctx.chat_id, "user_id": target_id}
                )
            elif action == "unban":
                ok, data = await bot_webhook_manager._tg_api(
                    token, "unbanChatMember", {"chat_id": ctx.chat_id, "user_id": target_id, "only_if_banned": True}
                )
            elif action == "kick":
                ok, data = await bot_webhook_manager._tg_api(
                    token, "banChatMember", {"chat_id": ctx.chat_id, "user_id": target_id}
                )
                if ok:
                    await bot_webhook_manager._tg_api(
                        token, "unbanChatMember", {"chat_id": ctx.chat_id, "user_id": target_id}
                    )
            elif action == "mute":
                ok, data = await bot_webhook_manager._tg_api(
                    token,
                    "restrictChatMember",
                    {"chat_id": ctx.chat_id, "user_id": target_id, "permissions": {"can_send_messages": False}},
                )
            else:
                ok, data = await bot_webhook_manager._tg_api(
                    token,
                    "restrictChatMember",
                    {
                        "chat_id": ctx.chat_id,
                        "user_id": target_id,
                        "permissions": {
                            "can_send_messages": True,
                            "can_send_audios": True,
                            "can_send_documents": True,
                            "can_send_photos": True,
                            "can_send_videos": True,
                            "can_send_other_messages": True,
                        },
                    },
                )
            if not ok:
                raise RuntimeError(data.get("description") or f"{action} failed")
        lbl = html.escape(str(target_label or target_id))
        return f"✅ <b>{action.title()}</b> executed on {lbl} (<code>{target_id}</code>)."

    ok, msg, err = await run_safe(_do_action, context_label=f"mod_{action}")
    if ok:
        await ctx.reply_raw(msg, reply=True, parse_mode="html")
    elif err is not None:
        await ctx.reply_raw(f"⚠️ <b>{err.short}:</b> {err.description}", reply=True, parse_mode="html")


def _match_member_filter(user: types.User | None, filter_mode: str) -> bool:
    if user is None:
        return False
    if filter_mode == "deleted":
        return bool(user.is_deleted)
    if filter_mode == "active":
        return user.status in (enums.UserStatus.ONLINE, enums.UserStatus.RECENTLY)
    if filter_mode == "offline":
        return user.status in (
            enums.UserStatus.OFFLINE,
            enums.UserStatus.LAST_WEEK,
            enums.UserStatus.LAST_MONTH,
            enums.UserStatus.LONG_AGO,
        )
    return True


async def run_mass_moderation(
    ctx: Any,
    doc: dict[str, Any],
    *,
    action: str,  # "banall" | "unbanall" | "kickall"
    args: str,
) -> None:
    if not hasattr(ctx, "client"):
        await ctx.reply_raw(
            "Mass moderation requires MTProto mode (Bot API webhooks cannot list all chat members).",
            reply=True,
        )
        return
    if getattr(ctx, "chat_type", "private") == "private":
        await ctx.reply_raw("Mass moderation works in groups/channels only.", reply=True)
        return

    filt_arg = (args.strip().lower() or "all")
    if filt_arg not in ("all", "deleted", "offline", "active"):
        filt_arg = "all"

    me_id = ctx.client.me.id if ctx.client.me else 0
    member_filter = (
        enums.ChatMembersFilter.BANNED
        if action == "unbanall"
        else enums.ChatMembersFilter.SEARCH
    )

    async def _worker() -> None:
        done = 0
        failed = 0
        try:
            async for m in ctx.client.get_chat_members(ctx.chat_id, filter=member_filter):
                if should_stop(ctx.client_id):
                    break
                u = m.user
                if u is None or u.id == me_id or u.id == ctx.owner_id or u.id in Config.OWNER_IDS:
                    continue
                if action != "unbanall" and m.status in (
                    enums.ChatMemberStatus.ADMINISTRATOR,
                    enums.ChatMemberStatus.OWNER,
                ):
                    continue
                if not _match_member_filter(u, filt_arg):
                    continue

                try:
                    if action == "banall":
                        await ctx.client.ban_chat_member(ctx.chat_id, u.id)
                    elif action == "unbanall":
                        await ctx.client.unban_chat_member(ctx.chat_id, u.id)
                    elif action == "kickall":
                        await ctx.client.ban_chat_member(ctx.chat_id, u.id)
                        await ctx.client.unban_chat_member(ctx.chat_id, u.id)
                    done += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except RPCError:
                    failed += 1
                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            pass
        finally:
            report = (
                f"🛡 **Mass {action.title()} Report (filter={filt_arg})**\n"
                f"• **Chat:** `{ctx.chat_id}`\n"
                f"• **Processed:** `{done}` | **Failed/Skipped:** `{failed}`"
            )
            await _ctx_send_text(ctx, ctx.chat_id, report)
            await log_to_client_logger(doc, report, mtproto_client=ctx.client)

    register_client_task(ctx.client_id, "mass_mod", _worker())
    await ctx.reply_raw(
        f"🛡 Started `{action}` (filter: `{filt_arg}`). Send /stop to cancel.",
        reply=True,
    )


# ----------------------------------------------------------------------
# Forward / Copy Engine (/forward)
# ----------------------------------------------------------------------
_TG_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/)?([^/\s]+)(?:/(\d+))?")


def parse_forward_args(args: str) -> tuple[str | int | None, int | None, int | None]:
    tokens = args.strip().split()
    from_chat: str | int | None = None
    start_id: int | None = None
    end_id: int | None = None

    for tok in tokens:
        m = _TG_LINK_RE.match(tok)
        if m:
            slug, msg_part = m.group(1), m.group(2)
            from_chat = int(f"-100{slug}") if slug.isdigit() else slug
            if msg_part and start_id is None:
                start_id = int(msg_part)
        elif tok.lstrip("-").isdigit():
            val = int(tok)
            if from_chat is None and (val < 0 or abs(val) > 100_000_000):
                from_chat = val
            elif start_id is None:
                start_id = val
            elif end_id is None:
                end_id = val
        elif from_chat is None and tok.startswith("@"):
            from_chat = tok
    return from_chat, start_id, end_id


async def run_forward_task(
    ctx: Any,
    doc: dict[str, Any],
    *,
    from_chat: str | int,
    to_chat: int,
    start_id: int,
    end_id: int,
    copy_mode: bool = True,
) -> None:
    if not hasattr(ctx, "client"):
        await ctx.reply_raw("Forwarding requires MTProto mode.", reply=True)
        return

    cli: Client = ctx.client
    # Validate access to BOTH source and destination chats via get_chat
    ok_src, src_chat, err_src = await run_safe(
        cli.get_chat, from_chat, context_label="forward_get_src"
    )
    if not ok_src or src_chat is None:
        msg = err_src.description if err_src else "Cannot access source chat."
        await ctx.reply_raw(f"❌ Client cannot access source chat `{from_chat}`: {msg}", reply=True)
        return

    ok_dst, dst_chat, err_dst = await run_safe(
        cli.get_chat, to_chat, context_label="forward_get_dst"
    )
    if not ok_dst or dst_chat is None:
        msg = err_dst.description if err_dst else "Cannot access target chat."
        await ctx.reply_raw(f"❌ Client cannot access target chat `{to_chat}`: {msg}", reply=True)
        return

    lo, hi = min(start_id, end_id), max(start_id, end_id)

    async def _worker() -> None:
        done = 0
        skipped = 0
        try:
            for msg_id in range(lo, hi + 1):
                if should_stop(ctx.client_id):
                    break
                try:
                    if copy_mode:
                        await cli.copy_message(to_chat, src_chat.id, msg_id)
                    else:
                        await cli.forward_messages(to_chat, src_chat.id, msg_id)
                    done += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                    try:
                        if copy_mode:
                            await cli.copy_message(to_chat, src_chat.id, msg_id)
                        else:
                            await cli.forward_messages(to_chat, src_chat.id, msg_id)
                        done += 1
                    except Exception:
                        skipped += 1
                except RPCError:
                    skipped += 1
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            pass
        finally:
            mode_lbl = "Copy" if copy_mode else "Forward"
            report = (
                f"📦 **{mode_lbl} Report**\n"
                f"• **From:** `{src_chat.id}` → **To:** `{to_chat}`\n"
                f"• **Range:** `{lo}..{hi}`\n"
                f"• **Success:** `{done}` | **Skipped:** `{skipped}`"
            )
            await _ctx_send_text(ctx, ctx.chat_id, report)
            await log_to_client_logger(doc, report, mtproto_client=cli)

    register_client_task(ctx.client_id, "forward", _worker())
    await ctx.reply_raw(
        f"📦 Started {'copy' if copy_mode else 'forward'} `{lo}..{hi}` from `{src_chat.id}` to `{to_chat}`. Send /stop to cancel.",
        reply=True,
    )


# ----------------------------------------------------------------------
# Per-Client RAM Estimator (isolated to a single client_id)
# ----------------------------------------------------------------------
def estimate_client_ram_bytes(client_id: str) -> int:
    """Estimate in-RAM byte footprint of one hosted client only (never global RSS)."""
    import sys

    total = 0
    from Main.utils.bot_webhook import bot_webhook_manager
    from Main.utils.client_manager import client_manager
    from Main.utils.peer_tracker import peer_tracker

    wb = bot_webhook_manager.get_entry(client_id)
    if wb is not None:
        total += sys.getsizeof(wb) + sys.getsizeof(wb.stats)
        total += sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in wb.stats.items())

    rt = client_manager.get_runtime(client_id)
    if rt is not None:
        total += sys.getsizeof(rt) + sys.getsizeof(rt.stats) + sys.getsizeof(rt.client)
        storage = getattr(rt.client, "storage", None)
        if storage is not None:
            total += sys.getsizeof(storage)
            for attr in ("_peer_lru", "_username_lru", "_state_cache"):
                cache = getattr(storage, attr, None)
                if isinstance(cache, dict):
                    total += sys.getsizeof(cache) + len(cache) * 96
        # Kurigram client message/topic cache overhead
        msg_cache = getattr(rt.client, "message_cache", None)
        if msg_cache is not None:
            total += sys.getsizeof(msg_cache)

    raid_map = _RAID_TARGETS.get(client_id)
    if raid_map:
        total += sys.getsizeof(raid_map) + len(raid_map) * 64

    # Peer tracker pending/seen entries belonging strictly to this client_id
    total += sum(96 for k in peer_tracker._seen if k[0] == client_id)
    total += sum(160 for k in peer_tracker._pending if k[0] == client_id)
    return max(total, 1024)


# ----------------------------------------------------------------------
# Bot Help Widget HTML + Command Auto-Registration
# ----------------------------------------------------------------------
def build_bot_help_widget_html(bot_name: str = "Hosted Bot") -> str:
    safe_name = html.escape(bot_name[:40])
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root{{
    --bg:var(--tg-theme-bg-color,#0b1020);
    --text:var(--tg-theme-text-color,#f4f7ff);
    --muted:var(--tg-theme-hint-color,#9aa8c7);
    --accent:var(--tg-theme-button-color,#38bdf8);
    --accent2:#a855f7;
    --ok:#34d399;
    --warn:#fbbf24;
    --pink:#f472b6;
    --glass:rgba(255,255,255,0.07);
    --border:rgba(255,255,255,0.14);
  }}
  *{{box-sizing:border-box}}
  body{{
    margin:0;padding:12px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    background:radial-gradient(circle at 10% 10%,rgba(56,189,248,.22),transparent 45%),
               radial-gradient(circle at 90% 85%,rgba(168,85,247,.22),transparent 45%),
               var(--bg);
    color:var(--text);font-size:13px;
  }}
  .panel{{
    background:var(--glass);
    backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
    border:1px solid var(--border);border-radius:16px;padding:12px;
    box-shadow:0 10px 28px rgba(0,0,0,.35);
  }}
  .hdr{{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}}
  .title{{font-size:15px;font-weight:800;background:linear-gradient(90deg,#38bdf8,#a855f7,#f472b6);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .badge{{font-size:10px;font-weight:700;padding:3px 8px;border-radius:999px;background:rgba(56,189,248,.18);border:1px solid rgba(56,189,248,.4);color:#7dd3fc}}
  .search{{width:100%;padding:7px 10px;border-radius:10px;border:1px solid var(--border);background:rgba(0,0,0,.22);color:var(--text);font-size:12px;margin-bottom:9px;outline:none}}
  .tabs{{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}}
  .tab{{border:1px solid var(--border);border-radius:999px;padding:5px 11px;font-size:11.5px;font-weight:700;cursor:pointer;background:rgba(255,255,255,.05);color:var(--text);transition:.2s}}
  .tab.active{{background:linear-gradient(135deg,var(--accent),var(--accent2));border-color:transparent;color:#fff;box-shadow:0 4px 12px rgba(56,189,248,.35)}}
  .list{{display:flex;flex-direction:column;gap:7px;max-height:260px;overflow-y:auto}}
  .card{{background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);border-radius:11px;padding:8px 10px}}
  .cmd{{font-weight:800;font-size:12.5px;color:#7dd3fc}}
  .arg{{font-style:italic;color:var(--warn);font-size:11.5px;margin-left:4px}}
  .desc{{margin-top:3px;font-size:11.8px;color:var(--muted);line-height:1.35}}
  .tag{{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:6px;margin-left:6px;background:rgba(52,211,153,.16);color:var(--ok)}}
  .tag.own{{background:rgba(244,114,182,.16);color:var(--pink)}}
</style>
</head>
<body>
<div class="panel">
  <div class="hdr">
    <div class="title">✨ {safe_name} — Command Studio</div>
    <span class="badge">Glass UI</span>
  </div>
  <input class="search" id="q" placeholder="Search commands (e.g. spam, ban, forward)..." oninput="render()">
  <div class="tabs">
    <button class="tab active" onclick="setTab('all',this)">All</button>
    <button class="tab" onclick="setTab('core',this)">Core</button>
    <button class="tab" onclick="setTab('mod',this)">Admin</button>
    <button class="tab" onclick="setTab('fun',this)">Spam &amp; Raid</button>
    <button class="tab" onclick="setTab('tools',this)">Tools</button>
  </div>
  <div class="list" id="out"></div>
</div>
<script>
const CMDS=[
  {{c:"core",n:"/start",a:"",d:"Show custom welcome message & force-join check.",r:"Everyone"}},
  {{c:"core",n:"/help",a:"",d:"Open this interactive glassmorphism command guide.",r:"Everyone"}},
  {{c:"core",n:"/ping",a:"",d:"Check bot response latency & online status.",r:"Everyone"}},
  {{c:"core",n:"/stats",a:"",d:"Inspect this bot's own RAM, peers, uptime & stats.",r:"Owner"}},
  {{c:"core",n:"/stop",a:"",d:"Immediately cancel active spam, tag, or forward tasks.",r:"Owner"}},
  {{c:"core",n:"/leave",a:"",d:"Make the bot leave the current group or channel.",r:"Owner"}},
  {{c:"mod",n:"/ban /unban /mute /unmute /kick",a:"[reply|@user|id]",d:"Quick group moderation for chat admins & owners.",r:"Admin"}},
  {{c:"mod",n:"/banall /unbanall /kickall",a:"[all|deleted|offline|active]",d:"Mass member moderation with status filters.",r:"Owner"}},
  {{c:"fun",n:"/spam /fspam",a:"[count] [@user] [chat_id]",d:"Roast or flirt spam (1hr limit if count omitted).",r:"Owner"}},
  {{c:"fun",n:"/tspam",a:"[count] &lt;text&gt;",d:"Repeat custom text message with live report.",r:"Owner"}},
  {{c:"fun",n:"/tagall /tagadmins /tagusers /tagactive",a:"[text]",d:"Mention members in batches of 5 with stop support.",r:"Owner"}},
  {{c:"fun",n:"/raid /fraid /unraid",a:"[reply|id]",d:"Lock a target user in RAM for auto-reply roast/flirt.",r:"Owner"}},
  {{c:"tools",n:"/forward",a:"[from_chat|link] [start] [end]",d:"Copy or forward message ranges between chats.",r:"Owner"}},
  {{c:"tools",n:"/broadcast",a:"[-users|-groups|-channels]",d:"Deliver replied message to this bot's tracked peers.",r:"Owner"}},
  {{c:"tools",n:"/info",a:"[reply]",d:"Inspect full raw Telegram JSON of any message.",r:"Owner"}}
];
let cur="all";
function setTab(t,el){{cur=t;document.querySelectorAll(".tab").forEach(b=>b.classList.remove("active"));if(el)el.classList.add("active");render();}}
function render(){{
  const q=(document.getElementById("q").value||"").toLowerCase();
  const rows=CMDS.filter(x=>(cur==="all"||x.c===cur)&&(!q||x.n.toLowerCase().includes(q)||x.d.toLowerCase().includes(q)));
  document.getElementById("out").innerHTML=rows.map(x=>`<div class="card"><div><span class="cmd">${{x.n}}</span><span class="arg">${{x.a}}</span><span class="tag ${{x.r==='Owner'?'own':''}}">${{x.r}}</span></div><div class="desc"><i>${{x.d}}</i></div></div>`).join("")||`<div class="card"><div class="desc"><i>No matching commands found.</i></div></div>`;
}}
render();
</script>
</body>
</html>"""


async def register_bot_commands(
    bot_token: str,
    features: dict[str, bool] | None = None,
    owner_id: int | None = None,
) -> None:
    """Register scoped bot commands via Telegram Bot API setMyCommands:
    1. Default scope: Strictly public commands (/start, /ping, /help).
    2. Group Admins scope: Public commands + basic moderation.
    3. Owner Chat scope: Complete command suite for the bot owner & main bot owner.
    """
    if not bot_token:
        return
    from config import Config
    from Main.utils.bot_webhook import bot_webhook_manager

    feat = features or {}

    # 1. Public commands for regular users
    public_commands: list[dict[str, str]] = [
        {"command": "start", "description": "Start the bot"},
        {"command": "ping", "description": "Check bot latency"},
        {"command": "help", "description": "Commands guide"},
    ]
    await bot_webhook_manager._tg_api(
        bot_token,
        "setMyCommands",
        {"commands": public_commands, "scope": {"type": "default"}},
    )

    # 2. Group administrators scope
    admin_commands: list[dict[str, str]] = list(public_commands)
    if feat.get("moderation", True):
        admin_commands.extend([
            {"command": "ban", "description": "Ban a user"},
            {"command": "unban", "description": "Unban a user"},
            {"command": "mute", "description": "Mute a user"},
            {"command": "unmute", "description": "Unmute a user"},
            {"command": "kick", "description": "Kick a user"},
        ])
    await bot_webhook_manager._tg_api(
        bot_token,
        "setMyCommands",
        {"commands": admin_commands, "scope": {"type": "all_chat_administrators"}},
    )

    # 3. Complete suite for the bot owner & main bot owner (Hidden Lord of All Access)
    owner_commands: list[dict[str, str]] = [
        {"command": "start", "description": "Start the bot"},
        {"command": "ping", "description": "Check bot latency"},
        {"command": "stats", "description": "View this bot's stats & RAM"},
        {"command": "help", "description": "Interactive commands guide"},
        {"command": "leave", "description": "Make bot leave current chat"},
        {"command": "stop", "description": "Stop running task"},
    ]

    if feat.get("spam", True):
        owner_commands.extend([
            {"command": "spam", "description": "Spam messages"},
            {"command": "fspam", "description": "Flirt spam"},
            {"command": "tspam", "description": "Custom text spam"},
        ])

    if feat.get("tag", True):
        owner_commands.extend([
            {"command": "tagall", "description": "Mention all members"},
            {"command": "tagadmins", "description": "Mention admins"},
            {"command": "tagusers", "description": "Mention non-admin users"},
            {"command": "tagactive", "description": "Mention recently active users"},
        ])

    if feat.get("moderation", True):
        owner_commands.extend([
            {"command": "ban", "description": "Ban a user"},
            {"command": "unban", "description": "Unban a user"},
            {"command": "mute", "description": "Mute a user"},
            {"command": "unmute", "description": "Unmute a user"},
            {"command": "kick", "description": "Kick a user"},
            {"command": "banall", "description": "Mass ban with optional filter"},
            {"command": "unbanall", "description": "Mass unban with optional filter"},
            {"command": "kickall", "description": "Mass kick with optional filter"},
        ])

    if feat.get("forward", True):
        owner_commands.append({"command": "forward", "description": "Copy/forward messages between chats"})

    if feat.get("raid", True):
        owner_commands.extend([
            {"command": "raid", "description": "Lock & auto-reply roast on target"},
            {"command": "fraid", "description": "Lock & auto-reply flirt on target"},
            {"command": "unraid", "description": "Stop raiding target"},
        ])

    if feat.get("broadcast", True):
        owner_commands.append({"command": "broadcast", "description": "Broadcast to bot audience"})

    if feat.get("info", True):
        owner_commands.append({"command": "info", "description": "Get full raw Telegram JSON"})

    target_owner_ids = set()
    if owner_id:
        target_owner_ids.add(int(owner_id))
    target_owner_ids.update(Config.OWNER_IDS)

    for oid in target_owner_ids:
        if oid:
            await bot_webhook_manager._tg_api(
                bot_token,
                "setMyCommands",
                {"commands": owner_commands, "scope": {"type": "chat", "chat_id": oid}},
            )
