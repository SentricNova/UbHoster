"""Core /start, /ping, /help, /cancel and main menu handlers for the hosting bot."""

from __future__ import annotations

import contextlib
import html
import logging
import os
import time

import psutil
from pyrogram import Client, enums, filters, types
from pyrogram.errors import MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback, raw_ping
from Main.Client.Mongo import bot_db
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_actions import check_force_join
from Main.utils.client_manager import client_manager
from Main.utils.error_handler import safe_handler
from Main.utils.keyboard import main_menu_keyboard
from Main.utils.rich_ui import build_help_rich_message, format_uptime
from Main.utils.widget_showcase import build_main_bot_showcase_widget_html

log = logging.getLogger(__name__)

_BOT_START_TIME = time.time()


def _build_welcome_rich(first_name: str, user_clients_count: int) -> types.InputRichMessage:
    return types.InputRichMessage(
        blocks=[
            types.InputRichBlockSectionHeading(
                text=types.RichTextBold(f"Welcome, {first_name}!"),
                size=3,
            ),
            types.InputRichBlockParagraph(
                text=(
                    "Host your Telegram Bots and Userbot Accounts on a lightweight, "
                    "raw-MTProto engine with low RAM overhead."
                )
            ),
            types.InputRichBlockTable(
                cells=[
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Feature"), is_header=True),
                        types.RichBlockTableCell(text=types.RichTextBold("Command"), is_header=True),
                    ],
                    [
                        types.RichBlockTableCell(text="Host a Telegram Bot"),
                        types.RichBlockTableCell(text=types.RichTextCode("/addbot")),
                    ],
                    [
                        types.RichBlockTableCell(text="Host an Account (Secure Keypad)"),
                        types.RichBlockTableCell(text=types.RichTextCode("/addacc")),
                    ],
                    [
                        types.RichBlockTableCell(text="View Connected Stats"),
                        types.RichBlockTableCell(text=types.RichTextCode("/stats / stats")),
                    ],
                    [
                        types.RichBlockTableCell(text="Broadcast & Peers"),
                        types.RichBlockTableCell(text=types.RichTextCode("/broadcast")),
                    ],
                    [
                        types.RichBlockTableCell(text="Export Session String"),
                        types.RichBlockTableCell(text=types.RichTextCode("/export")),
                    ],
                    [
                        types.RichBlockTableCell(text="Check Latency & DB"),
                        types.RichBlockTableCell(text=types.RichTextCode("/ping")),
                    ],
                ],
                is_bordered=True,
                is_striped=True,
                is_compact=True,
            ),
            types.InputRichBlockFooter(
                text=f"Your connected clients: {user_clients_count} | Running globally: {client_manager.running_count}"
            ),
        ]
    )


async def _check_access_gates(client: Client, user: types.User, chat_id: int) -> bool:
    if user.id not in Config.OWNER_IDS:
        maint = await bot_db.get_maintenance()
        if maint.get("enabled"):
            reason = maint.get("reason") or "Bot is currently under maintenance. Please try again later."
            await client.send_message(chat_id, f"🛠 **Maintenance Mode**\n\n{reason}")
            return False
        fj_ok, missing_chats = await check_force_join(user.id, is_main_bot=True)
        if not fj_ok:
            fj_rows = []
            for ch in missing_chats:
                title = str(ch.get("title") or ch.get("chat_id") or "Join Chat")
                uname = ch.get("username")
                url = ch.get("invite_link") or (f"https://t.me/{uname}" if uname else None)
                if url:
                    fj_rows.append([types.InlineKeyboardButton(f"📢 Join {title[:35]}", url=url)])
            await client.send_message(
                chat_id,
                "🔒 **Join Required**\n\nPlease join the required chat(s) below and then press `/start` again.",
                reply_markup=types.InlineKeyboardMarkup(fj_rows) if fj_rows else None,
            )
            return False
    return True


@Client.on_message(filters.command("start") & filters.private)
@safe_handler("start_handler")
async def start_handler(client: Client, message: types.Message) -> None:
    user = message.from_user
    if user is None:
        return
    user_doc = await bot_db.ensure_user(user.id, user.username, user.first_name)
    await bot_db.touch_owner_activity(user.id)

    # Logger group notice for newly joined users
    if user_doc.get("is_new") and Config.LOGGER_GROUP:
        uname_str = f"@{user.username}" if user.username else "No username"
        fname_str = html.escape(user.first_name or "User")
        log_msg = (
            "👤 <b>New User Started Main Bot!</b>\n\n"
            f"• <b>Name:</b> {fname_str}\n"
            f"• <b>Username:</b> {uname_str}\n"
            f"• <b>User ID:</b> <code>{user.id}</code>"
        )
        with contextlib.suppress(Exception):
            await client.send_message(Config.LOGGER_GROUP, log_msg, parse_mode=enums.ParseMode.HTML)

    # Affiliate / Referral Link Tracking (/start ref_<id> or /start <id>)
    if len(message.command) > 1:
        param = message.command[1].strip()
        ref_id_str = param.replace("ref_", "")
        if ref_id_str.isdigit():
            inviter_id = int(ref_id_str)
            if inviter_id != user.id:
                credited, new_cnt, _ = await bot_db.record_referral(inviter_id, user.id)
                if credited:
                    settings = await bot_db.get_affiliate_settings()
                    req = settings.get("required_invites", 3)
                    mention = f"@{user.username}" if user.username else (user.first_name or f"ID {user.id}")
                    notice = (
                        f"🎉 <b>New Referral Joined!</b>\n\n"
                        f"User <b>{html.escape(mention)}</b> (<code>{user.id}</code>) joined using your personal invite link!\n"
                        f"📊 Total Invites: <b>{new_cnt}/{req}</b>"
                    )
                    if new_cnt >= req:
                        notice += "\n\n🌟 <b>Congratulations!</b> You have unlocked full access to all premium hosting features!"
                    with contextlib.suppress(Exception):
                        await client.send_message(inviter_id, notice, parse_mode=enums.ParseMode.HTML)

    if not await _check_access_gates(client, user, message.chat.id):
        return
    user_clients = await bot_db.get_user_clients(user.id)
    welcome_caption = (
        f"👋 <b>Welcome, {html.escape(user.first_name or 'User')}!</b>\n\n"
        "Host your <b>Telegram Bots</b> and <b>Userbot Accounts</b> on an ultra-lightweight, "
        "raw-MTProto engine with minimal RAM overhead.\n\n"
        f"• <b>Your Connected Clients:</b> <code>{len(user_clients)}</code>\n"
        f"• <b>Running Globally:</b> <code>{client_manager.running_count}</code>\n"
        f"• <b>Service Status:</b> 🟢 <code>Operational</code>\n\n"
        "<i>Select an option below to manage your bots and accounts:</i>"
    )

    sent = False
    if Config.START_MEDIA_URL:
        try:
            await client.send_photo(
                chat_id=message.chat.id,
                photo=Config.START_MEDIA_URL,
                caption=welcome_caption,
                reply_markup=main_menu_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )
            sent = True
        except Exception as exc:
            log.debug("send_photo via START_MEDIA_URL failed (%s), fallback to rich message", exc)

    if not sent:
        await client.send_rich_message(
            chat_id=message.chat.id,
            rich_message=_build_welcome_rich(user.first_name or "User", len(user_clients)),
            reply_markup=main_menu_keyboard(),
        )


@Client.on_message(filters.command("ping"))
async def ping_handler(client: Client, message: types.Message) -> None:
    tg_ms = await raw_ping(client)
    mongo_ms, lmdb_ms = await bot_db.ping()
    uptime = format_uptime(time.time() - _BOT_START_TIME)
    is_owner = bool(message.from_user and message.from_user.id in Config.OWNER_IDS)

    if is_owner:
        ram_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
        vm = psutil.virtual_memory()
        server_ram_gb = vm.total / (1024 * 1024 * 1024)
        text = (
            f"🏓 **Pong! (Host Admin View)**\n"
            f"• **Telegram Raw Ping:** `{tg_ms:.2f} ms`\n"
            f"• **MongoDB Async:** `{mongo_ms:.2f} ms`\n"
            f"• **LMDB Cache:** `{lmdb_ms:.3f} ms`\n"
            f"• **Process RAM:** `{ram_mb:.1f} MB`\n"
            f"• **Server Virtual RAM:** `{server_ram_gb:.1f} GB ({vm.percent}% used)`\n"
            f"• **Active Userbots (MTProto):** `{client_manager.running_mtproto_count}`\n"
            f"• **Active Bots (Webhook):** `{bot_webhook_manager.active_count}`\n"
            f"• **System Uptime:** `{uptime}`"
        )
    else:
        text = (
            f"🏓 **Pong!**\n"
            f"• **Latency:** `{tg_ms:.2f} ms`\n"
            f"• **Database Sync:** `{mongo_ms:.2f} ms`\n"
            f"• **Cache Latency:** `{lmdb_ms:.3f} ms`\n"
            f"• **Service Health:** `🟢 100% Operational`\n"
            f"• **Uptime:** `{uptime}`"
        )

    await message.reply_text(text, quote=True)


@Client.on_message(filters.command("help"))
async def help_handler(client: Client, message: types.Message) -> None:
    is_owner = bool(message.from_user and message.from_user.id in Config.OWNER_IDS)
    try:
        await client.send_rich_message(
            chat_id=message.chat.id,
            rich_message=build_help_rich_message(is_owner=is_owner),
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        # Fallback to styled HTML message if rich message is unsupported
        help_html = (
            "╔═══════════════════════════╗\n"
            "   ⚡ <b>ANTIGRAVITY HOSTING HELP</b> ⚡\n"
            "╚═══════════════════════════╝\n\n"
            "<b>🎮 Core Hosting Commands</b>\n"
            "<blockquote>"
            "• <code>/start</code> — Open dashboard & feature showcase\n"
            "• <code>/addbot</code> — Connect & host a Telegram Bot via Bot Token\n"
            "• <code>/addacc</code> — Connect & host a Userbot via secure keypad\n"
            "• <code>/stats</code> — View & manage hosted bots/accounts\n"
            "• <code>/broadcast</code> — Broadcast message to your audience\n"
            "• <code>/export</code> — Export connected session string\n"
            "• <code>/invite</code> — Affiliate referral link & rewards\n"
            "• <code>/ping</code> — Check MTProto, MongoDB & LMDB latency"
            "</blockquote>"
        )
        if is_owner:
            help_html += (
                "\n\n<b>👑 Master Owner Controls</b>\n"
                "<blockquote>"
                "• <code>/restart</code> — Restart hosting engine\n"
                "• <code>/cleancache</code> — Prune LMDB cache\n"
                "• <code>/maintenance</code> — Toggle maintenance mode\n"
                "• <code>/forcejoin</code> — Manage mandatory join channels\n"
                "• <code>/tracemem</code> — Profile per-file RAM usage"
                "</blockquote>"
            )
        await message.reply_text(
            help_html,
            reply_markup=main_menu_keyboard(),
            parse_mode=enums.ParseMode.HTML,
            link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        )


@Client.on_message(filters.command("cancel") & filters.private)
async def cancel_handler(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    from Main.Plugins.broadcast import cancel_broadcast_draft

    wiz = client_manager.get_wizard(message.from_user.id)
    bc_cancelled = cancel_broadcast_draft(message.from_user.id)
    if wiz is None and not bc_cancelled:
        await message.reply_text("No active setup wizard to cancel.", quote=True)
        return
    if wiz is not None:
        await client_manager.cancel_wizard(message.from_user.id)
    await message.reply_text(
        "❌ Cancelled.",
        reply_markup=main_menu_keyboard(),
        quote=True,
    )


@Client.on_callback_query(filters.regex(r"^menu:(home|ping|help)$"))
async def menu_callbacks(client: Client, query: types.CallbackQuery) -> None:
    action = query.data.split(":", 1)[1]
    user = query.from_user

    if action == "home":
        await raw_answer_callback(client, query.id)
        await bot_db.ensure_user(user.id, user.username, user.first_name)
        await bot_db.touch_owner_activity(user.id)
        if not await _check_access_gates(client, user, query.message.chat.id):
            return
        user_clients = await bot_db.get_user_clients(user.id)
        welcome_caption = (
            f"👋 <b>Welcome, {html.escape(user.first_name or 'User')}!</b>\n\n"
            "Host your <b>Telegram Bots</b> and <b>Userbot Accounts</b> on an ultra-lightweight, "
            "raw-MTProto engine with minimal RAM overhead.\n\n"
            f"• <b>Your Connected Clients:</b> <code>{len(user_clients)}</code>\n"
            f"• <b>Running Globally:</b> <code>{client_manager.running_count}</code>\n"
            f"• <b>Service Status:</b> 🟢 <code>Operational</code>\n\n"
            "<i>Select an option below to manage your bots and accounts:</i>"
        )
        if query.message and query.message.photo:
            try:
                await client.edit_message_caption(
                    chat_id=query.message.chat.id,
                    message_id=query.message.id,
                    caption=welcome_caption,
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
                rich_message=_build_welcome_rich(user.first_name or "User", len(user_clients)),
                reply_markup=main_menu_keyboard(),
            )
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await client.send_message(
                query.message.chat.id,
                welcome_caption,
                reply_markup=main_menu_keyboard(),
                parse_mode=enums.ParseMode.HTML,
            )
        return

    if action == "help":
        await raw_answer_callback(client, query.id)
        is_owner = query.from_user.id in Config.OWNER_IDS
        help_html = (
            "╔═══════════════════════════╗\n"
            "   ⚡ <b>ANTIGRAVITY HOSTING HELP</b> ⚡\n"
            "╚═══════════════════════════╝\n\n"
            "<b>🎮 Core Hosting Commands</b>\n"
            "<blockquote>"
            "• <code>/start</code> — Open dashboard & feature showcase\n"
            "• <code>/addbot</code> — Connect & host a Telegram Bot via Bot Token\n"
            "• <code>/addacc</code> — Connect & host a Userbot via secure keypad\n"
            "• <code>/stats</code> — View & manage hosted bots/accounts\n"
            "• <code>/broadcast</code> — Broadcast message to your audience\n"
            "• <code>/export</code> — Export connected session string\n"
            "• <code>/invite</code> — Affiliate referral link & rewards\n"
            "• <code>/ping</code> — Check MTProto, MongoDB & LMDB latency"
            "</blockquote>"
        )
        if is_owner:
            help_html += (
                "\n\n<b>👑 Master Owner Controls</b>\n"
                "<blockquote>"
                "• <code>/restart</code> — Restart hosting engine\n"
                "• <code>/cleancache</code> — Prune LMDB cache\n"
                "• <code>/maintenance</code> — Toggle maintenance mode\n"
                "• <code>/forcejoin</code> — Manage mandatory join channels\n"
                "• <code>/tracemem</code> — Profile per-file RAM usage"
                "</blockquote>"
            )
        if query.message and query.message.photo:
            try:
                await client.edit_message_caption(
                    chat_id=query.message.chat.id,
                    message_id=query.message.id,
                    caption=help_html,
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
                rich_message=build_help_rich_message(is_owner=is_owner),
                reply_markup=main_menu_keyboard(),
            )
        except MessageNotModified:
            pass
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await client.send_message(
                chat_id=query.message.chat.id,
                text=help_html,
                reply_markup=main_menu_keyboard(),
                parse_mode=enums.ParseMode.HTML,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )
        return

    if action == "ping":
        tg_ms = await raw_ping(client)
        mongo_ms, lmdb_ms = await bot_db.ping()
        await raw_answer_callback(
            client,
            query.id,
            f"TG: {tg_ms:.0f}ms | Mongo: {mongo_ms:.0f}ms | LMDB: {lmdb_ms:.2f}ms",
            alert=True,
        )


async def _send_affiliate_panel(
    client: Client,
    user_id: int,
    chat_id: int,
    edit_message_id: int | None = None,
) -> None:
    is_owner = user_id in Config.OWNER_IDS
    settings = await bot_db.get_affiliate_settings()
    enabled = bool(settings.get("enabled", False))
    required = int(settings.get("required_invites", 3))
    me = await client.get_me()

    if is_owner:
        total_refs = await bot_db.engine.collection(bot_db.REFERRALS_COL).count_documents({})
        status_badge = "🟢 <b>Enabled</b>" if enabled else "🔴 <b>Disabled (Off by Default)</b>"
        text = (
            f"👑 <b>Master Affiliate Program Tuning</b>\n\n"
            f"• <b>Program Status:</b> {status_badge}\n"
            f"• <b>Required Invites to Unlock:</b> <code>{required}</code>\n"
            f"• <b>Total Referrals Platform-Wide:</b> <code>{total_refs}</code>\n\n"
            f"<i>When enabled, users must invite {required} users to access /addbot and /addacc. "
            f"You can toggle it or adjust the requirements below:</i>"
        )
        toggle_label = "🔴 Turn OFF" if enabled else "🟢 Turn ON"
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton(
                        toggle_label,
                        callback_data="affiliate:toggle",
                        style=enums.ButtonStyle.DANGER if enabled else enums.ButtonStyle.SUCCESS,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        "➖ Decrement (-1)",
                        callback_data="affiliate:dec",
                        style=enums.ButtonStyle.PRIMARY,
                    ),
                    types.InlineKeyboardButton(
                        "➕ Increment (+1)",
                        callback_data="affiliate:inc",
                        style=enums.ButtonStyle.PRIMARY,
                    ),
                ],
                [
                    types.InlineKeyboardButton(
                        "🏆 Leaderboard",
                        callback_data="affiliate:lead",
                        style=enums.ButtonStyle.PRIMARY,
                    ),
                    types.InlineKeyboardButton(
                        "🏠 Main Menu",
                        callback_data="menu:home",
                    ),
                ],
            ]
        )
    else:
        import urllib.parse
        info = await bot_db.get_user_referral_info(user_id)
        cnt = info.get("invited_count", 0)
        unlocked = info.get("unlocked", True)
        bot_uname = (me.username if me else "") or "PyroTesting_Bot"
        ref_link = f"https://t.me/{bot_uname}?start=ref_{user_id}"
        share_text = f"Host Telegram bots & userbots with ultra-low RAM! Get started: {ref_link}"
        share_url = f"https://t.me/share/url?url={urllib.parse.quote(ref_link, safe='')}&text={urllib.parse.quote(share_text, safe='')}"

        status_str = "🟢 <b>Unlocked (Full Access Active)</b>" if unlocked else f"🔒 <b>Locked ({max(1, required - cnt)} more invites needed)</b>"

        text = (
            f"🎁 <b>Affiliate Rewards Program</b>\n\n"
            f"Invite friends to @{bot_uname} and unlock premium bot hosting perks!\n\n"
            f"• <b>Your Referral Count:</b> <code>{cnt} / {required}</code>\n"
            f"• <b>Access Status:</b> {status_str}\n"
            f"• <b>Requirement Status:</b> {'Active' if enabled else 'Free Access Mode (Disabled)'}\n\n"
            f"🔗 <b>Your Personal Invite Link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            f"<i>Share your invite link with friends. Once they press /start, your counter automatically updates!</i>"
        )
        keyboard = types.InlineKeyboardMarkup(
            [
                [
                    types.InlineKeyboardButton(
                        "📤 Share Invite Link",
                        url=share_url,
                    )
                ],
                [
                    types.InlineKeyboardButton(
                        "🔄 Refresh Stats",
                        callback_data="menu:affiliate",
                        style=enums.ButtonStyle.PRIMARY,
                    ),
                    types.InlineKeyboardButton(
                        "🏠 Main Menu",
                        callback_data="menu:home",
                    ),
                ],
            ]
        )

    if edit_message_id is not None:
        try:
            await client.edit_message_caption(
                chat_id=chat_id,
                message_id=edit_message_id,
                caption=text,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass
        try:
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=edit_message_id,
                text=text,
                reply_markup=keyboard,
                parse_mode=enums.ParseMode.HTML,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await client.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML,
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )


@Client.on_message(filters.command("invite") & filters.private)
@safe_handler("invite_command")
async def invite_command(client: Client, message: types.Message) -> None:
    user = message.from_user
    if user is None:
        return
    await bot_db.ensure_user(user.id, user.username, user.first_name)
    is_owner = user.id in Config.OWNER_IDS

    # Check for owner commands e.g. /invite on, /invite off, /invite set 5
    if is_owner and len(message.command) > 1:
        sub = message.command[1].lower()
        settings = await bot_db.get_affiliate_settings()
        if sub in ("on", "enable", "1"):
            await bot_db.set_affiliate_settings(True, settings.get("required_invites", 3))
            await message.reply_text("✅ <b>Affiliate Program Enabled!</b>\nUsers must now invite friends to unlock hosting features.", quote=True, parse_mode=enums.ParseMode.HTML)
            return
        elif sub in ("off", "disable", "0"):
            await bot_db.set_affiliate_settings(False, settings.get("required_invites", 3))
            await message.reply_text("❌ <b>Affiliate Program Disabled!</b>\nAll users now have full free access.", quote=True, parse_mode=enums.ParseMode.HTML)
            return
        elif sub == "set" and len(message.command) > 2 and message.command[2].isdigit():
            req = max(1, int(message.command[2]))
            await bot_db.set_affiliate_settings(settings.get("enabled", False), req)
            await message.reply_text(f"⚙️ <b>Required Invites updated to {req} users.</b>", quote=True, parse_mode=enums.ParseMode.HTML)
            return

    await _send_affiliate_panel(client, user.id, message.chat.id)


@Client.on_callback_query(filters.regex(r"^menu:(affiliate|export)$"))
async def menu_affiliate_export_callback(client: Client, query: types.CallbackQuery) -> None:
    action = query.data.split(":", 1)[1]
    await raw_answer_callback(client, query.id)
    if action == "affiliate":
        await _send_affiliate_panel(client, query.from_user.id, query.message.chat.id, edit_message_id=query.message.id)
    elif action == "export":
        user_clients = await bot_db.get_user_clients(query.from_user.id)
        if not user_clients:
            await client.send_message(query.message.chat.id, "You don't have any connected bots or accounts to export.")
            return
        await client.send_message(query.message.chat.id, "Send <code>/export</code> to choose which client's session string to export.", parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^affiliate:(toggle|inc|dec|lead)$"))
async def affiliate_control_callback(client: Client, query: types.CallbackQuery) -> None:
    action = query.data.split(":", 1)[1]
    if query.from_user.id not in Config.OWNER_IDS:
        await raw_answer_callback(client, query.id, "Owner only.", alert=True)
        return

    settings = await bot_db.get_affiliate_settings()
    cur_enabled = bool(settings.get("enabled", False))
    cur_req = int(settings.get("required_invites", 3))

    if action == "toggle":
        new_state = not cur_enabled
        await bot_db.set_affiliate_settings(new_state, cur_req)
        await raw_answer_callback(client, query.id, f"Affiliate is now {'ON' if new_state else 'OFF'}.")
    elif action == "inc":
        new_req = cur_req + 1
        await bot_db.set_affiliate_settings(cur_enabled, new_req)
        await raw_answer_callback(client, query.id, f"Required invites set to {new_req}.")
    elif action == "dec":
        new_req = max(1, cur_req - 1)
        await bot_db.set_affiliate_settings(cur_enabled, new_req)
        await raw_answer_callback(client, query.id, f"Required invites set to {new_req}.")
    elif action == "lead":
        leaders = await bot_db.get_top_referrers(10)
        lines = ["🏆 <b>Affiliate Leaderboard (Top Referrers)</b>\n"]
        if not leaders:
            lines.append("<i>No referrals recorded yet.</i>")
        else:
            for i, l in enumerate(leaders, 1):
                uname = f"@{html.escape(l['username'])}" if l.get("username") else html.escape(str(l.get("name") or l["user_id"]))
                lines.append(f"{i}. {uname} (<code>{l['user_id']}</code>) — <b>{l['count']}</b> invites")
        await raw_answer_callback(client, query.id)
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text="\n".join(lines),
            reply_markup=types.InlineKeyboardMarkup(
                [
                    [
                        types.InlineKeyboardButton("⬅️ Back to Affiliate", callback_data="menu:affiliate"),
                        types.InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
                    ]
                ]
            ),
            parse_mode=enums.ParseMode.HTML,
        )
        return

    await _send_affiliate_panel(client, query.from_user.id, query.message.chat.id, edit_message_id=query.message.id)
