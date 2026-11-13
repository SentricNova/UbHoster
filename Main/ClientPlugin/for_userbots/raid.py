""".raid, .fraid, .unraid plugins for hosted userbots (RAM-locked)."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import (
    _resolve_target_user_id,
    check_client_access,
    remove_raid_target,
    set_raid_target,
)


async def _ub_raid(ctx: RawContext, mode: str) -> None:
    allowed, _, err = await check_client_access(ctx, feature="raid", owner_only=True)
    if not allowed:
        await ctx.edit_raw(err)
        return
    target_id = await _resolve_target_user_id(ctx, ctx.args)
    if not target_id:
        await ctx.edit_raw(f"Reply to a user or pass a user ID to .{mode}.")
        return
    set_raid_target(ctx.client_id, target_id, mode)
    await ctx.edit_raw(f"🎯 {mode.upper()} locked onto `{target_id}` in RAM.")


@userbot_command("raid")
async def ub_raid(ctx: RawContext) -> None:
    await _ub_raid(ctx, "raid")


@userbot_command("fraid")
async def ub_fraid(ctx: RawContext) -> None:
    await _ub_raid(ctx, "fraid")


@userbot_command("unraid")
async def ub_unraid(ctx: RawContext) -> None:
    allowed, _, err = await check_client_access(ctx, feature="raid", owner_only=True)
    if not allowed:
        await ctx.edit_raw(err)
        return
    target_id = await _resolve_target_user_id(ctx, ctx.args)
    removed = remove_raid_target(ctx.client_id, target_id)
    await ctx.edit_raw(f"✅ Cleared {removed} raid target(s).")
