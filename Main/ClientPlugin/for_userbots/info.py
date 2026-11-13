"""/info (.info) plugin for hosted userbots: returns the whole raw Telegram MTProto JSON."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access


@userbot_command("info")
async def hosted_userbot_info(ctx: RawContext) -> None:
    """Return the full raw Telegram MTProto JSON of the replied message (or current update)."""
    allowed, doc, err = await check_client_access(ctx, feature="info", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return

    if not ctx.reply_to_msg_id and not ctx.args:
        await ctx.edit_raw("Reply to any message with `/info` or `.info` to view its raw Telegram JSON.")
        return

    raw_json = await ctx.fetch_raw_json()
    target_id = ctx.reply_to_msg_id or ctx.msg_id
    await ctx.reply_raw_json(
        raw_json,
        filename=f"raw_msg_{target_id}.json",
        edit_for_userbot=True,
    )
