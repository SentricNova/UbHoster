"""/raid, /fraid, /unraid commands for hosted bots (target ID locked in RAM)."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import (
    _resolve_target_user_id,
    check_client_access,
    remove_raid_target,
    set_raid_target,
)

BotCtx = Union[RawContext, BotWebhookContext]


async def _handle_raid(ctx: BotCtx, mode: str) -> None:
    allowed, _, err = await check_client_access(ctx, feature="raid", owner_only=True)
    if not allowed:
        await ctx.reply_raw(err, reply=True)
        return
    target_id = await _resolve_target_user_id(ctx, ctx.args)
    if not target_id:
        await ctx.reply_raw(f"Reply to a user or pass a user ID to /{mode}.", reply=True)
        return
    set_raid_target(ctx.client_id, target_id, mode)
    label = "Flirt Raid" if mode == "fraid" else "Raid"
    await ctx.reply_raw(f"🎯 {label} locked onto `{target_id}` in RAM.", reply=True)


@bot_command("raid")
async def cmd_raid(ctx: BotCtx) -> None:
    await _handle_raid(ctx, "raid")


@bot_command("fraid")
async def cmd_fraid(ctx: BotCtx) -> None:
    await _handle_raid(ctx, "fraid")


@bot_command("unraid")
async def cmd_unraid(ctx: BotCtx) -> None:
    allowed, _, err = await check_client_access(ctx, feature="raid", owner_only=True)
    if not allowed:
        await ctx.reply_raw(err, reply=True)
        return
    target_id = await _resolve_target_user_id(ctx, ctx.args)
    removed = remove_raid_target(ctx.client_id, target_id)
    if target_id:
        await ctx.reply_raw(
            f"✅ Unlocked `{target_id}` from raid." if removed else f"`{target_id}` was not raided.",
            reply=True,
        )
    else:
        await ctx.reply_raw(f"✅ Cleared `{removed}` raid target(s).", reply=True)
