"""Owner-only /export (.export) plugin for hosted userbots."""

from __future__ import annotations

from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import RawContext, userbot_command


@userbot_command("export")
async def hosted_userbot_export(ctx: RawContext) -> None:
    """Export the hosted userbot's session string safely to Saved Messages ("me")."""
    try:
        session_str = await ctx.client.export_session_string()
    except Exception:
        doc = await bot_db.get_client(ctx.client_id)
        session_str = doc.get("session_string", "") if doc else ""

    if not session_str:
        await ctx.edit_raw("Could not export session string for this account.")
        return

    # Always remove the trigger command if run outside Saved Messages so nothing leaks
    try:
        me_id = ctx.client.me.id if ctx.client.me else 0
        if ctx.chat_id != me_id:
            await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
        else:
            await ctx.edit_raw("Exported session string below (Saved Messages).")
    except Exception:
        pass

    await ctx.client.send_message(
        "me",
        "🔑 **Exported Userbot Session String**\n"
        "Keep this secret! Anyone with this string can access your account.\n\n"
        f"<pre><code>{session_str}</code></pre>",
    )
