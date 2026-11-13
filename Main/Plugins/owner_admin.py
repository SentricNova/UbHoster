"""Main-Bot Owner-only admin commands (/restart, /cleancache, /maintaince, /forcejoin, global /broadcast)."""

from __future__ import annotations

import asyncio
import html
import logging
import os
import sys
import time
import tracemalloc
from typing import Any

import psutil

from pyrogram import Client, enums, filters, types
from pyrogram.errors import FloodWait, MessageNotModified

from config import Config
from Main.Client.Bot import raw_answer_callback
from Main.Client.Mongo import bot_db
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_manager import client_manager
from Main.utils.error_handler import safe_handler
from Main.utils.keyboard import (
    forcejoin_keyboard,
    owner_broadcast_keyboard,
    owner_broadcast_runtime_keyboard,
)
from Main.utils.peer_tracker import peer_tracker

log = logging.getLogger(__name__)

_OWNER_BC_DRAFTS: dict[int, dict[str, Any]] = {}
_OWNER_BC_TASK: asyncio.Task | None = None
_OWNER_BC_CTRL: dict[str, Any] = {"paused": False, "stop": False}

_SPEED_DELAYS = {"slow": 1.0, "medium": 0.25, "fast": 0.05}


def _is_owner(user_id: int | None) -> bool:
    return bool(user_id and user_id in Config.OWNER_IDS)


def _owner_filter(_, __, update: types.Message | types.CallbackQuery) -> bool:
    u = getattr(update, "from_user", None)
    return bool(u and u.id in Config.OWNER_IDS)


owner_only = filters.create(_owner_filter)


# ---------------------------------------------------------------------------
# /restart & /cleancache & /maintaince
# ---------------------------------------------------------------------------

@Client.on_message(filters.command("restart") & owner_only)
@safe_handler("owner_restart")
async def owner_restart_cmd(client: Client, message: types.Message) -> None:
    await message.reply_text("🔄 **Restarting hosting engine...**", quote=True)
    try:
        await client_manager.stop_all()
    except Exception:
        pass
    os.execv(sys.executable, [sys.executable, "-m", "Main"])


@Client.on_message(filters.command("cleancache") & owner_only)
@safe_handler("owner_cleancache")
async def owner_cleancache_cmd(client: Client, message: types.Message) -> None:
    res = await bot_db.clean_lmdb_cache()
    await message.reply_text(
        "🧹 **LMDB Cache Cleaned**\n"
        f"• Force-join entries purged: `{res.get('fj_removed', 0)}`\n"
        f"• Username peer entries purged: `{res.get('uname_removed', 0)}`\n"
        f"• Synced: `{res.get('users_synced', 0)}` users | `{res.get('clients_synced', 0)}` clients",
        quote=True,
    )


@Client.on_message(filters.command(["memprof", "ram"]) & owner_only)
@safe_handler("owner_memprof")
async def owner_memprof_cmd(client: Client, message: types.Message) -> None:
    proc = psutil.Process(os.getpid())
    mem = proc.memory_info()
    rss_mb = mem.rss / (1024 * 1024)
    vms_mb = mem.vms / (1024 * 1024)

    lines = [
        "🧠 <b>Memory Profiler (Per-File RAM Usage)</b>",
        f"• <b>Process RSS:</b> <code>{rss_mb:.2f} MB</code> | <b>VMS:</b> <code>{vms_mb:.2f} MB</code>",
    ]
    if tracemalloc.is_tracing():
        cur_b, peak_b = tracemalloc.get_traced_memory()
        lines.append(
            f"• <b>Traced Heap:</b> <code>{cur_b / (1024 * 1024):.2f} MB</code> "
            f"(peak: <code>{peak_b / (1024 * 1024):.2f} MB</code>)"
        )
        snap = tracemalloc.take_snapshot()
        stats = snap.statistics("filename")
        proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        proj_stats = [s for s in stats if proj_root.lower() in s.traceback[0].filename.lower()]
        lines.append("\n<b>Top Project Files:</b>")
        for idx, st in enumerate(proj_stats[:15], start=1):
            rel = os.path.relpath(st.traceback[0].filename, proj_root).replace("\\", "/")
            kb = st.size / 1024.0
            lines.append(f"<code>{idx:2d}.</code> <code>{kb:7.1f} KB</code> ({st.count} objs) — <code>{html.escape(rel)}</code>")

        lines.append("\n<b>Top Overall Modules:</b>")
        for idx, st in enumerate(stats[:8], start=1):
            fname = st.traceback[0].filename.replace("\\", "/")
            short = "/".join(fname.split("/")[-3:])
            mb = st.size / (1024 * 1024)
            lines.append(f"<code>{idx:2d}.</code> <code>{mb:6.2f} MB</code> ({st.count} objs) — <code>{html.escape(short)}</code>")
    else:
        lines.append("<i>tracemalloc is not active.</i>")

    await message.reply_text("\n".join(lines), parse_mode=enums.ParseMode.HTML, quote=True)


@Client.on_message(filters.command(["maintaince", "maintenance"]) & owner_only)
@safe_handler("owner_maintenance")
async def owner_maintenance_cmd(client: Client, message: types.Message) -> None:
    args = (message.text or "").split(None, 2)
    cur = await bot_db.get_maintenance()
    if len(args) == 1:
        new_state = not bool(cur.get("enabled", False))
        reason = cur.get("reason", "")
    else:
        arg1 = args[1].lower()
        if arg1 in ("on", "true", "1", "enable"):
            new_state = True
            reason = args[2] if len(args) > 2 else cur.get("reason", "")
        elif arg1 in ("off", "false", "0", "disable"):
            new_state = False
            reason = ""
        else:
            new_state = True
            reason = (message.text or "").split(None, 1)[1]

    await bot_db.set_maintenance(new_state, reason)
    status_str = "🛠 **ON** (only owners can use the bot)" if new_state else "🟢 **OFF** (normal access restored)"
    extra = f"\nReason: `{reason}`" if reason else ""
    await message.reply_text(f"Maintenance Mode is now {status_str}{extra}", quote=True)


# ---------------------------------------------------------------------------
# /forcejoin
# ---------------------------------------------------------------------------

def _fj_text(scope: str, chats: list[dict[str, Any]]) -> str:
    lines = [
        "🔒 **Force Join Configuration**",
        "",
        f"• **Scope:** `{scope}`",
        f"• **Required Chats ({len(chats)}):**",
    ]
    if not chats:
        lines.append("  _None configured._")
    else:
        for c in chats:
            lines.append(f"  • {c.get('title') or c.get('chat_id')} (`{c.get('chat_id')}`)")
    return "\n".join(lines)


@Client.on_message(filters.command("forcejoin") & owner_only)
@safe_handler("owner_forcejoin")
async def owner_forcejoin_cmd(client: Client, message: types.Message) -> None:
    cfg = await bot_db.get_force_join()
    scope = str(cfg.get("scope") or "off")
    chats = list(cfg.get("chats") or [])
    await message.reply_text(
        _fj_text(scope, chats),
        reply_markup=forcejoin_keyboard(scope, chats),
        parse_mode=enums.ParseMode.MARKDOWN,
        quote=True,
    )


@Client.on_callback_query(filters.regex(r"^fj:") & owner_only)
@safe_handler("owner_forcejoin_cb")
async def owner_forcejoin_cb(client: Client, cq: types.CallbackQuery) -> None:
    parts = cq.data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""

    cfg = await bot_db.get_force_join()
    scope = str(cfg.get("scope") or "off")
    chats = list(cfg.get("chats") or [])

    if action == "scope" and arg in ("off", "main", "all_bots"):
        scope = arg
        await bot_db.set_force_join(scope=scope, chats=chats)
        await raw_answer_callback(client, cq.id, f"Scope set to {scope}")
    elif action == "clear":
        chats = []
        await bot_db.set_force_join(scope=scope, chats=chats)
        await raw_answer_callback(client, cq.id, "Cleared all force-join chats.")
    elif action == "del" and arg:
        chats = [c for c in chats if str(c.get("chat_id")) != arg]
        await bot_db.set_force_join(scope=scope, chats=chats)
        await raw_answer_callback(client, cq.id, f"Removed {arg}")
    elif action == "add":
        await raw_answer_callback(client, cq.id)
        await bot_db.set_state(
            cq.from_user.id,
            {"step": "fj_add", "chat_id": cq.message.chat.id, "message_id": cq.message.id},
        )
        await cq.message.reply_text(
            "➕ Send the `@username` or numeric `-100...` chat ID to add to Force Join.\n"
            "Make sure the Main Bot is an admin in that chat. Send `/cancel` to abort."
        )
        return
    else:
        await raw_answer_callback(client, cq.id)

    try:
        await cq.message.edit_text(
            _fj_text(scope, chats),
            reply_markup=forcejoin_keyboard(scope, chats),
            parse_mode=enums.ParseMode.MARKDOWN,
        )
    except MessageNotModified:
        pass


@Client.on_message(filters.private & filters.text & owner_only, group=4)
@safe_handler("owner_fj_input")
async def owner_fj_text_input(client: Client, message: types.Message) -> None:
    user = message.from_user
    if not user:
        return
    state = await bot_db.get_state(user.id)
    if not state or state.get("step") != "fj_add":
        return
    text = (message.text or "").strip()
    if text.lower() == "/cancel":
        await bot_db.clear_state(user.id)
        await message.reply_text("❌ Force-join add cancelled.")
        return
    if text.startswith("/"):
        return

    target: int | str = int(text) if text.lstrip("-").isdigit() else text
    try:
        chat = await client.get_chat(target)
    except Exception as exc:
        await message.reply_text(f"❌ Could not access `{text}`: `{exc}`")
        return

    invite_link = getattr(chat, "invite_link", None)
    if not invite_link and not getattr(chat, "username", None):
        try:
            invite_link = await client.export_chat_invite_link(chat.id)
        except Exception:
            invite_link = None

    cfg = await bot_db.get_force_join()
    scope = str(cfg.get("scope") or "off")
    chats = [c for c in list(cfg.get("chats") or []) if str(c.get("chat_id")) != str(chat.id)]
    chats.append(
        {
            "chat_id": chat.id,
            "title": getattr(chat, "title", None) or str(chat.id),
            "username": getattr(chat, "username", None),
            "invite_link": invite_link,
        }
    )
    await bot_db.set_force_join(scope=scope, chats=chats)
    await bot_db.clear_state(user.id)
    await message.reply_text(
        f"✅ Added **{chat.title or chat.id}** (`{chat.id}`) to Force Join!\n\n" + _fj_text(scope, chats),
        reply_markup=forcejoin_keyboard(scope, chats),
        parse_mode=enums.ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# Owner Global /broadcast
# ---------------------------------------------------------------------------

def _obc_summary(draft: dict[str, Any]) -> str:
    return (
        "📣 **Owner Global Broadcast**\n\n"
        f"• **Mode:** `{draft['mode']}`\n"
        f"• **Sender:** `{draft['sender']}`\n"
        f"• **Speed:** `{draft['speed']}`\n"
        f"• **Auto-Delete:** `{draft['delete_after']}s`\n"
        f"• **Target:** `{draft['target']}`\n"
        f"• **Source Message:** `{draft['src_chat_id']}:{draft['src_msg_id']}`\n\n"
        "Configure options below, then tap **Start Broadcast**."
    )


async def open_owner_broadcast_menu(client: Client, message: types.Message) -> None:
    src = message.reply_to_message
    if src is None:
        await message.reply_text(
            "Reply to any message with `/broadcast` to open the Owner Global Broadcast panel.",
            quote=True,
        )
        return
    draft = {
        "mode": "copy",
        "sender": "main",
        "speed": "medium",
        "delete_after": 0,
        "target": "all",
        "src_chat_id": src.chat.id,
        "src_msg_id": src.id,
    }
    _OWNER_BC_DRAFTS[message.from_user.id] = draft
    await message.reply_text(
        _obc_summary(draft),
        reply_markup=owner_broadcast_keyboard(draft),
        parse_mode=enums.ParseMode.MARKDOWN,
        quote=True,
    )


async def _send_via_sender(
    main_bot: Client,
    sender_id: str,
    target_chat_id: int,
    src_chat_id: int,
    src_msg_id: int,
    mode: str,
) -> int | None:
    if sender_id == "main":
        if mode == "forward":
            sent = await main_bot.forward_messages(target_chat_id, src_chat_id, src_msg_id)
        else:
            sent = await main_bot.copy_message(target_chat_id, src_chat_id, src_msg_id)
        return getattr(sent, "id", None)

    rt = client_manager.get_runtime(sender_id)
    if rt is not None:
        # Userbot/MTProto client: copy_message or forward_messages from main_bot?
        # Copy text/caption cleanly if cross-client; forward only works if client can see src_chat_id.
        src_msg = await main_bot.get_messages(src_chat_id, src_msg_id)
        if mode == "forward":
            try:
                sent = await rt.client.forward_messages(target_chat_id, src_chat_id, src_msg_id)
                return getattr(sent, "id", None)
            except Exception:
                pass
        if src_msg and src_msg.text:
            sent = await rt.client.send_message(target_chat_id, src_msg.text, entities=src_msg.entities)
            return getattr(sent, "id", None)
        if src_msg and src_msg.caption:
            sent = await rt.client.send_message(target_chat_id, src_msg.caption, entities=src_msg.caption_entities)
            return getattr(sent, "id", None)
        return None

    wb = bot_webhook_manager.get_entry(sender_id)
    if wb is not None:
        src_msg = await main_bot.get_messages(src_chat_id, src_msg_id)
        text = (src_msg.text or src_msg.caption or "") if src_msg else ""
        if text:
            res = await bot_webhook_manager.call_api(
                sender_id, "sendMessage", {"chat_id": target_chat_id, "text": text}
            )
            return res.get("message_id") if isinstance(res, dict) else None
    return None


async def _collect_broadcast_jobs(
    sender_opt: str,
    target_opt: str,
) -> list[tuple[str, int]]:
    jobs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()
    peer_types = None if target_opt == "all" else {target_opt}

    if sender_opt in ("main", "all") and target_opt in ("user", "all"):
        cursor = bot_db.engine.mongo_db[bot_db.USERS_COL].find({}, {"_id": 0, "user_id": 1})
        async for doc in cursor:
            uid = doc.get("user_id")
            if uid and ("main", int(uid)) not in seen:
                seen.add(("main", int(uid)))
                jobs.append(("main", int(uid)))

    if sender_opt in ("bots", "ubs", "all"):
        all_clients = await bot_db.engine.find_many_mongo(bot_db.CLIENTS_COL, {}, limit=5000)
        for cdoc in all_clients:
            cid = str(cdoc.get("client_id"))
            ctype = str(cdoc.get("client_type") or "bot")
            if sender_opt == "bots" and ctype != "bot":
                continue
            if sender_opt == "ubs" and ctype != "userbot":
                continue
            if cid not in client_manager.running_ids:
                continue
            peers = await peer_tracker.list_peers(cid, limit=5000)
            for p in peers:
                if peer_types and p.get("peer_type") not in peer_types:
                    continue
                chat_id = int(p["chat_id"])
                if (cid, chat_id) not in seen:
                    seen.add((cid, chat_id))
                    jobs.append((cid, chat_id))
    return jobs


async def _run_owner_broadcast(
    main_bot: Client,
    status_msg: types.Message,
    draft: dict[str, Any],
) -> None:
    global _OWNER_BC_CTRL
    _OWNER_BC_CTRL = {"paused": False, "stop": False}
    jobs = await _collect_broadcast_jobs(draft["sender"], draft["target"])
    total = len(jobs)
    if total == 0:
        await status_msg.edit_text("⚠️ No matching recipients found for this broadcast configuration.")
        return

    delay = _SPEED_DELAYS.get(draft["speed"], 0.25)
    del_after = int(draft.get("delete_after") or 0)
    delete_at = (time.time() + del_after) if del_after > 0 else 0.0

    sent_ok = 0
    failed = 0
    batch_records: list[dict[str, Any]] = []

    await status_msg.edit_text(
        f"🚀 **Owner Broadcast Running** (`0/{total}`)",
        reply_markup=owner_broadcast_runtime_keyboard(paused=False, has_sent=False),
    )

    for idx, (sender_id, chat_id) in enumerate(jobs, start=1):
        while _OWNER_BC_CTRL["paused"] and not _OWNER_BC_CTRL["stop"]:
            await asyncio.sleep(0.5)
        if _OWNER_BC_CTRL["stop"]:
            break

        try:
            mid = await _send_via_sender(
                main_bot,
                sender_id,
                chat_id,
                draft["src_chat_id"],
                draft["src_msg_id"],
                draft["mode"],
            )
            if mid is not None:
                sent_ok += 1
                batch_records.append(
                    {
                        "sender_id": sender_id,
                        "chat_id": chat_id,
                        "message_id": int(mid),
                        "expires_at": int(delete_at),
                    }
                )
            else:
                failed += 1
        except FloodWait as fw:
            await asyncio.sleep(min(int(fw.value) + 1, 60))
        except Exception:
            failed += 1

        if len(batch_records) >= 50:
            await bot_db.save_broadcast_sent_msgs(batch_records)
            batch_records.clear()

        if idx % 20 == 0:
            try:
                await status_msg.edit_text(
                    f"🚀 **Owner Broadcast Running**\n"
                    f"• Progress: `{idx}/{total}`\n"
                    f"• Sent: `{sent_ok}` | Failed: `{failed}`",
                    reply_markup=owner_broadcast_runtime_keyboard(
                        paused=_OWNER_BC_CTRL["paused"],
                        has_sent=sent_ok > 0,
                    ),
                )
            except Exception:
                pass

        await asyncio.sleep(delay)

    if batch_records:
        await bot_db.save_broadcast_sent_msgs(batch_records)

    state_lbl = "🛑 Stopped" if _OWNER_BC_CTRL["stop"] else "✅ Completed"
    try:
        await status_msg.edit_text(
            f"{state_lbl} **Owner Broadcast**\n\n"
            f"• Total Targets: `{total}`\n"
            f"• Sent: `{sent_ok}`\n"
            f"• Failed: `{failed}`",
            reply_markup=owner_broadcast_runtime_keyboard(paused=False, has_sent=sent_ok > 0),
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^obc:") & owner_only)
@safe_handler("owner_broadcast_cb")
async def owner_broadcast_cb(client: Client, cq: types.CallbackQuery) -> None:
    global _OWNER_BC_TASK
    uid = cq.from_user.id
    parts = cq.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        _OWNER_BC_DRAFTS.pop(uid, None)
        await raw_answer_callback(client, cq.id, "Cancelled.")
        await cq.message.edit_text("❌ Owner broadcast cancelled.")
        return

    if action == "pause":
        _OWNER_BC_CTRL["paused"] = True
        await raw_answer_callback(client, cq.id, "Paused.")
        try:
            await cq.message.edit_reply_markup(
                owner_broadcast_runtime_keyboard(paused=True, has_sent=True)
            )
        except MessageNotModified:
            pass
        return

    if action == "resume":
        _OWNER_BC_CTRL["paused"] = False
        await raw_answer_callback(client, cq.id, "Resumed.")
        try:
            await cq.message.edit_reply_markup(
                owner_broadcast_runtime_keyboard(paused=False, has_sent=True)
            )
        except MessageNotModified:
            pass
        return

    if action == "stop":
        _OWNER_BC_CTRL["stop"] = True
        await raw_answer_callback(client, cq.id, "Stopping...")
        return

    if action == "delall":
        await raw_answer_callback(client, cq.id, "Deleting sent broadcast messages...")
        records = await bot_db.get_all_broadcast_msgs()
        deleted = 0
        oids = []
        for rec in records:
            oids.append(rec["_id"])
            sid = str(rec.get("sender_id") or "main")
            cid = int(rec["chat_id"])
            mid = int(rec["message_id"])
            try:
                if sid == "main":
                    await client.delete_messages(cid, mid)
                else:
                    rt = client_manager.get_runtime(sid)
                    if rt is not None:
                        await rt.client.delete_messages(cid, mid)
                    elif bot_webhook_manager.get_entry(sid) is not None:
                        await bot_webhook_manager.call_api(
                            sid, "deleteMessage", {"chat_id": cid, "message_id": mid}
                        )
                deleted += 1
            except Exception:
                pass
        if oids:
            await bot_db.delete_broadcast_msg_records(oids)
        await cq.message.reply_text(f"🗑 Deleted `{deleted}/{len(records)}` stored broadcast messages.")
        return

    draft = _OWNER_BC_DRAFTS.get(uid)
    if not draft:
        await raw_answer_callback(client, cq.id, "Draft expired. Reply /broadcast again.", alert=True)
        return

    if action == "opt" and len(parts) >= 4:
        field, val = parts[2], parts[3]
        if field == "mode":
            draft["mode"] = val
        elif field == "sender":
            draft["sender"] = val
        elif field == "speed":
            draft["speed"] = val
        elif field == "del":
            draft["delete_after"] = int(val)
        elif field == "target":
            draft["target"] = val
        await raw_answer_callback(client, cq.id)
        try:
            await cq.message.edit_text(
                _obc_summary(draft),
                reply_markup=owner_broadcast_keyboard(draft),
                parse_mode=enums.ParseMode.MARKDOWN,
            )
        except MessageNotModified:
            pass
        return

    if action == "start":
        if _OWNER_BC_TASK and not _OWNER_BC_TASK.done():
            await raw_answer_callback(client, cq.id, "A global broadcast is already running.", alert=True)
            return
        await raw_answer_callback(client, cq.id, "Starting broadcast...")
        _OWNER_BC_TASK = asyncio.create_task(_run_owner_broadcast(client, cq.message, dict(draft)))
