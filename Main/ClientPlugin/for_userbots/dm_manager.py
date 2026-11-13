"""Private chat manager (.dmclean, .dmblock, .dmmute, .dmunmute) & incoming DM Guard."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any

from pyrogram import Client, enums, raw

from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, log_to_client_logger

# Bounded in-memory warning counts: (client_id, sender_id) -> count
_DM_WARN_COUNTS: OrderedDict[tuple[str, int], int] = OrderedDict()
_MAX_WARN_ENTRIES = 10_000


async def _iterate_private_dialogs(cli: Client) -> list[int]:
    user_ids: list[int] = []
    async for dlg in cli.get_dialogs():
        if dlg.chat and dlg.chat.type == enums.ChatType.PRIVATE:
            if dlg.chat.id and dlg.chat.id != (cli.me.id if cli.me else 0):
                user_ids.append(dlg.chat.id)
    return user_ids


@userbot_command("dmclean")
async def ub_dm_clean(ctx: RawContext) -> None:
    """Request confirmation to delete all private DM chats from both sides (revoke=True)."""
    allowed, doc, err = await check_client_access(ctx, feature="dm_guard", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return

    async def _execute_dm_clean() -> None:
        targets = await _iterate_private_dialogs(ctx.client)
        cleaned = 0
        for uid in targets:
            try:
                peer = await ctx.client.resolve_peer(uid)
                await ctx.client.invoke(
                    raw.functions.messages.DeleteHistory(
                        peer=peer, max_id=0, just_clear=False, revoke=True
                    )
                )
                cleaned += 1
            except Exception:
                pass
            await asyncio.sleep(0.15)
        report = (
            f"🧹 <b>DM Clean Execution Finished</b>\n\n"
            f"• Successfully deleted <code>{cleaned}</code> / <code>{len(targets)}</code> private DM histories (both sides).\n"
            f"• Client: <code>{ctx.client_id}</code>"
        )
        await ctx.client.send_message("me", report, parse_mode=enums.ParseMode.HTML)
        await log_to_client_logger(doc, report, mtproto_client=ctx.client)

    from Main.utils.confirmation import confirmation_manager

    confirmation_manager.set_pending(
        client_id=ctx.client_id,
        user_id=ctx.sender_id,
        chat_id=ctx.chat_id,
        action_type="dmclean",
        description="Delete all private DM chat histories from both sides (irreversible)",
        handler=_execute_dm_clean,
        ttl=60.0,
    )

    confirm_msg = (
        "⚠️ <b>CONFIRMATION REQUIRED</b>\n\n"
        "<blockquote>"
        "You are about to irreversibly delete <b>all private DM chats from both sides</b> (<code>revoke=True</code>).\n\n"
        "• <b>Action:</b> <code>.dmclean</code>\n"
        "• <b>Scope:</b> Every 1-on-1 private conversation\n"
        "• <b>Impact:</b> Completely permanent & cannot be recovered."
        "</blockquote>\n\n"
        "👉 Send <code>.confirm</code> or <code>/confirm</code> within <b>60 seconds</b> to proceed.\n"
        "👉 Send <code>.cancel</code> or <code>/cancel</code> to abort."
    )
    await ctx.reply_raw(confirm_msg, reply=True, parse_mode="html")


@userbot_command("confirm")
async def ub_confirm_cmd(ctx: RawContext) -> None:
    """Confirm a pending destructive operation."""
    from Main.utils.confirmation import confirmation_manager

    pending = confirmation_manager.get_pending(ctx.client_id, ctx.sender_id)
    if not pending:
        await ctx.edit_raw("❌ No active operation pending confirmation, or the confirmation window expired.")
        return

    await ctx.edit_raw(f"⏳ Executing confirmed operation: <code>{pending.action_type}</code>...", parse_mode="html")
    ok, msg = await confirmation_manager.execute_confirmed(ctx.client_id, ctx.sender_id)
    if ok:
        await ctx.edit_raw(f"✅ {msg}")
    else:
        await ctx.edit_raw(f"⚠️ {msg}")


@userbot_command("cancel")
async def ub_cancel_cmd(ctx: RawContext) -> None:
    """Cancel a pending destructive operation."""
    from Main.utils.confirmation import confirmation_manager

    pending = confirmation_manager.cancel(ctx.client_id, ctx.sender_id)
    if pending:
        await ctx.edit_raw(f"❌ Cancelled pending operation: <code>{pending.action_type}</code>.", parse_mode="html")
    else:
        await ctx.edit_raw("No pending operation to cancel.")


@userbot_command("dmblock")
async def ub_dm_block(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="dm_guard", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await ctx.edit_raw("🚫 Blocking all private DM users...")
    targets = await _iterate_private_dialogs(ctx.client)
    blocked = 0
    for uid in targets:
        try:
            await ctx.client.block_user(uid)
            blocked += 1
        except Exception:
            pass
        await asyncio.sleep(0.15)
    await ctx.edit_raw(f"🚫 Blocked `{blocked}` / `{len(targets)}` DM users.")


async def _set_all_dm_mute(ctx: RawContext, mute: bool) -> None:
    allowed, _, err = await check_client_access(ctx, feature="dm_guard", owner_only=True)
    if not allowed:
        await ctx.edit_raw(err)
        return
    await ctx.edit_raw(f"{'🔇 Muting' if mute else '🔔 Unmuting'} all private DMs...")
    targets = await _iterate_private_dialogs(ctx.client)
    count = 0
    mute_until = 2**31 - 1 if mute else 0
    for uid in targets:
        try:
            peer = await ctx.client.resolve_peer(uid)
            await ctx.client.invoke(
                raw.functions.account.UpdateNotifySettings(
                    peer=raw.types.InputNotifyPeer(peer=peer),
                    settings=raw.types.InputPeerNotifySettings(
                        mute_until=mute_until,
                        silent=mute,
                    ),
                )
            )
            count += 1
        except Exception:
            pass
        await asyncio.sleep(0.1)
    await ctx.edit_raw(
        f"{'🔇 Muted' if mute else '🔔 Unmuted'} `{count}` / `{len(targets)}` private DMs."
    )


@userbot_command("dmmute")
async def ub_dm_mute(ctx: RawContext) -> None:
    await _set_all_dm_mute(ctx, True)


@userbot_command("dmunmute")
async def ub_dm_unmute(ctx: RawContext) -> None:
    await _set_all_dm_mute(ctx, False)


async def handle_incoming_dm_guard(
    cli: Client,
    client_id: str,
    sender_id: int,
    peer: raw.base.InputPeer,
) -> None:
    """Apply userbot's configured DM Guard rule on new incoming private messages."""
    doc = await bot_db.get_client(client_id)
    if doc is None:
        return
    features = doc.get("features") or {}
    if not features.get("dm_guard", True):
        return

    guard: dict[str, Any] = doc.get("dm_guard") or {}
    action = str(guard.get("action", "off")).lower()
    if action == "off":
        return

    sudo_users = set(doc.get("sudo_users") or [])
    if sender_id == doc.get("owner_id") or sender_id in sudo_users:
        return

    try:
        if action == "block":
            await cli.block_user(sender_id)
        elif action == "mute":
            await cli.invoke(
                raw.functions.account.UpdateNotifySettings(
                    peer=raw.types.InputNotifyPeer(peer=peer),
                    settings=raw.types.InputPeerNotifySettings(
                        mute_until=2**31 - 1, silent=True
                    ),
                )
            )
        elif action == "archive":
            await cli.invoke(
                raw.functions.folders.EditPeerFolders(
                    folder_peers=[
                        raw.types.InputFolderPeer(peer=peer, folder_id=1)
                    ]
                )
            )
        elif action == "warn":
            key = (client_id, sender_id)
            cnt = _DM_WARN_COUNTS.get(key, 0) + 1
            _DM_WARN_COUNTS[key] = cnt
            _DM_WARN_COUNTS.move_to_end(key)
            while len(_DM_WARN_COUNTS) > _MAX_WARN_ENTRIES:
                _DM_WARN_COUNTS.popitem(last=False)

            limit = max(1, int(guard.get("warn_limit", 3)))
            if cnt > limit:
                await cli.block_user(sender_id)
                _DM_WARN_COUNTS.pop(key, None)
            else:
                warn_text = (
                    guard.get("warn_text")
                    or "Please do not spam my DMs. Continued messages will result in a block."
                )
                await cli.send_message(
                    sender_id,
                    f"{warn_text}\n\n(Warning {cnt}/{limit})",
                )
    except Exception:
        pass
