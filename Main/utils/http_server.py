"""Lightweight aiohttp HTTP server for UptimeRobot healthchecks & Bot API webhooks."""

from __future__ import annotations

import logging
import os
import time

import orjson
import psutil
from aiohttp import web

from config import Config
from Main.Client.Mongo import bot_db
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_manager import client_manager

log = logging.getLogger(__name__)

_START_TIME = time.time()
_runner: web.AppRunner | None = None
_site: web.TCPSite | None = None


async def _handle_root(request: web.Request) -> web.Response:
    if request.method == "HEAD":
        return web.Response(status=200)
    return web.json_response(
        {
            "status": "ok",
            "service": "userbot-hosting",
            "uptime_seconds": int(time.time() - _START_TIME),
            "running_clients": client_manager.running_count,
            "shard": f"{Config.SHARD_INDEX}/{Config.TOTAL_SHARDS}",
        }
    )


async def _handle_health(request: web.Request) -> web.Response:
    if request.method == "HEAD":
        return web.Response(status=200)

    mongo_ok = bot_db.engine.is_connected
    mongo_ms = None
    lmdb_ms = None
    if mongo_ok:
        try:
            mongo_ms, lmdb_ms = await bot_db.ping()
        except Exception:
            mongo_ok = False

    ram_mb = round(psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024), 2)

    return web.json_response(
        {
            "status": "healthy" if mongo_ok else "degraded",
            "uptime_seconds": int(time.time() - _START_TIME),
            "db": {
                "connected": mongo_ok,
                "mongo_ms": round(mongo_ms, 2) if mongo_ms is not None else None,
                "lmdb_ms": round(lmdb_ms, 2) if lmdb_ms is not None else None,
            },
            "hosted": {
                "userbots_mtproto": client_manager.running_mtproto_count,
                "bots_webhook": bot_webhook_manager.active_count,
                "total_running": client_manager.running_count,
            },
            "ram_mb": ram_mb,
        },
        status=200 if mongo_ok else 503,
    )


async def _handle_bot_webhook(request: web.Request) -> web.Response:
    client_id = request.match_info.get("client_id", "")
    if not client_id.startswith("bot_") or len(client_id) > 32:
        return web.Response(status=404)

    secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    try:
        raw_body = await request.read()
        payload = orjson.loads(raw_body)
    except Exception:
        return web.Response(status=400)

    if not isinstance(payload, dict):
        return web.Response(status=400)

    status_code = await bot_webhook_manager.handle_webhook_request(
        client_id=client_id,
        secret_header=secret_header,
        payload=payload,
    )
    return web.Response(status=status_code)


async def start_http_server(
    host: str = Config.HTTP_HOST,
    port: int = Config.HTTP_PORT,
) -> None:
    global _runner, _site
    if _runner is not None:
        return

    app = web.Application(client_max_size=Config.HTTP_MAX_BODY_BYTES)
    app.router.add_route("GET", "/", _handle_root)
    app.router.add_route("HEAD", "/", _handle_root)
    app.router.add_route("GET", "/health", _handle_health)
    app.router.add_route("HEAD", "/health", _handle_health)
    app.router.add_route("POST", "/webhook/{client_id}", _handle_bot_webhook)

    _runner = web.AppRunner(app, access_log=None)
    await _runner.setup()
    _site = web.TCPSite(_runner, host=host, port=port)
    await _site.start()
    log.info("HTTP endpoint listening on http://%s:%s/", host, port)


async def stop_http_server() -> None:
    global _runner, _site
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
        _site = None
        log.info("HTTP endpoint stopped")
