"""Interactive /addbot and /addacc onboarding flows with RichMessage Table Keypad."""

from __future__ import annotations

import asyncio
import html
import logging
import re

from pyrogram import Client, enums, filters, types
from pyrogram.errors import MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback
from Main.Client.Mongo import bot_db
from Main.utils.client_manager import AuthWizardSession, client_manager
from Main.utils.keyboard import (
    addacc_api_creds_keyboard,
    addacc_method_keyboard,
    addacc_session_keyboard,
    addbot_wizard_keyboard,
    cancel_2fa_keyboard,
    keypad_keyboard,
    main_menu_keyboard,
)
from Main.utils.rich_ui import (
    build_addacc_api_rich_message,
    build_addacc_method_rich_message,
    build_addacc_session_rich_message,
    build_addbot_rich_message,
    build_keypad_rich_message,
)

log = logging.getLogger(__name__)

_BOT_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{25,}$")
_ALLOWED_KEYPAD_CHARS = frozenset("0123456789+")
_USER_WIZARD_LOCKS: dict[int, asyncio.Lock] = {}


def _user_lock(user_id: int) -> asyncio.Lock:
    if len(_USER_WIZARD_LOCKS) > 5000:
        for uid, lk in list(_USER_WIZARD_LOCKS.items()):
            if uid != user_id and not lk.locked():
                _USER_WIZARD_LOCKS.pop(uid, None)
    lock = _USER_WIZARD_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _USER_WIZARD_LOCKS[user_id] = lock
    return lock


def _get_addbot_caption(error_text: str | None = None) -> str:
    text = (
        "🤖 <b>Add a Telegram Bot (Step-by-Step)</b>\n\n"
        "1. Open @BotFather on Telegram and send <code>/newbot</code> (or <code>/mybots</code> -> API Token).\n"
        "2. Copy your Bot API Token (format: <code>123456789:AAHxxxxxxxxxxxxxxxx</code>).\n"
        "3. Send the token here as your next message — it will be encrypted and verified instantly.\n\n"
        "💡 <i>Hosted bots run on raw MTProto handlers with /start, /ping, /stats, and /help.</i>"
    )
    if error_text:
        text += f"\n\n⚠️ <b>Error:</b> <i>{html.escape(error_text)}</i>\n<i>Please send a valid Bot Token or tap Cancel.</i>"
    return text


def _get_addacc_method_caption() -> str:
    return (
        "📱 <b>Connect a Userbot Account</b>\n\n"
        "Select your preferred authentication method:\n\n"
        "• <b>Direct OTP Login (Recommended)</b>: Instant on-screen keypad phone & OTP authorization.\n"
        "• <b>Pyrogram Session String</b>: Connect via an exported Kurigram / Pyrogram string.\n\n"
        "<i>Choose an option below:</i>"
    )


def _get_addacc_session_caption(error_text: str | None = None) -> str:
    text = (
        "🔐 <b>Paste Your Session String</b>\n\n"
        "Send your Kurigram or Pyrogram session string as your next message.\n\n"
        "<i>Your message will be deleted immediately, encrypted, and saved to MongoDB.</i>"
    )
    if error_text:
        text += f"\n\n⚠️ <b>Error:</b> <i>{html.escape(error_text)}</i>\n<i>Send a valid session string or tap Cancel.</i>"
    return text


def _get_addacc_api_caption(error_text: str | None = None) -> str:
    text = (
        "🔐 <b>Enter Custom Telegram API Credentials</b>\n\n"
        "Send your custom <code>API_ID API_HASH</code> separated by space/newline as your next message, or tap Skip to use default credentials."
    )
    if error_text:
        text += f"\n\n⚠️ <b>Error:</b> <i>{html.escape(error_text)}</i>"
    return text


def _get_keypad_caption(wizard: AuthWizardSession) -> str:
    if wizard.stage == "phone":
        t = (
            "📱 <b>Step 1/2 — Enter Phone Number (Secure Keypad)</b>\n\n"
            "Use the on-screen secure keypad buttons below to enter your phone number with country code:\n\n"
            f"<b>Input:</b> <code>{wizard.buffer or '+...'}</code>"
        )
    else:
        hint = wizard.phone_number or "your Telegram app"
        t = (
            "🔑 <b>Step 2/2 — Enter Telegram OTP (Secure Keypad)</b>\n\n"
            f"An official login code was sent to {hint}.\n"
            "Use ONLY the keypad buttons below to enter the digits:\n\n"
            f"<b>OTP Code:</b> <code>{wizard.buffer or '•••••'}</code>"
        )
    if wizard.error_text:
        t += f"\n\n⚠️ <b>Error:</b> <i>{html.escape(wizard.error_text)}</i>"
    return t


def _get_success_caption(doc: dict, status_msg: str) -> str:
    kind = "Telegram Bot" if doc.get("client_type") == "bot" else "Userbot Account"
    uname = f"@{doc['username']}" if doc.get("username") else f"ID: {doc.get('tg_id', 'N/A')}"
    return (
        f"✅ <b>{kind} Connected Successfully!</b>\n\n"
        f"• <b>Name:</b> {html.escape(str(doc.get('name') or 'N/A'))}\n"
        f"• <b>Username:</b> {uname}\n"
        f"• <b>Client ID:</b> <code>{doc.get('client_id')}</code>\n"
        f"• <b>Status:</b> 🟢 <code>Active & Running</code>\n\n"
        f"<i>{html.escape(status_msg)}</i>"
    )


async def _safe_edit_rich(
    client: Client,
    chat_id: int,
    message_id: int | None,
    rich_message: types.InputRichMessage,
    reply_markup: types.InlineKeyboardMarkup,
    caption_text: str | None = None,
) -> int:
    if message_id is not None:
        # 1. If caption_text is provided, attempt edit_message_caption first (for media/photo messages)
        if caption_text:
            try:
                await client.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=caption_text,
                    reply_markup=reply_markup,
                    parse_mode=enums.ParseMode.HTML,
                )
                return message_id
            except MessageNotModified:
                return message_id
            except Exception:
                pass

        # 2. Try edit_message_text for standard text messages
        try:
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                rich_message=rich_message,
                reply_markup=reply_markup,
            )
            return message_id
        except MessageNotModified:
            return message_id
        except Exception as exc:
            log.debug("edit_message_text rich failed (%s), fallback to delete+send", exc)
            try:
                await client.delete_messages(chat_id, message_id)
            except Exception:
                pass

    sent = await client.send_rich_message(
        chat_id=chat_id,
        rich_message=rich_message,
        reply_markup=reply_markup,
    )
    return sent.id if sent else (message_id or 0)


async def _open_addacc_wizard(client: Client, user: types.User, chat_id: int, edit_msg_id: int | None = None) -> None:
    await bot_db.ensure_user(user.id, user.username, user.first_name)
    wizard = await client_manager.start_wizard(
        user_id=user.id,
        chat_id=chat_id,
        flow="addacc",
        stage="acc_method",
    )
    msg_id = await _safe_edit_rich(
        client,
        chat_id,
        edit_msg_id,
        build_addacc_method_rich_message(),
        addacc_method_keyboard(),
        caption_text=_get_addacc_method_caption(),
    )
    wizard.message_id = msg_id


async def _proceed_addacc_login_stage(client: Client, wizard: AuthWizardSession) -> None:
    if wizard.login_method == "session":
        wizard.stage = "session_str"
        wizard.message_id = await _safe_edit_rich(
            client,
            wizard.chat_id,
            wizard.message_id,
            build_addacc_session_rich_message(),
            addacc_session_keyboard(),
            caption_text=_get_addacc_session_caption(),
        )
    else:
        wizard.stage = "phone"
        wizard.buffer = ""
        wizard.error_text = None
        wizard.message_id = await _safe_edit_rich(
            client,
            wizard.chat_id,
            wizard.message_id,
            build_keypad_rich_message(mode="phone", value=""),
            keypad_keyboard("phone"),
            caption_text=_get_keypad_caption(wizard),
        )


async def _send_success_card(
    client: Client, wizard: AuthWizardSession, doc: dict, status_msg: str
) -> None:
    await _safe_edit_rich(
        client,
        wizard.chat_id,
        wizard.message_id,
        _success_rich_message(doc, status_msg),
        types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton(
                        "📊 View Client Stats",
                        callback_data=f"stats:view:{doc['client_id']}",
                        style=enums.ButtonStyle.SUCCESS,
                    )
                ],
                [types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
            ]
        ),
        caption_text=_get_success_caption(doc, status_msg),
    )


async def _open_addbot_wizard(client: Client, user: types.User, chat_id: int, edit_msg_id: int | None = None) -> None:
    await bot_db.ensure_user(user.id, user.username, user.first_name)
    wizard = await client_manager.start_wizard(
        user_id=user.id,
        chat_id=chat_id,
        flow="addbot",
        stage="bot_token",
    )
    msg_id = await _safe_edit_rich(
        client,
        chat_id,
        edit_msg_id,
        build_addbot_rich_message(),
        addbot_wizard_keyboard(),
        caption_text=_get_addbot_caption(),
    )
    wizard.message_id = msg_id


async def _is_blocked_by_maintenance(client: Client, user_id: int, chat_id: int) -> bool:
    if user_id in Config.OWNER_IDS:
        return False
    maint = await bot_db.get_maintenance()
    if maint.get("enabled"):
        reason = maint.get("reason") or "Bot is currently under maintenance."
        await client.send_message(chat_id, f"🛠 **Maintenance Mode**\n\n{reason}")
        return True
    return False


async def _is_blocked_by_affiliate(client: Client, user_id: int, chat_id: int) -> bool:
    if user_id in Config.OWNER_IDS:
        return False
    info = await bot_db.get_user_referral_info(user_id)
    if not info.get("enabled"):
        return False
    if info.get("unlocked"):
        return False

    import urllib.parse

    req = info.get("required_invites", 3)
    cnt = info.get("invited_count", 0)
    rem = max(1, req - cnt)
    bot_uname = (client.me.username if client.me else "") or "PyroTesting_Bot"
    ref_link = f"https://t.me/{bot_uname}?start=ref_{user_id}"
    share_text = f"Host your Telegram Bots & Userbots with ultra-low RAM! Join here: {ref_link}"
    share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link, safe='')}&text={urllib.parse.quote(share_text, safe='')}"

    await client.send_message(
        chat_id,
        (
            f"🔒 **Hosting Locked by Affiliate Program**\n\n"
            f"Our affiliate program is currently active! To unlock full access to bot and userbot hosting, "
            f"please invite **{rem} more user(s)** using your personal invite link.\n\n"
            f"📊 **Progress:** `{cnt} / {req}` invites\n"
            f"🔗 **Your Referral Link:**\n`{ref_link}`"
        ),
        reply_markup=types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton(
                        "📤 Share Invite Link",
                        url=share_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        "🎁 Check Status",
                        callback_data="menu:affiliate",
                    ),
                    types.InlineKeyboardButton(
                        "🏠 Main Menu",
                        callback_data="menu:home",
                    ),
                ],
            ]
        ),
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )
    return True


@Client.on_message(filters.command("addacc") & filters.private)
async def addacc_command(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    await bot_db.touch_owner_activity(message.from_user.id)
    if await _is_blocked_by_maintenance(client, message.from_user.id, message.chat.id):
        return
    if await _is_blocked_by_affiliate(client, message.from_user.id, message.chat.id):
        return
    await _open_addacc_wizard(client, message.from_user, message.chat.id)


@Client.on_message(filters.command("addbot") & filters.private)
async def addbot_command(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    await bot_db.touch_owner_activity(message.from_user.id)
    if await _is_blocked_by_maintenance(client, message.from_user.id, message.chat.id):
        return
    if await _is_blocked_by_affiliate(client, message.from_user.id, message.chat.id):
        return
    await _open_addbot_wizard(client, message.from_user, message.chat.id)


@Client.on_callback_query(filters.regex(r"^menu:(addacc|addbot)$"))
async def menu_add_callbacks(client: Client, query: types.CallbackQuery) -> None:
    await raw_answer_callback(client, query.id)
    await bot_db.touch_owner_activity(query.from_user.id)
    if await _is_blocked_by_maintenance(client, query.from_user.id, query.message.chat.id):
        return
    if await _is_blocked_by_affiliate(client, query.from_user.id, query.message.chat.id):
        return
    action = query.data.split(":", 1)[1]
    if action == "addacc":
        await _open_addacc_wizard(
            client,
            query.from_user,
            query.message.chat.id,
            edit_msg_id=query.message.id,
        )
    else:
        await _open_addbot_wizard(
            client,
            query.from_user,
            query.message.chat.id,
            edit_msg_id=query.message.id,
        )


@Client.on_callback_query(filters.regex(r"^addacc:(mode:(phone|session)|skip_api)$"))
async def addacc_setup_callbacks(client: Client, query: types.CallbackQuery) -> None:
    if query.from_user is None or query.message is None:
        return
    user_id = query.from_user.id
    async with _user_lock(user_id):
        wizard = client_manager.get_wizard(user_id)
        if wizard is None or wizard.flow != "addacc":
            await raw_answer_callback(
                client,
                query.id,
                "Session expired. Send /addacc to start again.",
                alert=True,
            )
            return

        wizard.message_id = query.message.id
        wizard.chat_id = query.message.chat.id
        data = query.data

        if data.startswith("addacc:mode:"):
            method = data.split(":", 2)[2]
            wizard.login_method = method
            wizard.stage = "api_creds"
            wizard.error_text = None
            await raw_answer_callback(client, query.id)
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addacc_api_rich_message(login_method=method),
                addacc_api_creds_keyboard(),
                caption_text=_get_addacc_api_caption(),
            )
            return

        if data == "addacc:skip_api":
            wizard.custom_api_id = None
            wizard.custom_api_hash = None
            await raw_answer_callback(client, query.id, "Using default API")
            await _proceed_addacc_login_stage(client, wizard)
            return


@Client.on_callback_query(filters.regex(r"^addbot:cancel$"))
async def addbot_cancel_callback(client: Client, query: types.CallbackQuery) -> None:
    await client_manager.cancel_wizard(query.from_user.id)
    await raw_answer_callback(client, query.id, "Cancelled")
    if query.message and query.message.photo:
        try:
            await client.edit_message_caption(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                caption="❌ <b>Bot setup cancelled.</b>",
                reply_markup=main_menu_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text="❌ Bot setup cancelled.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        pass


async def _refresh_keypad_ui(client: Client, wizard: AuthWizardSession) -> None:
    rich_msg = build_keypad_rich_message(
        mode=wizard.stage,
        value=wizard.buffer,
        error_text=wizard.error_text,
        phone_hint=wizard.phone_number,
    )
    wizard.message_id = await _safe_edit_rich(
        client,
        wizard.chat_id,
        wizard.message_id,
        rich_msg,
        keypad_keyboard(wizard.stage),
        caption_text=_get_keypad_caption(wizard),
    )


def _success_rich_message(doc: dict, status_msg: str) -> types.InputRichMessage:
    kind = "Telegram Bot" if doc.get("client_type") == "bot" else "Userbot Account"
    uname = f"@{doc['username']}" if doc.get("username") else "N/A"
    cmd_hint = "/start or /ping" if doc.get("client_type") == "bot" else ".ping"
    return types.InputRichMessage(
        blocks=[
            types.InputRichBlockSectionHeading(
                text=types.RichTextBold(f"✅ {kind} Connected!"),
                size=3,
            ),
            types.InputRichBlockTable(
                cells=[
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Name"), is_header=True),
                        types.RichBlockTableCell(text=doc.get("name", "Client")),
                    ],
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Username"), is_header=True),
                        types.RichBlockTableCell(text=uname),
                    ],
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Telegram ID"), is_header=True),
                        types.RichBlockTableCell(text=types.RichTextCode(str(doc.get("tg_id")))),
                    ],
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Status"), is_header=True),
                        types.RichBlockTableCell(text=status_msg),
                    ],
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Test Command"), is_header=True),
                        types.RichBlockTableCell(text=types.RichTextCode(cmd_hint)),
                    ],
                ],
                is_bordered=True,
                is_striped=True,
                is_compact=True,
            ),
        ]
    )


@Client.on_callback_query(filters.regex(r"^kp:"))
async def keypad_callbacks(client: Client, query: types.CallbackQuery) -> None:
    if query.from_user is None or query.message is None:
        return
    user_id = query.from_user.id
    data = query.data

    async with _user_lock(user_id):
        wizard = client_manager.get_wizard(user_id)

        if data == "kp:cancel":
            await client_manager.cancel_wizard(user_id)
            await raw_answer_callback(client, query.id, "Login cancelled")
            try:
                await client.edit_message_text(
                    chat_id=query.message.chat.id,
                    message_id=query.message.id,
                    text="❌ Account login cancelled.",
                    reply_markup=main_menu_keyboard(),
                )
            except MessageNotModified:
                pass
            return

        if wizard is None or wizard.flow != "addacc":
            await raw_answer_callback(
                client,
                query.id,
                "Session expired. Send /addacc to start again.",
                alert=True,
            )
            return

        wizard.message_id = query.message.id
        wizard.chat_id = query.message.chat.id

        if data.startswith("kp:cell:"):
            await raw_answer_callback(client, query.id, "Use the keypad buttons below")
            return

        if wizard.stage not in ("phone", "otp"):
            await raw_answer_callback(
                client, query.id, "Waiting for 2FA password or completion."
            )
            return

        if data.startswith("kp:d:"):
            char = data.split(":", 2)[2]
            if char not in _ALLOWED_KEYPAD_CHARS:
                await raw_answer_callback(client, query.id, "Invalid key")
                return
            if char == "+":
                if wizard.stage != "phone" or wizard.buffer:
                    await raw_answer_callback(
                        client, query.id, "'+' can only be at the start"
                    )
                    return
                wizard.buffer = "+"
            else:
                limit = 16 if wizard.stage == "phone" else 8
                if len(wizard.buffer) >= limit:
                    await raw_answer_callback(
                        client, query.id, f"Max {limit} characters reached"
                    )
                    return
                if wizard.stage == "phone" and not wizard.buffer:
                    wizard.buffer = f"+{char}"
                else:
                    wizard.buffer += char
            wizard.error_text = None
            await raw_answer_callback(client, query.id)
            await _refresh_keypad_ui(client, wizard)
            return

        if data == "kp:bs":
            wizard.buffer = wizard.buffer[:-1]
            wizard.error_text = None
            await raw_answer_callback(client, query.id)
            await _refresh_keypad_ui(client, wizard)
            return

        if data == "kp:clear":
            wizard.buffer = ""
            wizard.error_text = None
            await raw_answer_callback(client, query.id, "Cleared")
            await _refresh_keypad_ui(client, wizard)
            return

        if data == "kp:resend":
            if wizard.stage != "otp" or not wizard.phone_number:
                await raw_answer_callback(client, query.id, "Cannot resend right now")
                return
            await raw_answer_callback(client, query.id, "Resending OTP...")
            ok, msg = await client_manager.send_otp_code(wizard, wizard.phone_number)
            if not ok:
                wizard.error_text = msg
            await _refresh_keypad_ui(client, wizard)
            return

        if data == "kp:enter":
            if wizard.stage == "phone":
                await raw_answer_callback(client, query.id, "Sending OTP...")
                ok, msg = await client_manager.send_otp_code(wizard, wizard.buffer)
                if not ok:
                    wizard.error_text = msg
                await _refresh_keypad_ui(client, wizard)
                return

            if wizard.stage == "otp":
                await raw_answer_callback(client, query.id, "Verifying OTP...")
                state, msg, doc = await client_manager.verify_otp_code(
                    wizard, wizard.buffer
                )
                if state == "otp_error":
                    wizard.error_text = msg
                    await _refresh_keypad_ui(client, wizard)
                elif state == "2fa":
                    await client.edit_message_text(
                        chat_id=wizard.chat_id,
                        message_id=wizard.message_id,
                        text=(
                            "🔐 **Two-Step Verification Required**\n\n"
                            "Send your Telegram 2FA cloud password now.\n"
                            "_(Your password message will be deleted immediately and never stored.)_"
                        ),
                        reply_markup=cancel_2fa_keyboard(),
                    )
                elif state == "fatal":
                    await client.edit_message_text(
                        chat_id=wizard.chat_id,
                        message_id=wizard.message_id,
                        text=f"❌ {msg}",
                        reply_markup=main_menu_keyboard(),
                    )
                elif state == "success" and doc is not None:
                    await _safe_edit_rich(
                        client,
                        wizard.chat_id,
                        wizard.message_id,
                        _success_rich_message(doc, msg),
                        types.InlineKeyboardMarkup(
                            [
                                [
                                    types.InlineKeyboardButton(
                                        "📊 View Client Stats",
                                        callback_data=f"stats:view:{doc['client_id']}",
                                        style=enums.ButtonStyle.SUCCESS,
                                    )
                                ],
                                [
                                    types.InlineKeyboardButton(
                                        "🏠 Main Menu", callback_data="menu:home"
                                    )
                                ],
                            ]
                        ),
                        caption_text=_get_success_caption(doc, msg),
                    )


@Client.on_message(filters.private & filters.text & ~filters.regex(r"^(/|stats$)"), group=1)
async def wizard_text_guard_and_input(client: Client, message: types.Message) -> None:
    """Handle bot_token / 2fa text inputs and block accidental phone/OTP text messages."""
    if message.from_user is None or not message.text:
        return

    user_id = message.from_user.id
    wizard = client_manager.get_wizard(user_id)
    if wizard is None:
        return

    # Always delete sensitive user messages during onboarding
    try:
        await message.delete()
    except Exception:
        pass

    if wizard.stage in ("phone", "otp"):
        wizard.error_text = (
            "Do NOT send phone numbers or OTPs as text! Use ONLY the keypad buttons below."
        )
        await _refresh_keypad_ui(client, wizard)
        return

    if wizard.stage == "api_creds":
        parts = message.text.strip().split()
        if len(parts) != 2 or not parts[0].isdigit():
            err_msg = "Send API_ID and API_HASH separated by space/newline, or tap Skip."
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addacc_api_rich_message(
                    login_method=wizard.login_method,
                    error_text=err_msg,
                ),
                addacc_api_creds_keyboard(),
                caption_text=_get_addacc_api_caption(err_msg),
            )
            return
        api_id_val = int(parts[0])
        api_hash_val = parts[1].strip()
        if api_id_val <= 0 or not re.match(r"^[A-Za-z0-9]{16,64}$", api_hash_val):
            err_msg = "Invalid API_ID or API_HASH format."
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addacc_api_rich_message(
                    login_method=wizard.login_method,
                    error_text=err_msg,
                ),
                addacc_api_creds_keyboard(),
                caption_text=_get_addacc_api_caption(err_msg),
            )
            return
        wizard.custom_api_id = api_id_val
        wizard.custom_api_hash = api_hash_val
        await _proceed_addacc_login_stage(client, wizard)
        return

    if wizard.stage == "session_str":
        ok, status_msg, doc = await client_manager.verify_and_add_session_string(
            owner_id=user_id,
            session_string=message.text.strip(),
            custom_api_id=wizard.custom_api_id,
            custom_api_hash=wizard.custom_api_hash,
        )
        if not ok or doc is None:
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addacc_session_rich_message(error_text=status_msg),
                addacc_session_keyboard(),
                caption_text=_get_addacc_session_caption(status_msg),
            )
            return
        await client_manager.cancel_wizard(user_id)
        await _send_success_card(client, wizard, doc, status_msg)
        return

    if wizard.stage == "bot_token":
        token = message.text.strip()
        if not _BOT_TOKEN_RE.match(token):
            err_msg = "Invalid token format. Expected 123456789:AAH..."
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addbot_rich_message(
                    error_text=err_msg
                ),
                addbot_wizard_keyboard(),
                caption_text=_get_addbot_caption(err_msg),
            )
            return

        ok, status_msg, doc = await client_manager.verify_and_add_bot(user_id, token)
        if not ok or doc is None:
            wizard.message_id = await _safe_edit_rich(
                client,
                wizard.chat_id,
                wizard.message_id,
                build_addbot_rich_message(error_text=status_msg),
                addbot_wizard_keyboard(),
                caption_text=_get_addbot_caption(status_msg),
            )
            return

        await client_manager.cancel_wizard(user_id)
        await _safe_edit_rich(
            client,
            wizard.chat_id,
            wizard.message_id,
            _success_rich_message(doc, status_msg),
            types.InlineKeyboardMarkup(
                [
                    [
                        types.InlineKeyboardButton(
                            "📊 View Bot Stats",
                            callback_data=f"stats:view:{doc['client_id']}",
                            style=enums.ButtonStyle.SUCCESS,
                        )
                    ],
                    [types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
                ]
            ),
            caption_text=_get_success_caption(doc, status_msg),
        )
        return

    if wizard.stage == "2fa":
        state, msg, doc = await client_manager.verify_2fa_password(wizard, message.text)
        if state == "2fa_error":
            if wizard.message_id:
                try:
                    await client.edit_message_caption(
                        chat_id=wizard.chat_id,
                        message_id=wizard.message_id,
                        caption=f"❌ <b>{html.escape(msg)}</b>\n\nPlease send your 2FA password again or Cancel:",
                        reply_markup=cancel_2fa_keyboard(),
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    try:
                        await client.edit_message_text(
                            chat_id=wizard.chat_id,
                            message_id=wizard.message_id,
                            text=f"❌ {msg}\n\nPlease send your 2FA password again or Cancel:",
                            reply_markup=cancel_2fa_keyboard(),
                        )
                    except MessageNotModified:
                        pass
            return

        if state == "fatal" or doc is None:
            await client.send_message(
                wizard.chat_id,
                f"❌ {msg}",
                reply_markup=main_menu_keyboard(),
            )
            return

        await _safe_edit_rich(
            client,
            wizard.chat_id,
            wizard.message_id,
            _success_rich_message(doc, msg),
            types.InlineKeyboardMarkup(
                [
                    [
                        types.InlineKeyboardButton(
                            "📊 View Client Stats",
                            callback_data=f"stats:view:{doc['client_id']}",
                            style=enums.ButtonStyle.SUCCESS,
                        )
                    ],
                    [types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")],
                ]
            ),
            caption_text=_get_success_caption(doc, msg),
        )
