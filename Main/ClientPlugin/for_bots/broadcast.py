"""Owner-only /broadcast plugin for hosted bots (supports Webhook & Raw MTProto)."""

from __future__ import annotations

from typing import Union

from config import Config
from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import BotWebhookContext, RawContext, bot_command
from Main.utils.peer_tracker import broadcast_to_client_audience

BotCtx = Union[RawContext, BotWebhookContext]


def _parse_broadcast_args(raw_args: str) -> tuple[str | None, str]:
    parts = raw_args.strip().split(None, 1)
    if not parts:
        return None, ""
    flag = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    mapping = {
        "-users": "user",
        "-user": "user",
        "-groups": "group",
        "-group": "group",
        "-channels": "channel",
        "-channel": "channel",
        "-all": None,
    }
    if flag in mapping:
        return mapping[flag], rest.strip()
    return None, raw_args.strip()


@bot_command("broadcast")
async def hosted_bot_broadcast(ctx: BotCtx) -> None:
    """Allow the hosted bot's owner to broadcast a message to tracked users/groups/channels with confirmation."""
    if ctx.sender_id != ctx.owner_id and ctx.sender_id not in Config.OWNER_IDS:
        await ctx.reply_raw("Only the owner of this bot can use /broadcast.", reply=True)
        return

    raw_args = ctx.args.strip()
    is_confirmed = "-confirm" in raw_args.split()
    clean_args = raw_args.replace("-confirm", "").strip()

    peer_type, message_text = _parse_broadcast_args(clean_args)
    if not message_text:
        await ctx.reply_raw(
            "⚠️ <b>Hosted Bot Broadcast Usage:</b>\n\n"
            "• <code>/broadcast &lt;message&gt;</code> — Send to all users, groups & channels\n"
            "• <code>/broadcast -users &lt;message&gt;</code> — Users only\n"
            "• <code>/broadcast -groups &lt;message&gt;</code> — Groups only\n"
            "• <code>/broadcast -channels &lt;message&gt;</code> — Channels only",
            reply=True,
            parse_mode="html",
        )
        return

    doc = await bot_db.get_client(ctx.client_id)
    if doc is None:
        await ctx.reply_raw("Bot configuration not found.", reply=True)
        return

    from Main.utils.peer_tracker import peer_tracker
    await peer_tracker.flush()
    counts = await bot_db.get_client_peer_counts(ctx.client_id)
    target_cnt = counts.get(peer_type or "total", 0)

    if not is_confirmed:
        target_name = "All Users, Groups & Channels" if not peer_type else f"{peer_type.title()}s"
        snippet = message_text[:120] + ("..." if len(message_text) > 120 else "")
        prompt = (
            "⚠️ <b>BROADCAST SAFETY CONFIRMATION</b>\n\n"
            "<blockquote>"
            f"• <b>Target Audience:</b> {target_name}\n"
            f"• <b>Recipient Count:</b> <code>{target_cnt}</code> chats\n"
            f"• <b>Message Preview:</b> <i>{snippet}</i>"
            "</blockquote>\n\n"
            f"👉 To dispatch this broadcast, send:\n"
            f"<code>/broadcast -confirm {raw_args}</code>\n\n"
            "<i>(Messages cannot be recalled once sent)</i>"
        )
        await ctx.reply_raw(prompt, reply=True, parse_mode="html")
        return

    await ctx.reply_raw("📣 Broadcasting message... please wait.", reply=True)
    res = await broadcast_to_client_audience(
        client_id=ctx.client_id,
        client_doc=doc,
        peer_type=peer_type,
        text=message_text,
        mtproto_client=ctx.client if isinstance(ctx, RawContext) else None,
    )
    await ctx.reply_raw(
        f"✅ <b>Broadcast Complete</b> ({res.elapsed_seconds}s)\n\n"
        f"• Total: <code>{res.total}</code> | Sent: <code>{res.sent}</code> | Failed: <code>{res.failed}</code> | Pruned: <code>{res.pruned}</code>",
        reply=False,
        parse_mode="html",
    )
