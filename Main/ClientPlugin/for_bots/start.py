"""Core /start, /ping, /help (HTML Widget), /leave, /stop plugins for hosted bots."""

from __future__ import annotations

import html
import time
from typing import Union

from pyrogram import raw

from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import (
    ALL_FEATURES,
    check_client_access,
    check_force_join,
    estimate_client_ram_bytes,
    format_welcome_text,
    log_to_client_logger,
    stop_client_tasks,
)
from Main.utils.error_handler import run_safe

BotCtx = Union[RawContext, BotWebhookContext]


def build_hosted_bot_help_text(
    bot_name: str,
    features: dict[str, bool] | None = None,
    *,
    is_owner: bool = False,
    is_admin: bool = False,
) -> str:
    feat = features or {}
    bname = html.escape(bot_name.upper())

    # For non-owners: Strictly show only safe public commands & admin moderation
    if not is_owner:
        sections = [
            "🎮 <b>CORE COMMANDS</b>\n"
            "<blockquote>"
            "• <code>/start</code> — Wake bot & check operational status\n"
            "• <code>/help</code> — Open commands guide\n"
            "• <code>/ping</code> — Instant bot roundtrip latency"
            "</blockquote>"
        ]
        if is_admin and feat.get("moderation", True):
            sections.append(
                "🛡️ <b>GROUP MODERATION</b>\n"
                "<blockquote>"
                "• <code>/ban</code> | <code>/unban</code> (reply/user) — Ban / unban user\n"
                "• <code>/mute</code> | <code>/unmute</code> (reply/user) — Mute / unmute user\n"
                "• <code>/kick</code> (reply/user) — Kick member from group"
                "</blockquote>"
            )
        header = (
            f"╔═══════════════════════════╗\n"
            f"   ✨ <b>{bname} COMMANDS</b> ✨\n"
            f"╚═══════════════════════════╝\n\n"
            f"<i>Public Command Guide</i>\n\n"
        )
        return header + "\n\n".join(sections)

    # For Bot Owner & Main Bot Owner (Hidden Lord of All Access)
    sections = [
        "🎮 <b>CORE CONTROLS</b>\n"
        "<blockquote>"
        "• <code>/start</code> — Wake bot & check operational status\n"
        "• <code>/help</code> — Open this interactive commands guide\n"
        "• <code>/ping</code> — Instant bot & MTProto roundtrip latency\n"
        "• <code>/stats</code> — View bot RAM consumption & activity\n"
        "• <code>/leave</code> — Order bot to gracefully leave chat\n"
        "• <code>/stop</code> — Instantly halt active tasks (spam/tag/forward)"
        "</blockquote>"
    ]

    if feat.get("moderation", True):
        sections.append(
            "🛡️ <b>ADMIN & MODERATION SUITE</b>\n"
            "<blockquote>"
            "• <code>/ban</code> | <code>/unban</code> (reply/user) — Ban / unban user\n"
            "• <code>/mute</code> | <code>/unmute</code> (reply/user) — Mute / unmute user\n"
            "• <code>/kick</code> (reply/user) — Kick member from group\n"
            "• <code>/banall [active|offline|deleted]</code> — Mass ban members\n"
            "• <code>/kickall [active|offline|deleted]</code> — Mass kick members\n"
            "• <code>/unbanall [active|offline|deleted]</code> — Mass unban members"
            "</blockquote>"
        )

    if feat.get("spam", True):
        sections.append(
            "🚀 <b>HIGH-SPEED SPAM ENGINE</b>\n"
            "<blockquote>"
            "• <code>/spam [count] [@user]</code> — High-speed abusive flood\n"
            "• <code>/fspam [count] [@user]</code> — High-speed flirt line flood\n"
            "• <code>/tspam &lt;count&gt; &lt;text&gt;</code> — Custom text spam flood\n"
            "<i>(Default count: 1hr continuous spam until /stop)</i>"
            "</blockquote>"
        )

    if feat.get("tag", True):
        sections.append(
            "👥 <b>SMART MENTION ENGINE (5/msg)</b>\n"
            "<blockquote>"
            "• <code>/tagall [msg]</code> — Mention all chat members\n"
            "• <code>/tagadmins [msg]</code> — Mention chat administrators only\n"
            "• <code>/tagusers [msg]</code> — Mention standard members only\n"
            "• <code>/tagactive [msg]</code> — Mention recently active users"
            "</blockquote>"
        )

    if feat.get("raid", True):
        sections.append(
            "🎯 <b>RAID & AUTO-REPLY LOCKS</b>\n"
            "<blockquote>"
            "• <code>/raid</code> (reply) — Lock target & auto-roast on any message\n"
            "• <code>/fraid</code> (reply) — Lock target & auto-flirt on any message\n"
            "• <code>/unraid</code> — Release target and stop active raid"
            "</blockquote>"
        )

    if feat.get("forward", True):
        sections.append(
            "📂 <b>MESSAGE FORWARDER & CLONER</b>\n"
            "<blockquote>"
            "• <code>/forward &lt;from_chat&gt; &lt;start_id&gt; &lt;end_id&gt;</code> — Clone or copy message ranges"
            "</blockquote>"
        )

    if feat.get("broadcast", True):
        sections.append(
            "📣 <b>BROADCAST ENGINE</b>\n"
            "<blockquote>"
            "• <code>/broadcast</code> (reply) — Blast message to all accessible chats"
            "</blockquote>"
        )

    if feat.get("info", True):
        sections.append(
            "🔍 <b>DEVELOPER & INSPECT</b>\n"
            "<blockquote>"
            "• <code>/info</code> (reply) — Dump full raw Telegram JSON"
            "</blockquote>"
        )

    header = (
        f"╔═══════════════════════════╗\n"
        f"   ✨ <b>{bname} COMMANDS</b> ✨\n"
        f"╚═══════════════════════════╝\n\n"
        f"<i>Ultra-lightweight engine • Ram-friendly execution</i>\n\n"
    )
    footer = "\n\n💡 <i>Need to abort a task? Send <code>/stop</code> at any time.</i>"
    return header + "\n\n".join(sections) + footer


@bot_command("start")
async def hosted_bot_start(ctx: BotCtx) -> None:
    """Respond to /start with custom welcome formatting and optional force-join check."""
    allowed, doc, err_msg = await check_client_access(ctx, feature=None, owner_only=False)
    if not allowed:
        await ctx.reply_raw(err_msg, reply=True)
        return

    # Force-join check via main bot + 24h LMDB cache
    if ctx.chat_type == "private" and ctx.sender_id:
        ok_fj, missing = await check_force_join(ctx.sender_id, is_main_bot=False)
        if not ok_fj and missing:
            lines = ["⚠️ Please join our required channel(s) to use this bot:\n"]
            for ch in missing:
                link = ch.get("invite_link") or f"https://t.me/{str(ch.get('chat_id', '')).lstrip('-100')}"
                lines.append(f"• {ch.get('title') or ch.get('chat_id')}: {link}")
            lines.append("\nAfter joining, send /start again.")
            await ctx.reply_raw("\n".join(lines), reply=True)
            return

    sender_name = getattr(ctx, "sender_name", "") or "User"
    sender_username = getattr(ctx, "sender_username", "") or ""
    bot_name = (doc.get("name") if doc else None) or "Bot"
    custom_welcome = (doc.get("welcome_text") if doc else "") or ""

    if custom_welcome:
        msg_text = format_welcome_text(
            custom_welcome,
            first_name=sender_name,
            username=sender_username,
            user_id=ctx.sender_id,
            bot_name=bot_name,
        )
        await ctx.reply_raw(msg_text, reply=True, parse_mode="HTML")
        return

    uptime_sec = max(0, int(time.time() - ctx.stats.get("started_at", time.time())))
    await ctx.reply_raw(
        f"Hello {sender_name}! {bot_name} is online.\n\n"
        f"Uptime: {uptime_sec}s | Commands run: {ctx.stats.get('commands_handled', 0)}\n"
        "Send /help to view all commands.",
        reply=True,
    )


@bot_command("ping")
async def hosted_bot_ping(ctx: BotCtx) -> None:
    ok, rtt_ms, err = await run_safe(ctx.ping_ms, context_label="bot_ping")
    if ok:
        await ctx.reply_raw(f"Pong! {rtt_ms:.2f} ms", reply=True)
    elif err:
        await ctx.reply_raw(f"⚠️ {err.short}: {err.description}", reply=True)


@bot_command("help")
async def hosted_bot_help(ctx: BotCtx) -> None:
    """Send beautifully formatted commands guide with rich styling."""
    allowed, doc, err_msg = await check_client_access(ctx, feature="help", owner_only=False)
    if not allowed:
        await ctx.reply_raw(err_msg, reply=True)
        return

    from config import Config
    sender_id = int(getattr(ctx, "sender_id", 0) or 0)
    owner_id = int(getattr(ctx, "owner_id", 0) or 0)
    is_main_owner = sender_id in Config.OWNER_IDS
    is_client_owner = sender_id and (sender_id == owner_id)
    sudo_users = set((doc.get("sudo_users") if doc else []) or [])
    is_owner = is_main_owner or is_client_owner or (sender_id in sudo_users) or getattr(ctx, "is_outgoing", False)

    is_admin = False
    if not is_owner and getattr(ctx, "chat_type", "private") != "private":
        try:
            if hasattr(ctx, "http_session") and doc and doc.get("bot_token"):
                from Main.utils.bot_webhook import bot_webhook_manager
                ok, data = await bot_webhook_manager._tg_api(
                    doc["bot_token"],
                    "getChatMember",
                    {"chat_id": ctx.chat_id, "user_id": sender_id},
                )
                status = ((data.get("result") or {}).get("status") or "").lower()
                is_admin = ok and status in ("administrator", "creator")
            elif hasattr(ctx, "client"):
                from pyrogram import enums
                member = await ctx.client.get_chat_member(ctx.chat_id, sender_id)
                is_admin = member.status in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER)
        except Exception:
            is_admin = False

    bot_name = (doc.get("name") if doc else None) or "Hosted Bot"
    features = (doc.get("features") if doc else None) or {}
    help_text = build_hosted_bot_help_text(
        bot_name, features, is_owner=is_owner, is_admin=is_admin
    )

    await ctx.reply_raw(help_text, reply=True, parse_mode="html")


@bot_command("leave")
async def hosted_bot_leave(ctx: BotCtx) -> None:
    allowed, doc, err_msg = await check_client_access(ctx, owner_only=True)
    if not allowed:
        await ctx.reply_raw(err_msg, reply=True)
        return
    if ctx.chat_type == "private":
        await ctx.reply_raw("Cannot leave a private chat.", reply=True)
        return

    await ctx.reply_raw("👋 Leaving this chat as ordered.", reply=False)
    if hasattr(ctx, "client"):
        ok, _, err = await run_safe(ctx.client.leave_chat, ctx.chat_id, context_label="bot_leave")
    else:
        from Main.utils.bot_webhook import bot_webhook_manager

        ok, data = await bot_webhook_manager._tg_api(
            doc["bot_token"], "leaveChat", {"chat_id": ctx.chat_id}
        )
        err = None if ok else type("E", (), {"short": "Leave Failed", "description": data.get("description", "")})()
    if ok:
        await log_to_client_logger(
            doc, f"🚪 Left chat `{ctx.chat_id}`.", mtproto_client=getattr(ctx, "client", None)
        )
    elif err:
        await ctx.reply_raw(f"⚠️ {err.short}: {err.description}", reply=True)


@bot_command("stop")
async def hosted_bot_stop(ctx: BotCtx) -> None:
    allowed, _, err_msg = await check_client_access(ctx, owner_only=True)
    if not allowed:
        await ctx.reply_raw(err_msg, reply=True)
        return
    cnt = stop_client_tasks(ctx.client_id)
    await ctx.reply_raw(
        f"🛑 Stopped {cnt} active background task(s)." if cnt else "No active tasks running.",
        reply=True,
    )


@bot_command("stats")
async def hosted_bot_stats(ctx: BotCtx) -> None:
    """Show ONLY this bot's own details, commands, peers, and per-bot RAM usage."""
    allowed, doc, err_msg = await check_client_access(ctx, owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err_msg or "Access denied.", reply=True)
        return

    from Main.utils.bot_webhook import bot_webhook_manager
    from Main.utils.client_manager import client_manager
    from Main.utils.peer_tracker import peer_tracker
    from Main.utils.rich_ui import format_uptime

    cid = ctx.client_id
    snap = client_manager.get_stats_snapshot(cid)
    is_wb = bot_webhook_manager.get_entry(cid) is not None
    transport = "Bot API Webhook" if is_wb else "Raw MTProto"
    if snap is not None:
        uptime_str = format_uptime(time.time() - snap.get("started_at", time.time()))
        updates_cnt = snap.get("updates_handled", 0)
        cmds_cnt = snap.get("commands_handled", 0)
    else:
        uptime_str = "0s"
        updates_cnt = doc.get("updates_handled", 0)
        cmds_cnt = doc.get("commands_handled", 0)

    counts = await peer_tracker.get_counts(cid)
    features = doc.get("features") or {}
    enabled_feats = sum(1 for k in ALL_FEATURES if features.get(k, True))
    ram_kb = estimate_client_ram_bytes(cid) / 1024.0
    ram_str = f"{ram_kb / 1024:.2f} MB" if ram_kb >= 1024 else f"{ram_kb:.1f} KB"

    uname = f"@{doc['username']}" if doc.get("username") else "N/A"
    await ctx.reply_raw(
        f"📊 Bot Stats — {doc.get('name', cid)} ({uname})\n\n"
        f"• Bot ID: {doc.get('tg_id')}\n"
        f"• Transport: {transport}\n"
        f"• Bot RAM Usage: {ram_str}\n"
        f"• Uptime: {uptime_str}\n"
        f"• Updates Handled: {updates_cnt}\n"
        f"• Commands Run: {cmds_cnt}\n"
        f"• Features Enabled: {enabled_feats}/{len(ALL_FEATURES)}\n"
        f"• Audience: {counts.get('user', 0)} users | {counts.get('group', 0)} groups | {counts.get('channel', 0)} channels",
        reply=True,
    )
