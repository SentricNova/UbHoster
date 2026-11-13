"""Core .ping, .stop, .leave plugins for hosted userbots using raw MTProto methods."""

from __future__ import annotations

import time

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, log_to_client_logger, stop_client_tasks
from Main.utils.error_handler import run_safe


@userbot_command("ping")
async def hosted_userbot_ping(ctx: RawContext) -> None:
    """Measure raw MTProto Ping latency and edit the outgoing .ping message."""
    ok, rtt_ms, err = await run_safe(ctx.ping_ms, context_label="ub_ping")
    if not ok and err:
        await ctx.edit_raw(f"⚠️ {err.short}: {err.description}")
        return
    uptime_sec = max(0, int(time.time() - ctx.stats.get("started_at", time.time())))
    await ctx.edit_raw(
        f"Pong! {rtt_ms:.2f} ms\n"
        f"Uptime: {uptime_sec}s | Updates: {ctx.stats.get('updates_handled', 0)}"
    )


@userbot_command("stop")
async def hosted_userbot_stop(ctx: RawContext) -> None:
    cnt = stop_client_tasks(ctx.client_id)
    await ctx.edit_raw(f"🛑 Stopped {cnt} active task(s)." if cnt else "No active tasks running.")


@userbot_command("leave")
async def hosted_userbot_leave(ctx: RawContext) -> None:
    allowed, doc, err_msg = await check_client_access(ctx, owner_only=True)
    if not allowed:
        await ctx.edit_raw(err_msg)
        return
    if ctx.chat_type == "private":
        await ctx.edit_raw("Cannot leave a private chat.")
        return

    await ctx.edit_raw("👋 Leaving chat...")
    ok, _, err = await run_safe(ctx.client.leave_chat, ctx.chat_id, context_label="ub_leave")
    if ok:
        await log_to_client_logger(doc, f"🚪 Userbot left chat `{ctx.chat_id}`.", mtproto_client=ctx.client)
    elif err:
        await ctx.client.send_message("me", f"⚠️ Leave failed: {err.description}")
