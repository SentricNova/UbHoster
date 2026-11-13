"""Moderation plugins (.ban, .unban, .mute, .unmute, .kick, .banall, .unbanall, .kickall)."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, run_mass_moderation, run_moderation


async def _ub_mod(ctx: RawContext, action: str) -> None:
    allowed, doc, err = await check_client_access(
        ctx, feature="moderation", owner_only=True, allow_group_admin=True
    )
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_moderation(ctx, doc, action=action, args=ctx.args)


async def _ub_mass(ctx: RawContext, action: str) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="moderation", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_mass_moderation(ctx, doc, action=action, args=ctx.args)


@userbot_command("ban")
async def ub_ban(ctx: RawContext) -> None:
    await _ub_mod(ctx, "ban")


@userbot_command("unban")
async def ub_unban(ctx: RawContext) -> None:
    await _ub_mod(ctx, "unban")


@userbot_command("mute")
async def ub_mute(ctx: RawContext) -> None:
    await _ub_mod(ctx, "mute")


@userbot_command("unmute")
async def ub_unmute(ctx: RawContext) -> None:
    await _ub_mod(ctx, "unmute")


@userbot_command("kick")
async def ub_kick(ctx: RawContext) -> None:
    await _ub_mod(ctx, "kick")


@userbot_command("banall")
async def ub_banall(ctx: RawContext) -> None:
    await _ub_mass(ctx, "banall")


@userbot_command("unbanall")
async def ub_unbanall(ctx: RawContext) -> None:
    await _ub_mass(ctx, "unbanall")


@userbot_command("kickall")
async def ub_kickall(ctx: RawContext) -> None:
    await _ub_mass(ctx, "kickall")
