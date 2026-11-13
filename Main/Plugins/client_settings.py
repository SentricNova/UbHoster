"""Per-client settings plugin for Main Bot (Welcome, Logger, Sudo, Features, DM Guard)."""

from __future__ import annotations

import html
import logging
from typing import Any

from pyrogram import Client, enums, filters, types
from pyrogram.errors import MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback
from Main.Client.Mongo import bot_db
from Main.utils.client_actions import ALL_FEATURES
from Main.utils.error_handler import safe_handler
from Main.utils.keyboard import (
    client_settings_keyboard,
    dm_guard_keyboard,
    feature_toggles_keyboard,
    sudo_manage_keyboard,
)

log = logging.getLogger(__name__)

FEATURE_LABELS: dict[str, str] = {
    "spam": "Spam",
    "tag": "Tag All",
    "moderation": "Moderation",
    "forward": "Forward",
    "raid": "Raid",
    "steal": "Auto Steal",
    "broadcast": "Broadcast",
    "scrape": "Scrape",
    "dm_guard": "DM Guard",
    "help": "Help",
    "info": "Info",
}


async def _get_owned_client(client_id: str, user_id: int) -> dict[str, Any] | None:
    doc = await bot_db.get_client(client_id)
    if not doc:
        return None
    try:
        doc_owner = int(doc.get("owner_id", 0))
    except (ValueError, TypeError):
        doc_owner = 0
    if doc_owner != int(user_id) and int(user_id) not in Config.OWNER_IDS:
        return None
    return doc


def _settings_summary(doc: dict[str, Any]) -> str:
    label = doc.get("username") or doc.get("name") or doc.get("client_id")
    ctype = doc.get("client_type", "bot")
    welcome = doc.get("welcome_text") or "(Default)"
    if len(welcome) > 60:
        welcome = welcome[:57] + "..."
    logger_id = doc.get("logger_group_id")
    logger_str = f"<code>{logger_id}</code>" if logger_id else "Not set"
    sudos = doc.get("sudo_users") or []
    features = doc.get("features") or {}
    enabled_cnt = sum(1 for k in ALL_FEATURES if features.get(k, True))
    lines = [
        f"⚙️ <b>Settings for {html.escape(str(label))}</b> (<code>{html.escape(str(ctype))}</code>)",
        "",
        f"• <b>Welcome Text:</b> <code>{html.escape(str(welcome))}</code>",
        f"• <b>Logger Group:</b> {logger_str}",
        f"• <b>Sudo Users:</b> <code>{len(sudos)}/{Config.MAX_SUDO_PER_CLIENT}</code>",
        f"• <b>Features Enabled:</b> <code>{enabled_cnt}/{len(ALL_FEATURES)}</code>",
    ]
    if ctype == "userbot":
        dmg = doc.get("dm_guard") or {}
        lines.append(
            f"• <b>DM Guard:</b> <code>{html.escape(str(dmg.get('action', 'off')))}</code> "
            f"(warn limit: <code>{dmg.get('warn_limit', 3)}</code>)"
        )
    return "\n".join(lines)


async def _safe_edit(
    cq: types.CallbackQuery,
    text: str,
    reply_markup: types.InlineKeyboardMarkup,
) -> None:
    if cq.message and cq.message.photo:
        try:
            await cq.message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass
    try:
        await cq.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML,
            link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        )
    except MessageNotModified:
        pass
    except Exception as exc:
        log.debug("edit_text in _safe_edit failed (%s), falling back to delete+send", exc)
        try:
            await cq.message.delete()
        except Exception:
            pass
        if cq.message and cq.message.chat:
            await cq.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )


@Client.on_callback_query(filters.regex(r"^cset:"))
@safe_handler("client_settings_cb")
async def client_settings_cb(client: Client, cq: types.CallbackQuery) -> None:
    user = cq.from_user
    if not user or not cq.message:
        return
    parts = cq.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    client_id = parts[2] if len(parts) > 2 else ""

    doc = await _get_owned_client(client_id, user.id)
    if not doc:
        await raw_answer_callback(client, cq.id, "Client not found or access denied.", alert=True)
        return

    ctype = str(doc.get("client_type") or "bot")

    if action == "menu":
        await bot_db.clear_state(user.id)
        await raw_answer_callback(client, cq.id)
        await _safe_edit(cq, _settings_summary(doc), client_settings_keyboard(client_id, ctype))
        return

    if action == "welcome":
        await raw_answer_callback(client, cq.id)
        await bot_db.set_state(
            user.id,
            {
                "step": "cset_welcome",
                "client_id": client_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.id,
            },
        )
        await _safe_edit(
            cq,
            "👋 **Set Custom Welcome Text**\n\n"
            "Send the new welcome message for `/start`.\n"
            "Available placeholders:\n"
            "`{fullname}`, `{firstname}`, `{mention}`, `{id}`, `{username}`, `{bot_name}`\n\n"
            "Send `default` to reset to the default welcome, or `/cancel` to abort.",
            client_settings_keyboard(client_id, ctype),
        )
        return

    if action == "logger":
        await raw_answer_callback(client, cq.id)
        await bot_db.set_state(
            user.id,
            {
                "step": "cset_logger",
                "client_id": client_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.id,
            },
        )
        await _safe_edit(
            cq,
            "📋 **Set Logger Group**\n\n"
            "Send the numeric Group/Channel ID (e.g. `-1001234567890`) where this client "
            "should send errors, warnings, and reports.\n"
            "Make sure the client is a member/admin in that group.\n\n"
            "Send `off` to disable, or `/cancel` to abort.",
            client_settings_keyboard(client_id, ctype),
        )
        return

    if action == "sudo":
        await bot_db.clear_state(user.id)
        await raw_answer_callback(client, cq.id)
        sudos = [int(x) for x in (doc.get("sudo_users") or [])]
        sudo_lines = "\n".join(f"• `{uid}`" for uid in sudos) if sudos else "_No sudo users configured._"
        await _safe_edit(
            cq,
            f"👥 **Sudo Users ({len(sudos)}/{Config.MAX_SUDO_PER_CLIENT})**\n\n"
            f"{sudo_lines}\n\n"
            "Note: A user must have started this main bot before they can be added as sudo.",
            sudo_manage_keyboard(client_id, sudos),
        )
        return

    if action == "sudoadd":
        sudos = [int(x) for x in (doc.get("sudo_users") or [])]
        if len(sudos) >= Config.MAX_SUDO_PER_CLIENT:
            await raw_answer_callback(
                client,
                cq.id,
                f"Maximum {Config.MAX_SUDO_PER_CLIENT} sudo users allowed.",
                alert=True,
            )
            return
        await raw_answer_callback(client, cq.id)
        await bot_db.set_state(
            user.id,
            {
                "step": "cset_sudoadd",
                "client_id": client_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.id,
            },
        )
        await _safe_edit(
            cq,
            "➕ **Add Sudo User**\n\n"
            "Send the numeric Telegram User ID of the person to add as sudo.\n"
            "They must have already started this Main Bot.\n\n"
            "Send `/cancel` to abort.",
            sudo_manage_keyboard(client_id, sudos),
        )
        return

    if action == "sudodel" and len(parts) > 3:
        try:
            target_uid = int(parts[3])
        except ValueError:
            await raw_answer_callback(client, cq.id, "Invalid user ID.", alert=True)
            return
        sudos = [int(x) for x in (doc.get("sudo_users") or []) if int(x) != target_uid]
        await bot_db.update_client_settings(client_id, {"sudo_users": sudos})
        await raw_answer_callback(client, cq.id, f"Removed {target_uid} from sudo.")
        sudo_lines = "\n".join(f"• `{uid}`" for uid in sudos) if sudos else "_No sudo users configured._"
        await _safe_edit(
            cq,
            f"👥 **Sudo Users ({len(sudos)}/{Config.MAX_SUDO_PER_CLIENT})**\n\n{sudo_lines}",
            sudo_manage_keyboard(client_id, sudos),
        )
        return

    if action == "feats":
        await raw_answer_callback(client, cq.id)
        features = dict(doc.get("features") or {})
        await _safe_edit(
            cq,
            "🧩 **Feature Toggles**\n\nTap a feature to turn it **ON** 🟢 or **OFF** 🔴 for this client.",
            feature_toggles_keyboard(client_id, features, FEATURE_LABELS, ctype),
        )
        return

    if action == "ftog" and len(parts) > 3:
        fkey = parts[3]
        if fkey not in ALL_FEATURES:
            await raw_answer_callback(client, cq.id, "Unknown feature.", alert=True)
            return
        features = dict(doc.get("features") or {})
        new_val = not bool(features.get(fkey, True))
        features[fkey] = new_val
        await bot_db.update_client_settings(client_id, {"features": features})
        if ctype == "bot" and doc.get("bot_token"):
            from Main.utils.client_actions import register_bot_commands
            asyncio.create_task(
                register_bot_commands(
                    doc["bot_token"],
                    features,
                    owner_id=doc.get("owner_id"),
                )
            )
        await raw_answer_callback(
            client,
            cq.id,
            f"{FEATURE_LABELS.get(fkey, fkey)}: {'ON' if new_val else 'OFF'}",
        )
        await _safe_edit(
            cq,
            "🧩 **Feature Toggles**\n\nTap a feature to turn it **ON** 🟢 or **OFF** 🔴 for this client.",
            feature_toggles_keyboard(client_id, features, FEATURE_LABELS, ctype),
        )
        return

    if action == "dmguard":
        if ctype != "userbot":
            await raw_answer_callback(client, cq.id, "DM Guard is only for userbots.", alert=True)
            return
        await raw_answer_callback(client, cq.id)
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        await _safe_edit(
            cq,
            "🛡 **DM Guard Settings**\n\n"
            f"• **Action:** `{dmg.get('action', 'off')}`\n"
            f"• **Warn Limit:** `{dmg.get('warn_limit', 3)}`\n"
            f"• **Warn Text:** `{dmg.get('warn_text') or '(Default)'}`",
            dm_guard_keyboard(client_id, dmg),
        )
        return

    if action == "dmgact" and len(parts) > 3:
        new_act = parts[3]
        if new_act not in ("off", "warn", "mute", "archive", "block"):
            await raw_answer_callback(client, cq.id, "Invalid DM Guard action.", alert=True)
            return
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        dmg["action"] = new_act
        await bot_db.update_client_settings(client_id, {"dm_guard": dmg})
        await raw_answer_callback(client, cq.id, f"DM Guard set to {new_act}")
        await _safe_edit(
            cq,
            "🛡 **DM Guard Settings**\n\n"
            f"• **Action:** `{dmg.get('action', 'off')}`\n"
            f"• **Warn Limit:** `{dmg.get('warn_limit', 3)}`\n"
            f"• **Warn Text:** `{dmg.get('warn_text') or '(Default)'}`",
            dm_guard_keyboard(client_id, dmg),
        )
        return

    if action == "dmglim":
        await raw_answer_callback(client, cq.id)
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        await bot_db.set_state(
            user.id,
            {
                "step": "cset_dmglim",
                "client_id": client_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.id,
            },
        )
        await _safe_edit(
            cq,
            "🔢 **Set DM Guard Warn Limit**\n\n"
            "Send a number between `1` and `50` (warnings before auto-block).\n"
            "Send `/cancel` to abort.",
            dm_guard_keyboard(client_id, dmg),
        )
        return

    if action == "dmgtxt":
        await raw_answer_callback(client, cq.id)
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        await bot_db.set_state(
            user.id,
            {
                "step": "cset_dmgtxt",
                "client_id": client_id,
                "chat_id": cq.message.chat.id,
                "message_id": cq.message.id,
            },
        )
        await _safe_edit(
            cq,
            "✏️ **Set DM Guard Warning Text**\n\n"
            "Send the custom warning message.\n"
            "Send `default` to reset, or `/cancel` to abort.",
            dm_guard_keyboard(client_id, dmg),
        )


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^/(?!cancel\b)"), group=3)
@safe_handler("client_settings_input")
async def client_settings_text_input(client: Client, message: types.Message) -> None:
    user = message.from_user
    if not user:
        return
    state = await bot_db.get_state(user.id)
    if not state:
        return
    step = str(state.get("step") or "")
    if not step.startswith("cset_"):
        return

    text = (message.text or "").strip()
    client_id = str(state.get("client_id") or "")
    doc = await _get_owned_client(client_id, user.id)
    if not doc:
        await bot_db.clear_state(user.id)
        return

    ctype = str(doc.get("client_type") or "bot")

    if text.lower() == "/cancel":
        await bot_db.clear_state(user.id)
        await message.reply_text(
            _settings_summary(doc),
            reply_markup=client_settings_keyboard(client_id, ctype),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if step == "cset_welcome":
        val = "" if text.lower() in ("default", "clear", "reset") else text
        await bot_db.update_client_settings(client_id, {"welcome_text": val})
        await bot_db.clear_state(user.id)
        doc["welcome_text"] = val
        await message.reply_text(
            "✅ **Welcome text updated!**\n\n" + _settings_summary(doc),
            reply_markup=client_settings_keyboard(client_id, ctype),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if step == "cset_logger":
        if text.lower() in ("off", "clear", "none", "0"):
            await bot_db.update_client_settings(client_id, {"logger_group_id": None})
            await bot_db.clear_state(user.id)
            doc["logger_group_id"] = None
            await message.reply_text(
                "✅ **Logger group disabled.**\n\n" + _settings_summary(doc),
                reply_markup=client_settings_keyboard(client_id, ctype),
                parse_mode=enums.ParseMode.HTML,
            )
            return
        try:
            gid = int(text)
        except ValueError:
            await message.reply_text("❌ Invalid Group ID. Send a numeric ID like `-1001234567890` or `off`.")
            return
        await bot_db.update_client_settings(client_id, {"logger_group_id": gid})
        await bot_db.clear_state(user.id)
        doc["logger_group_id"] = gid
        await message.reply_text(
            f"✅ **Logger group set to `{gid}`!**\n\n" + _settings_summary(doc),
            reply_markup=client_settings_keyboard(client_id, ctype),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if step == "cset_sudoadd":
        try:
            target_uid = int(text)
        except ValueError:
            await message.reply_text("❌ Invalid User ID. Send a numeric Telegram User ID or `/cancel`.")
            return
        target_user = await bot_db.get_user(target_uid)
        if not target_user:
            await message.reply_text(
                "❌ Invalid user id, only that person can be added as sudo user who has already started our main bot."
            )
            return
        sudos = [int(x) for x in (doc.get("sudo_users") or [])]
        if target_uid in sudos:
            await bot_db.clear_state(user.id)
            await message.reply_text(
                f"ℹ️ `{target_uid}` is already a sudo user.",
                reply_markup=sudo_manage_keyboard(client_id, sudos),
                parse_mode=enums.ParseMode.HTML,
            )
            return
        if len(sudos) >= Config.MAX_SUDO_PER_CLIENT:
            await bot_db.clear_state(user.id)
            await message.reply_text(
                f"❌ Maximum {Config.MAX_SUDO_PER_CLIENT} sudo users allowed.",
                reply_markup=sudo_manage_keyboard(client_id, sudos),
            )
            return
        sudos.append(target_uid)
        await bot_db.update_client_settings(client_id, {"sudo_users": sudos})
        await bot_db.clear_state(user.id)
        await message.reply_text(
            f"✅ Added `{target_uid}` to sudo users ({len(sudos)}/{Config.MAX_SUDO_PER_CLIENT}).",
            reply_markup=sudo_manage_keyboard(client_id, sudos),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if step == "cset_dmglim":
        try:
            lim = int(text)
        except ValueError:
            await message.reply_text("❌ Send a valid number between 1 and 50.")
            return
        lim = max(1, min(50, lim))
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        dmg["warn_limit"] = lim
        await bot_db.update_client_settings(client_id, {"dm_guard": dmg})
        await bot_db.clear_state(user.id)
        await message.reply_text(
            f"✅ DM Guard warn limit set to `{lim}`.",
            reply_markup=dm_guard_keyboard(client_id, dmg),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    if step == "cset_dmgtxt":
        val = "" if text.lower() in ("default", "clear", "reset") else text
        dmg = dict(doc.get("dm_guard") or {"action": "off", "warn_limit": 3, "warn_text": ""})
        dmg["warn_text"] = val
        await bot_db.update_client_settings(client_id, {"dm_guard": dmg})
        await bot_db.clear_state(user.id)
        await message.reply_text(
            "✅ DM Guard warning text updated.",
            reply_markup=dm_guard_keyboard(client_id, dmg),
        )
