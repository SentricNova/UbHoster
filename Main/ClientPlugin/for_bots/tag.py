"""/tagall, /tagadmins, /tagusers, /tagactive commands for hosted bots."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import check_client_access, run_tag_task

BotCtx = Union[RawContext, BotWebhookContext]


@bot_command("tagall")
async def hosted_bot_tagall(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_tag_task(ctx, doc, mode="tagall", args=ctx.args)


@bot_command("tagadmins")
async def hosted_bot_tagadmins(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_tag_task(ctx, doc, mode="tagadmins", args=ctx.args)


@bot_command("tagusers")
async def hosted_bot_tagusers(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_tag_task(ctx, doc, mode="tagusers", args=ctx.args)


@bot_command("tagactive")
async def hosted_bot_tagactive(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_tag_task(ctx, doc, mode="tagactive", args=ctx.args)
