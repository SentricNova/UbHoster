""".spam, .fspam, .tspam plugins for hosted userbots."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, run_spam_task


@userbot_command("spam")
async def ub_spam(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_spam_task(ctx, doc, mode="spam", args=ctx.args)


@userbot_command("fspam")
async def ub_fspam(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_spam_task(ctx, doc, mode="fspam", args=ctx.args)


@userbot_command("tspam")
async def ub_tspam(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="spam", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_spam_task(ctx, doc, mode="tspam", args=ctx.args)
