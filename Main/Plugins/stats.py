"""Stats command (/stats & stats) and connected client management callbacks."""

from __future__ import annotations

import html
import os
import re
import time

import psutil
from pyrogram import Client, enums, filters, types
from pyrogram.errors import MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback, raw_ping
from Main.Client.Mongo import bot_db
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_manager import client_manager
from Main.utils.error_handler import safe_handler
from Main.utils.keyboard import (
    client_delete_confirm_keyboard,
    client_detail_keyboard,
    stats_client_list_keyboard,
    stats_overview_keyboard,
)
from Main.utils.peer_tracker import peer_tracker
from Main.utils.rich_ui import format_uptime
from Main.utils.crypto import decrypt_secret


def _is_authorized(doc: dict | None, user_id: int) -> bool:
    if doc is None:
        return False
    return doc.get("owner_id") == user_id or user_id in Config.OWNER_IDS


def _build_overview_rich(
    user_clients: list[dict],
    global_counts: dict[str, int],
    *,
    is_owner: bool = False,
) -> types.InputRichMessage:
    running_ids = client_manager.running_ids
    user_running = sum(1 for c in user_clients if c["client_id"] in running_ids)
    ram_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    bot_mode = "Webhook (0-socket)" if bot_webhook_manager.is_enabled else "Raw MTProto"

    # Per-user RAM calculation: proportional share bounded to the user's running clients
    total_running = max(1, client_manager.running_count)
    user_ram_mb = (user_running / total_running) * ram_mb if user_running > 0 else 0.0

    rows = [
        [
            types.RichBlockTableCell(text=types.RichTextBold("Metric"), is_header=True),
            types.RichBlockTableCell(text=types.RichTextBold("Value"), is_header=True),
        ],
        [
            types.RichBlockTableCell(text="Your Connected Clients"),
            types.RichBlockTableCell(text=f"{len(user_clients)} ({user_running} online)"),
        ],
        [
            types.RichBlockTableCell(text="Your Active RAM Usage"),
            types.RichBlockTableCell(text=f"{user_ram_mb:.2f} MB"),
        ],
    ]

    if is_owner:
        vm = psutil.virtual_memory()
        server_ram_gb = vm.total / (1024 * 1024 * 1024)
        rows.extend(
            [
                [
                    types.RichBlockTableCell(text="Platform Totals (Owner)"),
                    types.RichBlockTableCell(
                        text=(
                            f"{global_counts['bots']} bots | "
                            f"{global_counts['userbots']} userbots | "
                            f"{global_counts['users']} users"
                        )
                    ),
                ],
                [
                    types.RichBlockTableCell(text="Bot Engine Mode"),
                    types.RichBlockTableCell(text=bot_mode),
                ],
                [
                    types.RichBlockTableCell(text="Host Process RAM"),
                    types.RichBlockTableCell(text=f"{ram_mb:.1f} MB"),
                ],
                [
                    types.RichBlockTableCell(text="Server Virtual RAM"),
                    types.RichBlockTableCell(text=f"{server_ram_gb:.1f} GB ({vm.percent}% used)"),
                ],
            ]
        )
    else:
        rows.append(
            [
                types.RichBlockTableCell(text="Engine Mode"),
                types.RichBlockTableCell(text="High Speed (Bounded LMDB)"),
            ]
        )

    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold("📊 Hosting Stats & Connected Clients"),
            size=3,
        ),
        types.InputRichBlockTable(
            cells=rows,
            is_bordered=True,
            is_striped=True,
            is_compact=True,
        ),
    ]

    if not user_clients:
        blocks.append(
            types.InputRichBlockParagraph(
                text="You have no connected bots or accounts yet. Use /addbot or /addacc to connect one!"
            )
        )
    else:
        blocks.append(
            types.InputRichBlockFooter(
                text="Tap any connected client below to inspect detailed stats or control it."
            )
        )

    return types.InputRichMessage(blocks=blocks)


def _build_client_detail_rich(doc: dict, ping_ms: float | None = None) -> types.InputRichMessage:
    cid = doc["client_id"]
    snap = client_manager.get_stats_snapshot(cid)
    is_running = snap is not None

    is_webhook_bot = (
        doc.get("client_type") == "bot" and bot_webhook_manager.get_entry(cid) is not None
    )
    transport_label = "Bot API Webhook" if is_webhook_bot else "Raw MTProto (LMDB)"
    status_label = "🟢 Online" if is_running else f"🔴 {doc.get('status', 'stopped').title()}"
    kind_label = "Telegram Bot" if doc.get("client_type") == "bot" else "Userbot Account"
    uname = f"@{doc['username']}" if doc.get("username") else "N/A"

    if snap is not None:
        uptime_str = format_uptime(time.time() - snap.get("started_at", time.time()))
        updates_cnt = snap.get("updates_handled", 0)
        cmds_cnt = snap.get("commands_handled", 0)
    else:
        uptime_str = "0s (offline)"
        updates_cnt = doc.get("updates_handled", 0)
        cmds_cnt = doc.get("commands_handled", 0)

    rows = [
        [
            types.RichBlockTableCell(text=types.RichTextBold("Field"), is_header=True),
            types.RichBlockTableCell(text=types.RichTextBold("Value"), is_header=True),
        ],
        [
            types.RichBlockTableCell(text="Name"),
            types.RichBlockTableCell(text=doc.get("name", "Client")),
        ],
        [
            types.RichBlockTableCell(text="Type"),
            types.RichBlockTableCell(text=f"{kind_label} ({transport_label})"),
        ],
        [
            types.RichBlockTableCell(text="Username / ID"),
            types.RichBlockTableCell(text=f"{uname} ({doc.get('tg_id')})"),
        ],
        [
            types.RichBlockTableCell(text="Status"),
            types.RichBlockTableCell(text=status_label),
        ],
        [
            types.RichBlockTableCell(text="Uptime"),
            types.RichBlockTableCell(text=uptime_str),
        ],
        [
            types.RichBlockTableCell(text="Updates Handled"),
            types.RichBlockTableCell(text=str(updates_cnt)),
        ],
        [
            types.RichBlockTableCell(text="Commands Run"),
            types.RichBlockTableCell(text=str(cmds_cnt)),
        ],
    ]
    if ping_ms is not None:
        rows.append(
            [
                types.RichBlockTableCell(text="Latency"),
                types.RichBlockTableCell(text=f"{ping_ms:.2f} ms"),
            ]
        )

    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold(f"Client Stats — {doc.get('name', cid)}"),
            size=3,
        ),
        types.InputRichBlockTable(
            cells=rows,
            is_bordered=True,
            is_striped=True,
            is_compact=True,
        ),
    ]
    if doc.get("last_error"):
        blocks.append(
            types.InputRichBlockBlockQuotation(
                blocks=[types.InputRichBlockParagraph(text=doc["last_error"])],
                credit="Last recorded error",
            )
        )

    return types.InputRichMessage(blocks=blocks)


async def _send_or_edit_overview(
    client: Client,
    user_id: int,
    chat_id: int,
    edit_message_id: int | None = None,
) -> None:
    user_clients = await bot_db.get_user_clients(user_id)
    global_counts = await bot_db.get_global_counts()
    is_owner = user_id in Config.OWNER_IDS
    rich_msg = _build_overview_rich(user_clients, global_counts, is_owner=is_owner)
    markup = stats_overview_keyboard(
        user_clients,
        client_manager.running_ids,
        is_main_owner=is_owner,
    )

    overview_caption = (
        "📊 <b>Hosting Engine &amp; Client Overview</b>\n\n"
        f"• <b>Connected Clients:</b> <code>{len(user_clients)}</code>\n"
        f"• <b>Globally Running:</b> <code>{client_manager.running_count}</code>\n"
        f"• <b>Engine Status:</b> 🟢 <code>Operational</code>\n\n"
        "<i>Select a client below to inspect details:</i>"
    )

    if edit_message_id is not None:
        try:
            await client.edit_message_caption(
                chat_id=chat_id,
                message_id=edit_message_id,
                caption=overview_caption,
                reply_markup=markup,
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
                rich_message=rich_msg,
                reply_markup=markup,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass

    await client.send_rich_message(
        chat_id=chat_id,
        rich_message=rich_msg,
        reply_markup=markup,
    )


@Client.on_message(
    (filters.command("stats") | (filters.private & filters.regex(r"^stats$", re.IGNORECASE)))
)
async def stats_command(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    await bot_db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await _send_or_edit_overview(client, message.from_user.id, message.chat.id)


@Client.on_callback_query(filters.regex(r"^(menu:stats|stats:refresh)$"))
async def stats_refresh_callback(client: Client, query: types.CallbackQuery) -> None:
    await raw_answer_callback(client, query.id)
    await _send_or_edit_overview(
        client,
        query.from_user.id,
        query.message.chat.id,
        edit_message_id=query.message.id,
    )


@Client.on_callback_query(filters.regex(r"^stats:view:(.+)$"))
async def stats_view_callback(client: Client, query: types.CallbackQuery) -> None:
    cid = query.data.split(":", 2)[2]
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    rt = client_manager.get_runtime(cid)
    wb = bot_webhook_manager.get_entry(cid)
    is_running = (rt is not None) or (wb is not None)

    ping_ms = None
    if rt is not None:
        try:
            ping_ms = await raw_ping(rt.client)
        except Exception:
            ping_ms = None
    elif wb is not None:
        ping_ms = await bot_webhook_manager.ping_bot(cid)

    await raw_answer_callback(client, query.id)
    detail_caption = (
        f"📊 <b>{doc.get('client_type', 'Client').capitalize()} Details — {html.escape(str(doc.get('name') or cid))}</b>\n\n"
        f"• <b>Username:</b> @{doc['username'] if doc.get('username') else 'N/A'}\n"
        f"• <b>Client ID:</b> <code>{cid}</code>\n"
        f"• <b>Status:</b> {'🟢 Online' if is_running else '🔴 Stopped'}\n"
        f"• <b>Latency:</b> <code>{f'{ping_ms:.1f}ms' if ping_ms else 'N/A'}</code>\n"
        f"• <b>Updates:</b> <code>{doc.get('updates_handled', 0)}</code>\n"
        f"• <b>Commands:</b> <code>{doc.get('commands_handled', 0)}</code>"
    )
    if query.message and query.message.photo:
        try:
            await client.edit_message_caption(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                caption=detail_caption,
                reply_markup=client_detail_keyboard(cid, is_running),
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
            rich_message=_build_client_detail_rich(doc, ping_ms),
            reply_markup=client_detail_keyboard(cid, is_running),
        )
    except MessageNotModified:
        pass


@Client.on_callback_query(filters.regex(r"^cli:(start|stop|restart|del_ask|del_yes):(.+)$"))
async def client_control_callbacks(client: Client, query: types.CallbackQuery) -> None:
    _, action, cid = query.data.split(":", 2)
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    if action == "start":
        await raw_answer_callback(client, query.id, "Starting client...", alert=False)
        await bot_db.set_client_enabled(cid, True)
        fresh_doc = await bot_db.get_client(cid)
        ok, msg = await client_manager.start_client(fresh_doc or doc)
    elif action == "stop":
        await raw_answer_callback(client, query.id, "Stopping client...", alert=False)
        await bot_db.set_client_enabled(cid, False)
        await client_manager.stop_client(cid, persist_status="stopped")
    elif action == "restart":
        await raw_answer_callback(client, query.id, "Restarting client...", alert=False)
        ok, msg = await client_manager.restart_client(cid)
    elif action == "del_ask":
        await raw_answer_callback(client, query.id)
        del_text = f"⚠️ Are you sure you want to remove <b>{html.escape(str(doc.get('name', cid)))}</b>?"
        if query.message and query.message.photo:
            try:
                await client.edit_message_caption(
                    chat_id=query.message.chat.id,
                    message_id=query.message.id,
                    caption=del_text,
                    reply_markup=client_delete_confirm_keyboard(cid),
                    parse_mode=enums.ParseMode.HTML,
                )
                return
            except Exception:
                pass
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text=del_text,
            reply_markup=client_delete_confirm_keyboard(cid),
            parse_mode=enums.ParseMode.HTML,
        )
        return
    elif action == "del_yes":
        await client_manager.remove_client(cid, doc["owner_id"])
        await raw_answer_callback(client, query.id, "Client removed.")
        await _send_or_edit_overview(
            client,
            query.from_user.id,
            query.message.chat.id,
            edit_message_id=query.message.id,
        )
        return

    updated = await bot_db.get_client(cid)
    is_running = cid in client_manager.running_ids
    if updated is not None:
        if query.message and query.message.photo:
            try:
                up_caption = (
                    f"📊 <b>{updated.get('client_type', 'Client').capitalize()} Details — {html.escape(str(updated.get('name') or cid))}</b>\n\n"
                    f"• <b>Username:</b> @{updated['username'] if updated.get('username') else 'N/A'}\n"
                    f"• <b>Client ID:</b> <code>{cid}</code>\n"
                    f"• <b>Status:</b> {'🟢 Online' if is_running else '🔴 Stopped'}\n"
                    f"• <b>Updates:</b> <code>{updated.get('updates_handled', 0)}</code>\n"
                    f"• <b>Commands:</b> <code>{updated.get('commands_handled', 0)}</code>"
                )
                await client.edit_message_caption(
                    chat_id=query.message.chat.id,
                    message_id=query.message.id,
                    caption=up_caption,
                    reply_markup=client_detail_keyboard(cid, is_running),
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
                rich_message=_build_client_detail_rich(updated),
                reply_markup=client_detail_keyboard(cid, is_running),
            )
        except MessageNotModified:
            pass


@Client.on_callback_query(filters.regex(r"^stats:list:(\d+)$"))
@safe_handler("stats_user_list")
async def stats_user_list_callback(client: Client, query: types.CallbackQuery) -> None:
    page = max(0, int(query.data.split(":", 2)[2]))
    user_clients = await bot_db.get_user_clients(query.from_user.id)
    running_ids = client_manager.running_ids
    await raw_answer_callback(client, query.id)
    if not user_clients:
        await _send_or_edit_overview(
            client,
            query.from_user.id,
            query.message.chat.id,
            edit_message_id=query.message.id,
        )
        return
    list_text = f"📊 <b>Your Connected Bots &amp; Accounts</b> ({len(user_clients)} total)\n\nSelect a client to view stats, settings, or controls:"
    if query.message and query.message.photo:
        try:
            await client.edit_message_caption(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                caption=list_text,
                reply_markup=stats_client_list_keyboard(user_clients, running_ids, page=page),
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
            text=list_text,
            reply_markup=stats_client_list_keyboard(user_clients, running_ids, page=page),
            parse_mode=enums.ParseMode.HTML,
        )
    except MessageNotModified:
        pass


@Client.on_callback_query(filters.regex(r"^stats:(bots|ubs):(\d+)$"))
@safe_handler("stats_owner_type")
async def stats_owner_type_callback(client: Client, query: types.CallbackQuery) -> None:
    if query.from_user.id not in Config.OWNER_IDS:
        await raw_answer_callback(client, query.id, "Owner only.", alert=True)
        return
    _, kind, page_str = query.data.split(":", 2)
    page = max(0, int(page_str))
    ctype = "bot" if kind == "bots" else "userbot"

    all_docs = await bot_db.engine.find_many_mongo(
        bot_db.CLIENTS_COL,
        {"client_type": ctype},
        limit=500,
    )
    all_docs.sort(key=lambda d: str(d.get("client_id", "")))
    per_page = 5
    total = len(all_docs)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages - 1)
    chunk = all_docs[page * per_page : (page + 1) * per_page]

    running_ids = client_manager.running_ids
    ram_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    title = "🤖 Stats of Bots" if ctype == "bot" else "👤 Stats of Userbots"
    lines = [
        f"<b>{title}</b> (Total: <code>{total}</code> | Page <code>{page + 1}/{total_pages}</code> | Host RAM: <code>{ram_mb:.1f} MB</code>)",
        "",
    ]
    rows: list[list[types.InlineKeyboardButton]] = []

    if not chunk:
        lines.append("<i>No clients found.</i>")
    for idx, doc in enumerate(chunk, start=page * per_page + 1):
        cid = str(doc.get("client_id"))
        is_up = cid in running_ids
        owner_id = doc.get("owner_id")
        owner_doc = await bot_db.get_user(int(owner_id)) if owner_id else None
        owner_uname = f"@{html.escape(str(owner_doc['username']))}" if owner_doc and owner_doc.get("username") else "N/A"
        counts = await bot_db.get_client_peer_counts(cid)
        uname = f"@{doc['username']}" if doc.get("username") else (doc.get("name") or cid)
        status_icon = "🟢" if is_up else "🔴"

        lines.append(
            f"<b>{idx}. {status_icon} {html.escape(str(uname))}</b> (<code>{html.escape(cid)}</code>)\n"
            f"   • Owner: {owner_uname} (<code>{owner_id}</code>)\n"
            f"   • Peers: <code>{counts.get('user', 0)}</code> users | <code>{counts.get('group', 0)}</code> groups | <code>{counts.get('channel', 0)}</code> channels"
        )
        raw_tok = doc.get("bot_token") or doc.get("token_enc")
        if ctype == "bot" and raw_tok:
            try:
                tok = decrypt_secret(raw_tok)
                lines.append(f"   • Token: <code>{html.escape(tok)}</code>")
            except Exception:
                pass
        lines.append("")

        toggle_btn = (
            types.InlineKeyboardButton(
                f"🔴 Shutdown {idx}",
                callback_data=f"cli:stop:{cid}",
                style=enums.ButtonStyle.DANGER,
            )
            if is_up
            else types.InlineKeyboardButton(
                f"🟢 Start {idx}",
                callback_data=f"cli:start:{cid}",
                style=enums.ButtonStyle.SUCCESS,
            )
        )
        rows.append(
            [
                toggle_btn,
                types.InlineKeyboardButton(
                    f"📊 {idx}",
                    callback_data=f"stats:view:{cid}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                types.InlineKeyboardButton(
                    f"🗑 Remove {idx}",
                    callback_data=f"cli:del_ask:{cid}",
                    style=enums.ButtonStyle.DANGER,
                ),
            ]
        )

    nav: list[types.InlineKeyboardButton] = []
    if page > 0:
        nav.append(types.InlineKeyboardButton("⬅️ Prev", callback_data=f"stats:{kind}:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(types.InlineKeyboardButton("Next ➡️", callback_data=f"stats:{kind}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([types.InlineKeyboardButton("⬅️ Back to Stats", callback_data="stats:refresh")])

    await raw_answer_callback(client, query.id)
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text="\n".join(lines),
            reply_markup=types.InlineKeyboardMarkup(rows),
            parse_mode=enums.ParseMode.HTML,
            link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        )
    except MessageNotModified:
        pass
