""".forward plugin for hosted userbots."""

from __future__ import annotations

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import (
    check_client_access,
    parse_forward_args,
    run_forward_task,
)


@userbot_command("forward")
async def ub_forward(ctx: RawContext) -> None:
    allowed, doc, err = await check_client_access(ctx, feature="forward", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return

    if ctx.chat_type == "private":
        await ctx.edit_raw("Run .forward inside the destination group or channel.")
        return

    copy_mode = "-fwd" not in ctx.args.lower() and "-forward" not in ctx.args.lower()
    cleaned_args = ctx.args.replace("-fwd", "").replace("-forward", "").replace("-copy", "").strip()
    from_chat, start_id, end_id = parse_forward_args(cleaned_args)

    if from_chat is None or start_id is None or end_id is None:
        await ctx.edit_raw(
            "Usage: .forward <from_chat_id|t.me_link> <start_id> <end_id> [-fwd]"
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
