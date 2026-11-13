"""/forward plugin for hosted bots with Copy vs Forward mode & range extraction."""

from __future__ import annotations

from typing import Union

from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.client_actions import (
    check_client_access,
    parse_forward_args,
    run_forward_task,
)

BotCtx = Union[RawContext, BotWebhookContext]


@bot_command("forward")
async def hosted_bot_forward(ctx: BotCtx) -> None:
    """Copy or forward messages between chats into the current group/channel.

    Usage:
        /forward <from_chat_id|t.me_link> <start_id> <end_id> [-fwd]
    """
    allowed, doc, err = await check_client_access(ctx, feature="forward", owner_only=True)
    if not allowed or doc is None:
        await ctx.reply_raw(err, reply=True)
        return

    if getattr(ctx, "chat_type", "private") == "private":
        await ctx.reply_raw("Run /forward inside the destination group or channel.", reply=True)
        return

    copy_mode = "-fwd" not in ctx.args.lower() and "-forward" not in ctx.args.lower()
    cleaned_args = ctx.args.replace("-fwd", "").replace("-forward", "").replace("-copy", "").strip()
    from_chat, start_id, end_id = parse_forward_args(cleaned_args)

    if from_chat is None or start_id is None or end_id is None:
        await ctx.reply_raw(
            "Usage (run in destination group/channel):\n"
            "/forward <from_chat_id or t.me link> <start_msg_id> <end_msg_id>\n"
            "• Default mode: Copy (add -fwd to Forward instead)\n"
            "• Example: /forward -1001234567890 12 435\n"
            "• Send /stop anytime to cancel.",
            reply=True,
        )
        return

    await run_forward_task(
        ctx,
        doc,
        from_chat=from_chat,
        to_chat=ctx.chat_id,
        start_id=start_id,
        end_id=end_id,
        copy_mode=copy_mode,
    )
