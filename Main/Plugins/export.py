"""Owner-only /export command on the Main Hosting Bot to export client session strings."""

from __future__ import annotations

from pyrogram import Client, filters, types

from Main.Client.Bot import raw_answer_callback
from Main.Client.Mongo import bot_db
from Main.utils.client_manager import client_manager
from Main.utils.keyboard import (
    export_delete_keyboard,
    export_picker_keyboard,
    main_menu_keyboard,
)


async def _resolve_session_string(doc: dict) -> str:
    session_str = doc.get("session_string") or ""
    if session_str:
        return session_str
    rt = client_manager.get_runtime(doc["client_id"])
    if rt is not None:
        try:
            return await rt.client.export_session_string()
        except Exception:
            pass
    return ""


@Client.on_message(filters.command("export"))
async def export_command(client: Client, message: types.Message) -> None:
    if message.from_user is None:
        return
    if message.chat.type.value != "private":
        await message.reply_text(
            "For security, please use `/export` in my private chat.",
            quote=True,
        )
        return

    user_id = message.from_user.id
    await bot_db.ensure_user(user_id, message.from_user.username, message.from_user.first_name)
    clients = await bot_db.get_user_clients(user_id)
    if not clients:
        await message.reply_text(
            "You have no connected bots or accounts yet. Use /addacc or /addbot first.",
            reply_markup=main_menu_keyboard(),
            quote=True,
        )
        return

    await message.reply_text(
        "🔑 **Export Session String**\n\n"
        "Select one of your connected accounts or bots to export its session string:",
        reply_markup=export_picker_keyboard(clients),
        quote=True,
    )


@Client.on_callback_query(filters.regex(r"^exp:get:(.+)$"))
async def export_get_callback(client: Client, query: types.CallbackQuery) -> None:
    if query.from_user is None or query.message is None:
        return
    cid = query.data.split(":", 2)[2]
    doc = await bot_db.get_client(cid)

    # Strict owner-only check for exporting raw credentials
    if doc is None or doc.get("owner_id") != query.from_user.id:
        await raw_answer_callback(client, query.id, "Not authorized or client not found.", alert=True)
        return

    session_str = await _resolve_session_string(doc)
    if not session_str:
        await raw_answer_callback(
            client,
            query.id,
            "No Pyrogram/Kurigram session string stored for this webhook bot.",
            alert=True,
        )
        return

    await raw_answer_callback(client, query.id, "Session exported")
    uname = f"@{doc['username']}" if doc.get("username") else str(doc.get("tg_id"))
    await client.send_message(
        chat_id=query.message.chat.id,
        text=(
            f"🔑 **Session String — {doc.get('name', cid)}** (`{uname}`)\n"
            "⚠️ _Do not share this with anyone! Delete this message after copying._\n\n"
            f"<pre><code>{session_str}</code></pre>"
        ),
        reply_markup=export_delete_keyboard(cid),
    )


@Client.on_callback_query(filters.regex(r"^exp:delmsg$"))
async def export_delete_msg_callback(client: Client, query: types.CallbackQuery) -> None:
    await raw_answer_callback(client, query.id, "Deleted")
    try:
        await query.message.delete()
    except Exception:
        pass
