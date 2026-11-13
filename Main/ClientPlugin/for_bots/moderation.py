"""Moderation commands (/ban, /unban, /mute, /unmute, /kick, /banall, /unbanall, /kickall)."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import check_client_access, run_mass_moderation, run_moderation

BotCtx = Union[RawContext, BotWebhookContext]


async def _admin_mod(ctx: BotCtx, action: str) -> None:
    allowed, doc, err = await check_client_access(
        ctx, feature="moderation", owner_only=True, allow_group_admin=True
    )
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_moderation(ctx, doc, action=action, args=ctx.args)


async def _owner_mass_mod(ctx: BotCtx, action: str) -> None:
    allowed, doc, err = await check_client_access(
        ctx, feature="moderation", owner_only=True, allow_group_admin=False
    )
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_mass_moderation(ctx, doc, action=action, args=ctx.args)


@bot_command("ban")
async def cmd_ban(ctx: BotCtx) -> None:
    await _admin_mod(ctx, "ban")


@bot_command("unban")
async def cmd_unban(ctx: BotCtx) -> None:
    await _admin_mod(ctx, "unban")


@bot_command("mute")
async def cmd_mute(ctx: BotCtx) -> None:
    await _admin_mod(ctx, "mute")


@bot_command("unmute")
async def cmd_unmute(ctx: BotCtx) -> None:
    await _admin_mod(ctx, "unmute")


@bot_command("kick")
async def cmd_kick(ctx: BotCtx) -> None:
    await _admin_mod(ctx, "kick")


@bot_command("banall")
async def cmd_banall(ctx: BotCtx) -> None:
    await _owner_mass_mod(ctx, "banall")


@bot_command("unbanall")
async def cmd_unbanall(ctx: BotCtx) -> None:
    await _owner_mass_mod(ctx, "unbanall")


@bot_command("kickall")
async def cmd_kickall(ctx: BotCtx) -> None:
    await _owner_mass_mod(ctx, "kickall")
