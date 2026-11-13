""".spy, .unspy user activity monitoring plugins for hosted userbots."""

from __future__ import annotations

import html

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access, resolve_target_user
from Main.utils.spy_manager import (
    add_spy_target,
    get_user_spy_targets,
    is_user_spy_enabled,
    remove_spy_target,
    set_user_spy_enabled,
)


@userbot_command("spy")
async def ub_spy_cmd(ctx: RawContext) -> None:
    """Monitor a target user across mutual groups (.spy <id/@username/reply>, .spy on/off/list)."""
    allowed, _, err = await check_client_access(ctx, feature=None, owner_only=True)
    if not allowed:
        await ctx.edit_raw(err)
        return

    arg = ctx.args.strip()
    sub_cmd = arg.lower()

    if sub_cmd == "off":
        await set_user_spy_enabled(ctx.sender_id, False)
        await ctx.edit_raw("⏸ <b>Spy Monitoring:</b> Notifications are now <b>OFF</b>.", parse_mode="html")
        return

    if sub_cmd == "on":
        await set_user_spy_enabled(ctx.sender_id, True)
        await ctx.edit_raw("✅ <b>Spy Monitoring:</b> Notifications are now <b>ON</b>.", parse_mode="html")
        return

    if sub_cmd == "list":
        targets = await get_user_spy_targets(ctx.sender_id)
        if not targets:
            await ctx.edit_raw("📋 <b>Spy Monitoring List:</b> No targets currently monitored.", parse_mode="html")
            return
        status_str = "🟢 Active" if is_user_spy_enabled(ctx.sender_id) else "🔴 Paused (.spy on)"
        lines = [
            "📋 <b>Your Monitored Spy Targets</b>",
            f"• <b>Status:</b> {status_str}",
            f"• <b>Total Targets:</b> <code>{len(targets)}</code>",
            "",
        ]
        for tid in targets:
            lines.append(f"• <code>{tid}</code>")
        lines.append("\n💡 <i>Remove target with <code>.unspy &lt;id&gt;</code></i>")
        await ctx.edit_raw("\n".join(lines), parse_mode="html")
        return

    target_id, target_label = await resolve_target_user(ctx, arg)
    if not target_id:
        await ctx.edit_raw(
            "⚠️ <b>Usage:</b>\n"
            "• <code>.spy &lt;user_id | @username | username&gt;</code>\n"
            "• Reply to any message with <code>.spy</code>\n"
            "• <code>.spy on</code> / <code>.spy off</code> / <code>.spy list</code>",
            parse_mode="html",
        )
        return

    if target_id == ctx.sender_id:
        await ctx.edit_raw("Cannot spy on your own account.", parse_mode="html")
        return

    ok = await add_spy_target(ctx.sender_id, target_id)
    if ok:
        lbl = html.escape(str(target_label or target_id))
        await ctx.edit_raw(
            f"👁️ <b>Spy Monitoring Activated</b>\n\n"
            f"<blockquote>"
            f"• <b>Target:</b> {lbl}\n"
            f"• <b>Target ID:</b> <code>{target_id}</code>\n"
            f"• <b>Notification:</b> Saved Messages (\"me\") & Logger\n"
            f"• <b>Cooldown:</b> 1 notification per chat per 24h"
            f"</blockquote>\n\n"
            f"<i>You will be alerted whenever this user is spotted in any mutual group.</i>",
            parse_mode="html",
        )
    else:
        await ctx.edit_raw(f"⚠️ Failed registering spy target `{target_id}`.")


@userbot_command("unspy")
async def ub_unspy_cmd(ctx: RawContext) -> None:
    """Stop monitoring a target user (.unspy <id/@username/reply>)."""
    allowed, _, err = await check_client_access(ctx, feature=None, owner_only=True)
    if not allowed:
        await ctx.edit_raw(err)
        return

    target_id, target_label = await resolve_target_user(ctx, ctx.args.strip())
    if not target_id:
        await ctx.edit_raw("⚠️ Reply to a user or pass a user ID/username to <code>.unspy</code>.", parse_mode="html")
        return

    ok = await remove_spy_target(ctx.sender_id, target_id)
    if ok:
        lbl = html.escape(str(target_label or target_id))
        await ctx.edit_raw(f"✅ Removed {lbl} (<code>{target_id}</code>) from spy monitor.", parse_mode="html")
    else:
        await ctx.edit_raw(f"⚠️ Failed removing spy target `{target_id}`.")
