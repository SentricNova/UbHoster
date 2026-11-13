"""Application entrypoint coordinating DB, HTTP/Webhook server, Main Bot, and Hosted Clients."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
import tracemalloc

# Install uvloop on Linux before importing pyrogram so Kurigram binds to the fast loop
if sys.platform != "win32":
    with contextlib.suppress(ImportError):
        import uvloop

        uvloop.install()

from pyrogram import idle

from config import Config
from Main.Client.Bot import create_main_bot
from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import load_client_plugins
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_manager import client_manager
from Main.utils.error_handler import configure_error_reporter
from Main.utils.http_server import start_http_server, stop_http_server
from Main.utils.peer_tracker import peer_tracker

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)
log = logging.getLogger("Main.main")


async def _delete_expired_broadcasts(main_bot) -> None:
    expired = await bot_db.get_expired_broadcast_msgs(time.time())
    if not expired:
        return
    oids = []
    for rec in expired:
        oids.append(rec["_id"])
        sid = str(rec.get("sender_id") or "main")
        cid = int(rec["chat_id"])
        mid = int(rec["message_id"])
        with contextlib.suppress(Exception):
            if sid == "main":
                await main_bot.delete_messages(cid, mid)
            else:
                rt = client_manager.get_runtime(sid)
                if rt is not None:
                    await rt.client.delete_messages(cid, mid)
                elif bot_webhook_manager.get_entry(sid) is not None:
                    await bot_webhook_manager.call_api(
                        sid, "deleteMessage", {"chat_id": cid, "message_id": mid}
                    )
    if oids:
        await bot_db.delete_broadcast_msg_records(oids)


async def _maintenance_loop(stop_event: asyncio.Event, main_bot) -> None:
    """Periodically bulk-flush dirty client stats, tracked peers, expired wizards, and auto-sleep."""
    last_sleep_check = 0.0
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=20.0)
        except asyncio.TimeoutError:
            with contextlib.suppress(Exception):
                await client_manager.flush_all_stats()
            with contextlib.suppress(Exception):
                await peer_tracker.flush()
            with contextlib.suppress(Exception):
                cleaned = await client_manager.cleanup_expired_wizards()
                if cleaned:
                    log.debug("Cleaned up %d expired onboarding wizard(s)", cleaned)
            with contextlib.suppress(Exception):
                await _delete_expired_broadcasts(main_bot)
            now = time.time()
            if now - last_sleep_check >= 600.0:
                last_sleep_check = now
                with contextlib.suppress(Exception):
                    slept = await client_manager.sleep_inactive_userbots()
                    if slept:
                        log.info("Auto-slept %d inactive userbot(s)", slept)


async def start_services() -> None:
    Config.validate()
    if not tracemalloc.is_tracing():
        tracemalloc.start(1)

    await bot_db.connect()
    await bot_db.ensure_indexes()
    await bot_db.sync_cache()

    from Main.utils.spy_manager import load_spy_cache
    await load_spy_cache()

    load_client_plugins()
    await start_http_server(Config.HTTP_HOST, Config.HTTP_PORT)

    bot = create_main_bot()
    await bot.start()
    configure_error_reporter(bot, Config.LOGGER_GROUP)
    me = await bot.get_me()
    log.info(
        "Main hosting bot started as @%s (%s) | Shard %d/%d | Webhook mode=%s",
        me.username,
        me.id,
        Config.SHARD_INDEX,
        Config.TOTAL_SHARDS,
        bot_webhook_manager.is_enabled,
    )

    started, failed = await client_manager.start_all_enabled()
    log.info(
        "Hosted clients booted: %d online (%d MTProto, %d webhook), %d failed",
        started,
        client_manager.running_mtproto_count,
        bot_webhook_manager.active_count,
        failed,
    )

    stop_event = asyncio.Event()
    maint_task = asyncio.create_task(_maintenance_loop(stop_event, bot))

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    try:
        await idle()
    finally:
        log.info("Shutting down services...")
        stop_event.set()
        maint_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maint_task

        await peer_tracker.flush()
        await client_manager.stop_all()
        with contextlib.suppress(Exception):
            await bot.stop()
        await stop_http_server()
        await bot_db.close()
        log.info("Shutdown complete.")


"""def run() -> None:
    loop = utils.get_event_loop()
    try:
        loop.run_until_complete(start_services())
    except KeyboardInterrupt:
        pass"""
def run() -> None:
    try:
        asyncio.run(start_services())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
