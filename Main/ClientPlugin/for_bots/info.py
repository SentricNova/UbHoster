"""/info plugin for hosted bots: returns the whole raw Telegram JSON for a replied message."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import check_client_access

BotCtx = Union[RawContext, BotWebhookContext]


@bot_command("info")
async def hosted_bot_info(ctx: BotCtx) -> None:
    """Return the full raw Telegram JSON of the replied message (or current update)."""
    allowed, doc, err = await check_client_access(ctx, feature="info", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return

    if not ctx.reply_to_msg_id and not ctx.args:
        await ctx.reply_raw(
            "Reply to any message with /info to inspect its full raw Telegram JSON.",
            reply=True,
        )
        return

    raw_json = await ctx.fetch_raw_json()
    target_id = ctx.reply_to_msg_id or ctx.msg_id
    await ctx.reply_raw_json(raw_json, filename=f"raw_msg_{target_id}.json")
