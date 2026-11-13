"""Audience management (Users/Groups/Channels) and /broadcast plugin for Main Bot."""

from __future__ import annotations

from dataclasses import dataclass

from pyrogram import Client, filters, types
from pyrogram.errors import MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback
from Main.Client.Mongo import bot_db
from Main.utils.client_manager import client_manager
from Main.utils.keyboard import (
    broadcast_cancel_keyboard,
    broadcast_client_picker_keyboard,
    broadcast_target_keyboard,
    main_menu_keyboard,
    peers_list_keyboard,
    peers_menu_keyboard,
)
from Main.utils.peer_tracker import broadcast_to_client_audience, peer_tracker


@dataclass
class _BroadcastDraft:
    client_id: str
    target: str  # "all" | "user" | "group" | "channel"
    message_id: int | None = None
    text: str = ""
    source_message: types.Message | None = None
    stage: int = 0
    estimated_count: int = 0


_BROADCAST_DRAFTS: dict[int, _BroadcastDraft] = {}
_PAGE_SIZE = 8


def cancel_broadcast_draft(user_id: int) -> bool:
    return _BROADCAST_DRAFTS.pop(user_id, None) is not None


async def _safe_edit_text_or_send(
    client: Client,
    chat_id: int,
    message_id: int | None,
    text: str,
    reply_markup: types.InlineKeyboardMarkup,
) -> None:
    if message_id is not None:
        try:
            await client.edit_message_caption(
                chat_id=chat_id,
                message_id=message_id,
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
            await client.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML,
            )
            return
        except MessageNotModified:
            return
        except Exception:
            pass
    await client.send_message(
        chat_id,
        text,
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML,
    )


def _is_authorized(doc: dict | None, user_id: int) -> bool:
    if doc is None:
        return False
    return doc.get("owner_id") == user_id or user_id in Config.OWNER_IDS


def _build_peers_summary_rich(doc: dict, counts: dict[str, int]) -> types.InputRichMessage:
    rows = [
        [
            types.RichBlockTableCell(text=types.RichTextBold("Audience"), is_header=True),
            types.RichBlockTableCell(text=types.RichTextBold("Tracked Count"), is_header=True),
        ],
        [
            types.RichBlockTableCell(text="👤 Users (Private)"),
            types.RichBlockTableCell(text=str(counts.get("user", 0))),
        ],
        [
            types.RichBlockTableCell(text="👥 Groups / Supergroups"),
            types.RichBlockTableCell(text=str(counts.get("group", 0))),
        ],
        [
            types.RichBlockTableCell(text="📢 Channels"),
            types.RichBlockTableCell(text=str(counts.get("channel", 0))),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextBold("Total Reachable")),
            types.RichBlockTableCell(text=types.RichTextBold(str(counts.get("total", 0)))),
        ],
    ]
    return types.InputRichMessage(
        blocks=[
            types.InputRichBlockSectionHeading(
                text=types.RichTextBold(f"👥 Audience Manager — {doc.get('name', doc['client_id'])}"),
                size=3,
            ),
            types.InputRichBlockTable(
                cells=rows,
                is_bordered=True,
                is_striped=True,
                is_compact=True,
            ),
            types.InputRichBlockFooter(
                text="Peers are captured automatically on messages/joins. You can also broadcast directly using /broadcast inside your hosted bot."
            ),
        ]
    )


async def _show_broadcast_entry(
    client: Client,
    user_id: int,
    chat_id: int,
    edit_message_id: int | None = None,
) -> None:
    clients = await bot_db.get_user_clients(user_id)
    if not clients:
        text = "You have no connected bots or accounts yet. Use /addbot or /addacc first."
        await _safe_edit_text_or_send(
            client, chat_id, edit_message_id, text, main_menu_keyboard()
        )
        return

    text = "📣 **Broadcast Center**\n\nSelect which connected bot or account you want to broadcast from:"
    markup = broadcast_client_picker_keyboard(clients)
    await _safe_edit_text_or_send(client, chat_id, edit_message_id, text, markup)


@Client.on_message(filters.command("broadcast") & filters.private)
async def main_broadcast_command(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    await bot_db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    if message.from_user.id in Config.OWNER_IDS and message.reply_to_message is not None:
        from Main.Plugins.owner_admin import open_owner_broadcast_menu

        await open_owner_broadcast_menu(client, message)
        return
    await _show_broadcast_entry(client, message.from_user.id, message.chat.id)


@Client.on_callback_query(filters.regex(r"^menu:broadcast$"))
async def menu_broadcast_callback(client: Client, query: types.CallbackQuery) -> None:
    await raw_answer_callback(client, query.id)
    await _show_broadcast_entry(
        client,
        query.from_user.id,
        query.message.chat.id,
        edit_message_id=query.message.id,
    )


@Client.on_callback_query(filters.regex(r"^peers:menu:(.+)$"))
async def peers_menu_callback(client: Client, query: types.CallbackQuery) -> None:
    cid = query.data.split(":", 2)[2]
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    await peer_tracker.flush()
    counts = await bot_db.get_client_peer_counts(cid)
    can_sync = client_manager.get_runtime(cid) is not None
    await raw_answer_callback(client, query.id)
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            rich_message=_build_peers_summary_rich(doc, counts),
            reply_markup=peers_menu_keyboard(cid, counts, can_sync_dialogs=can_sync),
        )
    except MessageNotModified:
        pass


@Client.on_callback_query(filters.regex(r"^peers:sync:(.+)$"))
async def peers_sync_callback(client: Client, query: types.CallbackQuery) -> None:
    cid = query.data.split(":", 2)[2]
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    ok, msg = await client_manager.sync_mtproto_dialogs(cid, limit=100)
    await raw_answer_callback(client, query.id, msg, alert=not ok)
    counts = await bot_db.get_client_peer_counts(cid)
    can_sync = client_manager.get_runtime(cid) is not None
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            rich_message=_build_peers_summary_rich(doc, counts),
            reply_markup=peers_menu_keyboard(cid, counts, can_sync_dialogs=can_sync),
        )
    except MessageNotModified:
        pass


@Client.on_callback_query(filters.regex(r"^peers:list:([^:]+):(user|group|channel):(\d+)$"))
async def peers_list_callback(client: Client, query: types.CallbackQuery) -> None:
    _, _, cid, peer_type, page_str = query.data.split(":")
    page = max(0, int(page_str))
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return

    await peer_tracker.flush()
    items = await bot_db.get_client_peers(
        cid,
        peer_type=peer_type,
        skip=page * _PAGE_SIZE,
        limit=_PAGE_SIZE + 1,
    )
    has_next = len(items) > _PAGE_SIZE
    page_items = items[:_PAGE_SIZE]

    await raw_answer_callback(client, query.id)
    title_map = {"user": "Users", "group": "Groups", "channel": "Channels"}
    header = f"📋 **{title_map[peer_type]}** for `{doc.get('name', cid)}` (Page {page + 1})"
    if not page_items:
        header += "\n\n_No tracked peers in this category yet._"

    await _safe_edit_text_or_send(
        client,
        query.message.chat.id,
        query.message.id,
        header,
        peers_list_keyboard(cid, peer_type, page_items, page, has_next),
    )


@Client.on_callback_query(filters.regex(r"^peers:del:([^:]+):(user|group|channel):(\d+):(-?\d+)$"))
async def peers_delete_callback(client: Client, query: types.CallbackQuery) -> None:
    _, _, cid, peer_type, page_str, chat_id_str = query.data.split(":")
    page = max(0, int(page_str))
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return

    await bot_db.delete_client_peer(cid, int(chat_id_str))
    await raw_answer_callback(client, query.id, "Peer removed")

    items = await bot_db.get_client_peers(
        cid,
        peer_type=peer_type,
        skip=page * _PAGE_SIZE,
        limit=_PAGE_SIZE + 1,
    )
    has_next = len(items) > _PAGE_SIZE
    page_items = items[:_PAGE_SIZE]
    title_map = {"user": "Users", "group": "Groups", "channel": "Channels"}
    header = f"📋 **{title_map[peer_type]}** for `{doc.get('name', cid)}` (Page {page + 1})"
    if not page_items:
        header += "\n\n_No tracked peers in this category._"
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text=header,
            reply_markup=peers_list_keyboard(cid, peer_type, page_items, page, has_next),
        )
    except MessageNotModified:
        pass


@Client.on_callback_query(filters.regex(r"^peers:clear_(ask|yes):(.+)$"))
async def peers_clear_callback(client: Client, query: types.CallbackQuery) -> None:
    _, mode, cid = query.data.split(":", 2)
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    if mode == "ask":
        await raw_answer_callback(client, query.id)
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text=f"⚠️ Clear **all** tracked users, groups, and channels for **{doc.get('name', cid)}**?",
            reply_markup=types.InlineKeyboardMarkup(
                [
                    [
                        types.InlineKeyboardButton(
                            "✅ Confirm Clear",
                            callback_data=f"peers:clear_yes:{cid}",
                        ),
                        types.InlineKeyboardButton(
                            "↩️ Cancel",
                            callback_data=f"peers:menu:{cid}",
                        ),
                    ]
                ]
            ),
        )
        return

    peer_tracker.forget_client(cid)
    deleted = await bot_db.delete_all_client_peers(cid)
    await raw_answer_callback(client, query.id, f"Cleared {deleted} peers.")
    counts = await bot_db.get_client_peer_counts(cid)
    can_sync = client_manager.get_runtime(cid) is not None
    await client.edit_message_text(
        chat_id=query.message.chat.id,
        message_id=query.message.id,
        rich_message=_build_peers_summary_rich(doc, counts),
        reply_markup=peers_menu_keyboard(cid, counts, can_sync_dialogs=can_sync),
    )


@Client.on_callback_query(filters.regex(r"^bc:pick:(.+)$"))
async def broadcast_pick_callback(client: Client, query: types.CallbackQuery) -> None:
    cid = query.data.split(":", 2)[2]
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    await peer_tracker.flush()
    counts = await bot_db.get_client_peer_counts(cid)
    await raw_answer_callback(client, query.id)
    await _safe_edit_text_or_send(
        client,
        query.message.chat.id,
        query.message.id,
        (
            f"📣 **Broadcast Target — {doc.get('name', cid)}**\n\n"
            "Choose who should receive your broadcast:"
        ),
        broadcast_target_keyboard(cid, counts),
    )


@Client.on_callback_query(filters.regex(r"^bc:ask:([^:]+):(all|user|group|channel)$"))
async def broadcast_ask_callback(client: Client, query: types.CallbackQuery) -> None:
    _, _, cid, target = query.data.split(":")
    doc = await bot_db.get_client(cid)
    if not _is_authorized(doc, query.from_user.id):
        await raw_answer_callback(client, query.id, "Client not found.", alert=True)
        return
    assert doc is not None

    _BROADCAST_DRAFTS[query.from_user.id] = _BroadcastDraft(
        client_id=cid,
        target=target,
        message_id=query.message.id,
    )
    await raw_answer_callback(client, query.id)
    label = "All Users, Groups & Channels" if target == "all" else f"{target.title()}s"
    await _safe_edit_text_or_send(
        client,
        query.message.chat.id,
        query.message.id,
        (
            f"✍️ **Send your broadcast message now**\n\n"
            f"• **Client:** `{doc.get('name', cid)}`\n"
            f"• **Target:** `{label}`\n\n"
            "Send the text message you want to deliver:"
        ),
        broadcast_cancel_keyboard(),
    )


@Client.on_callback_query(filters.regex(r"^bc:cancel$"))
async def broadcast_cancel_callback(client: Client, query: types.CallbackQuery) -> None:
    _BROADCAST_DRAFTS.pop(query.from_user.id, None)
    await raw_answer_callback(client, query.id, "Broadcast cancelled")
    try:
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text="❌ Broadcast cancelled.",
            reply_markup=main_menu_keyboard(),
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^bc:conf1$"))
async def broadcast_confirm_stage1_callback(client: Client, query: types.CallbackQuery) -> None:
    draft = _BROADCAST_DRAFTS.get(query.from_user.id)
    if draft is None or draft.stage != 1:
        await raw_answer_callback(client, query.id, "No active draft to confirm.", alert=True)
        return
    await raw_answer_callback(client, query.id)
    draft.stage = 2
    doc = await bot_db.get_client(draft.client_id)
    cname = (doc.get("name") if doc else None) or draft.client_id
    label = "All Users, Groups & Channels" if draft.target == "all" else f"{draft.target.title()}s"

    card = (
        "🚨 <b>FINAL DOUBLE-CONFIRMATION (Stage 2 of 2)</b>\n\n"
        "<blockquote>"
        f"Are you <b>ABSOLUTELY SURE</b> you want to broadcast now?\n\n"
        f"• <b>Client:</b> <code>{cname}</code>\n"
        f"• <b>Audience:</b> {label} (<code>{draft.estimated_count}</code> chats)\n"
        "• <b>Notice:</b> This action cannot be recalled once dispatched."
        "</blockquote>\n\n"
        "Press below to confirm dispatch:"
    )
    keyboard = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "🚀 YES, DISPATCH BROADCAST NOW",
                    callback_data="bc:conf2",
                )
            ],
            [
                types.InlineKeyboardButton(
                    "❌ Abort & Cancel",
                    callback_data="bc:cancel",
                )
            ],
        ]
    )
    await client.edit_message_text(
        chat_id=query.message.chat.id,
        message_id=query.message.id,
        text=card,
        reply_markup=keyboard,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_callback_query(filters.regex(r"^bc:conf2$"))
async def broadcast_confirm_stage2_callback(client: Client, query: types.CallbackQuery) -> None:
    draft = _BROADCAST_DRAFTS.pop(query.from_user.id, None)
    if draft is None or draft.stage != 2:
        await raw_answer_callback(client, query.id, "No active draft ready for dispatch.", alert=True)
        return
    await raw_answer_callback(client, query.id, "Dispatching broadcast...")

    doc = await bot_db.get_client(draft.client_id)
    if not _is_authorized(doc, query.from_user.id):
        await client.edit_message_text(
            chat_id=query.message.chat.id,
            message_id=query.message.id,
            text="Client not found or authorization failed.",
            reply_markup=main_menu_keyboard(),
        )
        return
    assert doc is not None

    await client.edit_message_text(
        chat_id=query.message.chat.id,
        message_id=query.message.id,
        text="📣 <b>Broadcasting in progress... please wait.</b>",
        parse_mode=enums.ParseMode.HTML,
    )

    peer_filter = None if draft.target == "all" else draft.target
    rt = client_manager.get_runtime(draft.client_id)

    res = await broadcast_to_client_audience(
        client_id=draft.client_id,
        client_doc=doc,
        peer_type=peer_filter,
        text=draft.text,
        source_bot=client,
        source_message=draft.source_message,
        mtproto_client=rt.client if rt is not None else None,
    )
    await client.edit_message_text(
        chat_id=query.message.chat.id,
        message_id=query.message.id,
        text=(
            f"✅ <b>Broadcast Complete</b> ({res.elapsed_seconds}s)\n\n"
            f"• <b>Total Targeted:</b> <code>{res.total}</code>\n"
            f"• <b>Successfully Sent:</b> <code>{res.sent}</code>\n"
            f"• <b>Failed:</b> <code>{res.failed}</code>\n"
            f"• <b>Pruned:</b> <code>{res.pruned}</code>"
        ),
        parse_mode=enums.ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
    )


@Client.on_message(filters.private & ~filters.regex(r"^(/|stats$)"), group=2)
async def broadcast_message_receiver(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    user_id = message.from_user.id
    if client_manager.get_wizard(user_id) is not None:
        return

    draft = _BROADCAST_DRAFTS.get(user_id)
    if draft is None or draft.stage != 0:
        return

    doc = await bot_db.get_client(draft.client_id)
    if not _is_authorized(doc, user_id):
        _BROADCAST_DRAFTS.pop(user_id, None)
        await message.reply_text("Client not found.", reply_markup=main_menu_keyboard())
        return
    assert doc is not None

    broadcast_text = message.text or message.caption or ""
    if not broadcast_text.strip() and not message.media:
        await message.reply_text(
            "Please send a text message (or media with caption) to broadcast.",
            reply_markup=broadcast_cancel_keyboard(),
        )
        return

    # Calculate estimated audience count
    await peer_tracker.flush()
    counts = await bot_db.get_client_peer_counts(draft.client_id)
    count = counts.get("total" if draft.target == "all" else draft.target, 0)

    draft.text = broadcast_text
    draft.source_message = message
    draft.estimated_count = count
    draft.stage = 1

    cname = doc.get("name") or draft.client_id
    label = "All Users, Groups & Channels" if draft.target == "all" else f"{draft.target.title()}s"
    snippet = broadcast_text[:120] + ("..." if len(broadcast_text) > 120 else "")
    if not snippet:
        snippet = f"[Media: {message.media.value if hasattr(message.media, 'value') else 'Attachment'}]"

    card = (
        "⚠️ <b>BROADCAST REVIEW & CONFIRMATION (Stage 1 of 2)</b>\n\n"
        "<blockquote>"
        f"• <b>Source Client:</b> <code>{html.escape(cname)}</code> (<code>{draft.client_id}</code>)\n"
        f"• <b>Target Audience:</b> {label}\n"
        f"• <b>Recipients:</b> <code>{count}</code> tracked chats\n"
        f"• <b>Message Preview:</b> <i>{html.escape(snippet)}</i>"
        "</blockquote>\n\n"
        "⚠️ <i>Please verify the content and target before proceeding.</i>"
    )
    keyboard = types.InlineKeyboardMarkup(
        [
            [
                types.InlineKeyboardButton(
                    "✅ Proceed to Final Confirmation",
                    callback_data="bc:conf1",
                )
            ],
            [
                types.InlineKeyboardButton(
                    "❌ Cancel Broadcast",
                    callback_data="bc:cancel",
                )
            ],
        ]
    )
    await message.reply_text(card, reply_markup=keyboard, parse_mode=enums.ParseMode.HTML)
