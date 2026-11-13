""".scrape (/scrape) & .send (reply to scraped file) plugins for hosted userbots."""

from __future__ import annotations

import asyncio
import json
import re
from io import BytesIO
from typing import Any

from pyrogram import enums
from pyrogram.errors import FloodWait, RPCError

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import (
    check_client_access,
    log_to_client_logger,
    register_client_task,
    should_stop,
)
from Main.utils.error_handler import run_safe

_MSG_LINK_RE = re.compile(r"(?:https?://)?t\.me/(?:c/)?([^/\s]+)/(\d+)")


def _classify_status(status: enums.UserStatus | None) -> str:
    if status in (enums.UserStatus.ONLINE, enums.UserStatus.RECENTLY):
        return "active"
    return "offline"


@userbot_command("scrape")
async def ub_scrape(ctx: RawContext) -> None:
    """Scrape group/channel members into a JSON file sent to Saved Messages & logger group."""
    allowed, doc, err = await check_client_access(ctx, feature="scrape", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return
    if ctx.chat_type == "private":
        await ctx.edit_raw("Run .scrape inside a group or channel.")
        return

    limit = 0
    if ctx.args.strip().isdigit():
        limit = max(1, int(ctx.args.strip()))

    await ctx.edit_raw("🔍 Scraping members... please wait.")

    async def _collect() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        async for m in ctx.client.get_chat_members(ctx.chat_id):
            u = m.user
            if u is None or u.is_bot or u.is_deleted:
                continue
            rows.append(
                {
                    "id": u.id,
                    "username": u.username or "",
                    "name": " ".join(filter(None, [u.first_name, u.last_name])) or "User",
                    "status": _classify_status(u.status),
                }
            )
            if limit and len(rows) >= limit:
                break
        return rows

    ok, rows, r_err = await run_safe(_collect, context_label="ub_scrape")
    if not ok:
        await ctx.edit_raw(f"⚠️ **{r_err.short}:** {r_err.description}")
        return

    if not rows:
        await ctx.edit_raw("No members scraped (ensure you have access/admin rights).")
        return

    payload = json.dumps(rows, indent=2, ensure_ascii=False).encode("utf-8")
    caption = (
        f"📋 **Scraped {len(rows)} Members** (from `{ctx.chat_id}`)\n\n"
        'Reply to this file with:\n'
        '`.send all|active|offline <msg_link>`\n'
        "to send that message to scraped users who have usernames."
    )

    bio_me = BytesIO(payload)
    bio_me.name = f"scraped_{abs(ctx.chat_id)}.json"
    await ctx.client.send_document("me", bio_me, caption=caption)

    logger_id = doc.get("logger_group_id")
    if logger_id:
        try:
            bio_log = BytesIO(payload)
            bio_log.name = f"scraped_{abs(ctx.chat_id)}.json"
            await ctx.client.send_document(int(logger_id), bio_log, caption=caption)
        except Exception:
            pass

    await ctx.edit_raw(f"✅ Scraped `{len(rows)}` members and sent file to Saved Messages.")


@userbot_command("send")
async def ub_send_scraped(ctx: RawContext) -> None:
    """Reply to a scraped JSON file as `.send all|active|offline <msg_link>`."""
    allowed, doc, err = await check_client_access(ctx, feature="scrape", owner_only=True)
    if not allowed or doc is None:
        await ctx.edit_raw(err)
        return

    if not ctx.reply_to_msg_id:
        await ctx.edit_raw("Reply to a scraped file with `.send all|active|offline <msg_link>`.")
        return

    parts = ctx.args.strip().split()
    if len(parts) < 2:
        await ctx.edit_raw("Usage: `.send all|active|offline <t.me message link>`")
        return

    filter_mode = parts[0].lower()
    if filter_mode not in ("all", "active", "offline"):
        filter_mode = "all"
    m = _MSG_LINK_RE.match(parts[1])
    if not m:
        await ctx.edit_raw("Invalid Telegram message link.")
        return

    slug, msg_id_str = m.group(1), m.group(2)
    src_chat: int | str = int(f"-100{slug}") if slug.isdigit() else slug
    src_msg_id = int(msg_id_str)

    replied = await ctx.client.get_messages(ctx.chat_id, ctx.reply_to_msg_id)
    if not replied or not replied.document:
        await ctx.edit_raw("Replied message must be the scraped JSON file.")
        return

    downloaded = await ctx.client.download_media(replied, in_memory=True)
    if not isinstance(downloaded, BytesIO):
        await ctx.edit_raw("Could not download scraped file.")
        return

    try:
        rows = json.loads(downloaded.getvalue().decode("utf-8"))
    except Exception:
        await ctx.edit_raw("Invalid scraped JSON file format.")
        return

    targets = [
        r
        for r in rows
        if r.get("username")
        and (filter_mode == "all" or r.get("status") == filter_mode)
    ]
    if not targets:
        await ctx.edit_raw(f"No scraped users with usernames matched filter `{filter_mode}`.")
        return

    async def _worker() -> None:
        sent = 0
        failed = 0
        try:
            for item in targets:
                if should_stop(ctx.client_id):
                    break
                uname = item["username"]
                try:
                    await ctx.client.copy_message(uname, src_chat, src_msg_id)
                    sent += 1
                except FloodWait as fw:
                    await asyncio.sleep(fw.value + 1)
                except RPCError:
                    failed += 1
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass
        finally:
            report = (
                f"📨 **Scrape Send Report ({filter_mode})**\n"
                f"• **Targets:** `{len(targets)}` | **Sent:** `{sent}` | **Failed:** `{failed}`"
            )
            await ctx.client.send_message("me", report)
            await log_to_client_logger(doc, report, mtproto_client=ctx.client)

    register_client_task(ctx.client_id, "scrape_send", _worker())
    await ctx.edit_raw(
        f"📨 Sending to `{len(targets)}` scraped users (`{filter_mode}`). Use `.stop` to cancel."
    )
