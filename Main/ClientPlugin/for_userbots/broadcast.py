"""Owner .broadcast plugin for hosted userbots with safe double confirmation."""

from __future__ import annotations

import asyncio
import html
import time
from dataclasses import dataclass
from typing import Any

from pyrogram import enums
from pyrogram.errors import FloodWait, RPCError

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import (
    check_client_access,
    log_to_client_logger,
    register_client_task,
    should_stop,
)


@dataclass
class PendingUserbotBroadcast:
    scope: str
    text: str
    reply_to_id: int | None
    estimated_count: int
    stage: int  # 1 = awaiting first confirmation, 2 = awaiting final double-confirmation
    expires_at: float


_PENDING_BROADCASTS: dict[str, PendingUserbotBroadcast] = {}


def _parse_ub_broadcast(args: str) -> tuple[str, str]:
    tokens = args.strip().split(None, 1)
    if not tokens:
        return "", ""
    head = tokens[0].lower().lstrip("-")
    rest = tokens[1].strip() if len(tokens) > 1 else ""
    if head in ("all", "private", "users", "user", "groups", "group", "channels", "channel"):
        norm = {
            "users": "private",
            "user": "private",
            "group": "groups",
            "channel": "channels",
        }.get(head, head)
        return norm, rest
    return "all", args.strip()


async def _count_target_dialogs(cli: Any, scope: str) -> int:
    cnt = 0
    try:
        async for dlg in cli.get_dialogs():
            chat = dlg.chat
            if not chat:
                continue
            if scope == "private" and chat.type == enums.ChatType.PRIVATE:
                cnt += 1
            elif scope == "groups" and chat.type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
                cnt += 1
            elif scope == "channels" and chat.type == enums.ChatType.CHANNEL:
                cnt += 1
            elif scope == "all":
                cnt += 1
    except Exception:
        pass
    return cnt


@userbot_command("broadcast")
async def hosted_userbot_broadcast(ctx: RawContext) -> None:
    """Safe, double-confirmed userbot broadcast."""
    allowed, doc, err = await check_client_access(ctx, feature="broadcast", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return

    scope, text = _parse_ub_broadcast(ctx.args)
    if not scope or (not text and not ctx.reply_to_msg_id):
        await ctx.edit_raw(
            "⚠️ <b>Userbot Broadcast Usage:</b>\n\n"
            "• <code>.broadcast groups &lt;message&gt;</code> (Groups only)\n"
            "• <code>.broadcast private &lt;message&gt;</code> (Private DMs only)\n"
            "• <code>.broadcast channels &lt;message&gt;</code> (Channels only)\n"
            "• <code>.broadcast all &lt;message&gt;</code> (Every chat)\n\n"
            "<i>Or reply to any text/media message with <code>.broadcast &lt;scope&gt;</code></i>",
            parse_mode="html",
        )
        return

    await ctx.edit_raw("⏳ Calculating target audience and preparing safety preview...")
    count = await _count_target_dialogs(ctx.client, scope)

    preview_snippet = text[:120] + ("..." if len(text) > 120 else "")
    if not preview_snippet and ctx.reply_to_msg_id:
        preview_snippet = f"[Replied Message ID: {ctx.reply_to_msg_id}]"

    _PENDING_BROADCASTS[ctx.client_id] = PendingUserbotBroadcast(
        scope=scope,
        text=text,
        reply_to_id=ctx.reply_to_msg_id,
        estimated_count=count,
        stage=1,
        expires_at=time.time() + 60.0,
    )

    scope_title = {
        "private": "Private Chats (DMs only)",
        "groups": "Groups & Supergroups only",
        "channels": "Channels only",
        "all": "ALL Dialogs (Private + Groups + Channels)",
    }.get(scope, scope)

    card = (
        "⚠️ <b>BROADCAST SAFETY REVIEW (Stage 1 of 2)</b>\n\n"
        "<blockquote>"
        f"• <b>Target Audience:</b> {scope_title}\n"
        f"• <b>Estimated Recipients:</b> <code>{count}</code> chats\n"
        f"• <b>Content Preview:</b> <i>{html.escape(preview_snippet)}</i>\n"
        f"• <b>Source Account:</b> <code>{ctx.client_id}</code>"
        "</blockquote>\n\n"
        "⚠️ <i>Please review carefully. This will dispatch messages across your chats.</i>\n\n"
        "👉 Send <code>.confirm_broadcast</code> within 60s to proceed to Final Confirmation.\n"
        "👉 Send <code>.cancel</code> to abort."
    )
    await ctx.edit_raw(card, parse_mode="html")


@userbot_command("confirm_broadcast")
async def hosted_userbot_confirm_broadcast(ctx: RawContext) -> None:
    """Step 2 of 2: Double-confirmation prompt before dispatching userbot broadcast."""
    draft = _PENDING_BROADCASTS.get(ctx.client_id)
    if not draft or draft.stage != 1 or time.time() > draft.expires_at:
        await ctx.edit_raw("❌ No broadcast draft is pending confirmation, or the confirmation window expired.")
        return

    draft.stage = 2
    draft.expires_at = time.time() + 45.0

    card = (
        "🚨 <b>FINAL DOUBLE-CONFIRMATION (Stage 2 of 2)</b>\n\n"
        "<blockquote>"
        f"Are you <b>ABSOLUTELY SURE</b> you want to broadcast to <b>{draft.estimated_count} chats</b>?\n"
        "This action cannot be recalled once dispatched."
        "</blockquote>\n\n"
        "👉 Send <code>.send_broadcast</code> within 45s to START broadcast.\n"
        "👉 Send <code>.cancel</code> to abort."
    )
    await ctx.edit_raw(card, parse_mode="html")


@userbot_command("send_broadcast")
async def hosted_userbot_send_broadcast(ctx: RawContext) -> None:
    """Execute confirmed userbot broadcast."""
    draft = _PENDING_BROADCASTS.pop(ctx.client_id, None)
    if not draft or draft.stage != 2 or time.time() > draft.expires_at:
        await ctx.edit_raw("❌ Final broadcast confirmation window expired. Please initiate with <code>.broadcast</code> again.", parse_mode="html")
        return

    allowed, doc, _ = await check_client_access(ctx, feature="broadcast", owner_only=True)
    if not allowed or doc is None:
        return

    await ctx.edit_raw(
        f"🚀 <b>Broadcast Initiated!</b>\n"
        f"• Delivering to <code>{draft.scope}</code> chats...\n"
        f"• Send <code>.stop</code> at any time to abort.",
        parse_mode="html",
    )

    async def _worker() -> None:
        sent = 0
        failed = 0
        try:
            async for dlg in ctx.client.get_dialogs():
                if should_stop(ctx.client_id):
                    break
                chat = dlg.chat
                if chat is None:
                    continue
                if draft.scope == "private" and chat.type != enums.ChatType.PRIVATE:
                    continue
                if draft.scope == "groups" and chat.type not in (
                    enums.ChatType.GROUP,
                    enums.ChatType.SUPERGROUP,
                ):
                    continue
                if draft.scope == "channels" and chat.type != enums.ChatType.CHANNEL:
                    continue

                try:
                    if draft.reply_to_id:
                        await ctx.client.copy_message(chat.id, ctx.chat_id, draft.reply_to_id)
                    else:
                        await ctx.client.send_message(chat.id, draft.text)
                    sent += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except RPCError:
                    failed += 1
                except Exception:
                    failed += 1
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            pass
        finally:
            report = (
                f"📣 <b>Userbot Broadcast Finished ({draft.scope})</b>\n\n"
                f"• <b>Successfully Sent:</b> <code>{sent}</code>\n"
                f"• <b>Failed:</b> <code>{failed}</code>\n"
                f"• <b>Account:</b> <code>{ctx.client_id}</code>"
            )
            await ctx.client.send_message("me", report, parse_mode=enums.ParseMode.HTML)
            await log_to_client_logger(doc, report, mtproto_client=ctx.client)

    register_client_task(ctx.client_id, "ub_broadcast", _worker())
