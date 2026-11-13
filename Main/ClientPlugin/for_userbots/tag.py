""".tagall, .tagadmins, .tagusers, .tagactive plugins for hosted userbots."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, run_tag_task


@userbot_command("tagall")
async def ub_tagall(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_tag_task(ctx, doc, mode="tagall", args=ctx.args)


@userbot_command("tagadmins")
async def ub_tagadmins(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_tag_task(ctx, doc, mode="tagadmins", args=ctx.args)


@userbot_command("tagusers")
async def ub_tagusers(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_tag_task(ctx, doc, mode="tagusers", args=ctx.args)


@userbot_command("tagactive")
async def ub_tagactive(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="tag", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    await run_tag_task(ctx, doc, mode="tagactive", args=ctx.args)
