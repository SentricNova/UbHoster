"""/spam, /fspam, /tspam commands for hosted bots."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import check_client_access, run_spam_task

BotCtx = Union[RawContext, BotWebhookContext]


@bot_command("spam")
async def hosted_bot_spam(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_spam_task(ctx, doc, mode="spam", args=ctx.args)


@bot_command("fspam")
async def hosted_bot_fspam(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_spam_task(ctx, doc, mode="fspam", args=ctx.args)


@bot_command("tspam")
async def hosted_bot_tspam(ctx: BotCtx) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return
    await run_spam_task(ctx, doc, mode="tspam", args=ctx.args)
