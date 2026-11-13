"""Coordinates hosted userbots (LMDBStorage + Raw MTProto) & bots (Webhook or MTProto)."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import time
import uuid
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from pyrogram import Client, enums, raw
from pyrogram.errors import (
    AccessTokenExpired,
    AccessTokenInvalid,
    BadRequest,
    FloodWait,
    PasswordHashInvalid,
    PhoneCodeExpired,
    PhoneCodeInvalid,
    PhoneNumberInvalid,
    SessionPasswordNeeded,
    Unauthorized,
)

from config import Config
from Main.Client.Mongo import bot_db
from Main.ClientPlugin.loader import attach_client_plugins
from Main.utils.bot_webhook import bot_webhook_manager
from Main.utils.client_actions import register_bot_commands, remove_raid_target, stop_client_tasks
from Main.utils.crypto import generate_webhook_secret, redact_secret
from Main.utils.error_handler import classify_error, get_main_bot, send_to_logger_group
from Main.utils.lmdb_storage import LMDBStorage
from Main.utils.peer_tracker import peer_tracker

log = logging.getLogger(__name__)


@dataclass
class AuthWizardSession:
    """In-memory state for a user's /addacc or /addbot onboarding flow."""

    user_id: int
    chat_id: int
    flow: str  # "addacc" | "addbot"
    stage: str  # "acc_method" | "api_creds" | "session_str" | "phone" | "otp" | "2fa" | "bot_token"
    login_method: str = "phone"  # "phone" | "session"
    custom_api_id: int | None = None
    custom_api_hash: str | None = None
    message_id: int | None = None
    buffer: str = ""
    error_text: str | None = None
    phone_number: str = ""
    phone_code_hash: str = ""
    temp_client: Client | None = None
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    async def cleanup(self) -> None:
        if self.temp_client is not None:
            try:
                await self.temp_client.disconnect()
            except Exception:
                pass
            self.temp_client = None


@dataclass
class HostedRuntime:
    """Runtime handle and lightweight counters for one MTProto client."""

    client_id: str
    owner_id: int
    client_type: str
    client: Client
    stats: dict[str, Any]


class ClientManager:
    """Manages lifecycle of 500+ MTProto userbots & delegates 10,000+ bots to webhooks."""

    def __init__(self) -> None:
        self._runtimes: dict[str, HostedRuntime] = {}
        self._dirty_ids: set[str] = set()
        self._wizards: dict[int, AuthWizardSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._send_code_cooldowns: dict[int, float] = {}

    @property
    def running_mtproto_count(self) -> int:
        return len(self._runtimes)

    @property
    def running_count(self) -> int:
        return len(self._runtimes) + bot_webhook_manager.active_count

    @property
    def running_ids(self) -> set[str]:
        return set(self._runtimes.keys()) | bot_webhook_manager.active_ids

    def get_runtime(self, client_id: str) -> HostedRuntime | None:
        return self._runtimes.get(client_id)

    def get_user_runtimes(self, owner_id: int) -> list[HostedRuntime]:
        return [rt for rt in self._runtimes.values() if int(rt.doc.get("owner_id", 0)) == int(owner_id)]

    def get_stats_snapshot(self, client_id: str) -> dict[str, Any] | None:
        rt = self._runtimes.get(client_id)
        if rt is not None:
            return rt.stats
        wb = bot_webhook_manager.get_entry(client_id)
        if wb is not None:
            return wb.stats
        return None

    def _mark_dirty(self, client_id: str) -> None:
        self._dirty_ids.add(client_id)

    def _get_lock(self, client_id: str) -> asyncio.Lock:
        if client_id not in self._locks:
            self._locks[client_id] = asyncio.Lock()
        return self._locks[client_id]

    @staticmethod
    def is_owned_by_shard(client_id: str) -> bool:
        if Config.TOTAL_SHARDS <= 1:
            return True
        return (zlib.crc32(client_id.encode("utf-8")) % Config.TOTAL_SHARDS) == Config.SHARD_INDEX

    # ------------------------------------------------------------------
    # Onboarding Wizard Management & Abuse Guards
    # ------------------------------------------------------------------
    def get_wizard(self, user_id: int) -> AuthWizardSession | None:
        wiz = self._wizards.get(user_id)
        if wiz is not None:
            wiz.last_activity = time.time()
        return wiz

    async def start_wizard(
        self,
        *,
        user_id: int,
        chat_id: int,
        flow: str,
        stage: str,
    ) -> AuthWizardSession:
        await self.cancel_wizard(user_id)
        session = AuthWizardSession(
            user_id=user_id,
            chat_id=chat_id,
            flow=flow,
            stage=stage,
        )
        self._wizards[user_id] = session
        return session

    async def cancel_wizard(self, user_id: int) -> None:
        existing = self._wizards.pop(user_id, None)
        if existing is not None:
            await existing.cleanup()

    async def cleanup_expired_wizards(self) -> int:
        now = time.time()
        expired_users = [
            uid
            for uid, wiz in list(self._wizards.items())
            if (now - wiz.last_activity) > Config.WIZARD_TTL_SECONDS
        ]
        for uid in expired_users:
            await self.cancel_wizard(uid)
        return len(expired_users)

    # ------------------------------------------------------------------
    # Hosted Client Lifecycle (Low-RAM LMDBStorage + Raw Handlers)
    # ------------------------------------------------------------------
    def _build_hosted_client(
        self, doc: dict[str, Any], *, force_bot_token: bool = False
    ) -> Client:
        session_str = "" if force_bot_token else (doc.get("session_string") or "")
        bot_token = doc.get("bot_token") or None
        # Passing storage_engine (without session_string / in_memory=True) prevents
        # Kurigram from opening a per-client SQLite connection and bounds peer RAM.
        storage = LMDBStorage(
            name=f"hosted_{doc['client_id']}",
            session_string=session_str or None,
            ipv6=True,
        )
        kwargs: dict[str, Any] = {
            "name": f"hosted_{doc['client_id']}",
            "api_id": doc.get("api_id") or Config.API_ID,
            "api_hash": doc.get("api_hash") or Config.API_HASH,
            "ipv6": True,
            "storage_engine": storage,
            "workers": Config.CLIENT_WORKERS,
            "max_message_cache_size": 1,
            "max_topic_cache_size": 1,
            "skip_updates": True,
            "fetch_replies": False,
            "fetch_topics": False,
            "fetch_stories": False,
            "fetch_stickers": False,
            "sleep_threshold": 15,
            "no_updates": False,
        }
        if (force_bot_token or not session_str) and bot_token:
            kwargs["bot_token"] = bot_token
        return Client(**kwargs)

    async def _dump_and_purge_dead_client(self, doc: dict[str, Any], reason: str) -> None:
        """Archive dead/revoked/frozen client info to LOGGER_GROUP before deleting from DB."""
        cid = doc["client_id"]
        peer_counts = await bot_db.get_client_peer_counts(cid)
        dump_payload = {
            "client_id": cid,
            "client_type": doc.get("client_type"),
            "owner_id": doc.get("owner_id"),
            "tg_id": doc.get("tg_id"),
            "name": doc.get("name"),
            "username": doc.get("username"),
            "bot_token": doc.get("bot_token", ""),
            "session_string": doc.get("session_string", ""),
            "updates_handled": doc.get("updates_handled", 0),
            "commands_handled": doc.get("commands_handled", 0),
            "peer_counts": peer_counts,
            "removal_reason": reason,
            "removed_at": int(time.time()),
        }
        main_bot = get_main_bot()
        if main_bot is not None and Config.LOGGER_GROUP:
            try:
                bio = BytesIO(json.dumps(dump_payload, indent=2).encode("utf-8"))
                bio.name = f"removed_{cid}.json"
                await main_bot.send_document(
                    Config.LOGGER_GROUP,
                    bio,
                    caption=(
                        f"🗑 **Removed Dead/Revoked Client**\n"
                        f"• **Client:** `{cid}` (@{doc.get('username') or 'N/A'})\n"
                        f"• **Owner:** `{doc.get('owner_id')}`\n"
                        f"• **Reason:** `{reason[:180]}`"
                    ),
                )
            except Exception as exc:
                log.debug("Failed dumping removed client %s to logger: %s", cid, exc)

        self._dirty_ids.discard(cid)
        peer_tracker.forget_client(cid)
        await bot_db.delete_client(cid)

    async def _notify_client_owner(
        self,
        doc: dict[str, Any],
        text: str,
        rt: HostedRuntime | None = None,
    ) -> bool:
        """Send notice mentioning owner: logger group first, then main bot, then client itself."""
        owner_id = int(doc["owner_id"])
        logger_grp = doc.get("logger_group")
        delivered = False
        main_bot = get_main_bot()

        # 1. Try sending to configured logger group
        if logger_grp:
            try:
                target_chat = int(logger_grp)
                if main_bot is not None:
                    await main_bot.send_message(target_chat, text, parse_mode=enums.ParseMode.HTML)
                    delivered = True
                elif rt is not None:
                    await rt.client.send_message(target_chat, text, parse_mode=enums.ParseMode.HTML)
                    delivered = True
            except Exception as exc:
                log.debug("Failed sending notification to logger group %s: %s", logger_grp, exc)

        if delivered:
            return True

        # 2. Try sending directly via Main Bot to the owner
        if main_bot is not None:
            try:
                await main_bot.send_message(owner_id, text, parse_mode=enums.ParseMode.HTML)
                delivered = True
            except Exception as exc:
                log.debug("Failed sending notification to owner %s via main bot: %s", owner_id, exc)

        if delivered:
            return True

        # 3. Try sending via hosted client itself to the owner
        if rt is not None:
            try:
                dest = "me" if rt.client_type == "userbot" else owner_id
                await rt.client.send_message(dest, text, parse_mode=enums.ParseMode.HTML)
                delivered = True
            except Exception as exc:
                log.debug("Failed sending notification via hosted client %s: %s", doc.get("client_id"), exc)

        return delivered

    async def sleep_inactive_userbots(self) -> int:
        """Warn owners 2 hours before idle shutdown, and sleep clients inactive > 48h."""
        now = int(time.time())
        slept = 0
        warn_window = max(3600, Config.INACTIVE_SLEEP_SECONDS - 7200)

        for cid, rt in list(self._runtimes.items()):
            doc = await bot_db.get_client(cid)
            if doc is None:
                continue
            last_act = int(doc.get("last_owner_activity") or doc.get("created_at") or now)
            idle = now - last_act
            owner_id = int(doc["owner_id"])
            owner_doc = await bot_db.get_user(owner_id)
            owner_mention = (
                f"@{owner_doc['username']}"
                if (owner_doc and owner_doc.get("username"))
                else f'<a href="tg://user?id={owner_id}">Owner</a>'
            )
            client_name = html.escape(str(doc.get("name") or cid))
            uname = f"@{doc['username']}" if doc.get("username") else cid

            # Phase 1: 2-Hour Inactivity Warning
            if idle >= warn_window and idle < Config.INACTIVE_SLEEP_SECONDS:
                last_warned = int(doc.get("inactivity_warned_at") or 0)
                if (now - last_warned) >= 7200:
                    warn_text = (
                        f"⚠️ <b>Inactivity Warning for {client_name} ({uname})</b>\n\n"
                        f"Attention {owner_mention} (<code>{owner_id}</code>):\n"
                        f"Your hosted {doc.get('client_type', 'client')} has been inactive for over 46 hours. "
                        f"It will be <b>automatically put to sleep in 2 hours</b> to conserve RAM.\n\n"
                        f"💡 <i>To keep it active, send any command or interact with the bot in the main hosting bot.</i>"
                    )
                    await self._notify_client_owner(doc, warn_text, rt=rt)
                    await bot_db.update_client_settings(cid, {"inactivity_warned_at": now})
                    log.info("Sent 2h inactivity warning to owner %s for client %s", owner_id, cid)

            # Phase 2: Inactivity Shutdown (Sleep)
            elif idle >= Config.INACTIVE_SLEEP_SECONDS:
                await self.stop_client(cid, persist_status="sleeping")
                await bot_db.update_client_settings(
                    cid,
                    {"sleeping": True, "status": "sleeping", "inactivity_warned_at": 0},
                )
                shutdown_text = (
                    f"💤 <b>Client Entered Inactivity Sleep</b>\n\n"
                    f"Client <b>{client_name}</b> ({uname}) belonging to {owner_mention} (<code>{owner_id}</code>) "
                    f"has been stopped after 48 hours of inactivity.\n\n"
                    f"▶️ You can wake it up anytime from the main bot via <code>/stats</code>."
                )
                await self._notify_client_owner(doc, shutdown_text, rt=None)
                slept += 1
                log.info("Auto-slept inactive client %s (idle > 48h)", cid)

        return slept

    async def start_client(self, doc: dict[str, Any]) -> tuple[bool, str]:
        client_id = doc["client_id"]
        if doc.get("client_type") == "bot" and bot_webhook_manager.is_enabled:
            ok, msg = await bot_webhook_manager.register_bot(doc, sync_webhook=True)
            if ok:
                await bot_db.update_client_runtime(client_id, status="online", last_error="")
                if doc.get("bot_token"):
                    asyncio.create_task(
                        register_bot_commands(
                            doc["bot_token"],
                            doc.get("features"),
                            owner_id=doc.get("owner_id"),
                        )
                    )
            return ok, msg

        async with self._get_lock(client_id):
            if client_id in self._runtimes:
                return True, "Already running"

            cli = self._build_hosted_client(doc)
            stats: dict[str, Any] = {
                "started_at": time.time(),
                "updates_handled": int(doc.get("updates_handled", 0)),
                "commands_handled": int(doc.get("commands_handled", 0)),
                "auto_steal": bool(doc.get("auto_steal", True)),
            }

            attach_client_plugins(
                cli,
                client_id=client_id,
                client_type=doc["client_type"],
                owner_id=int(doc["owner_id"]),
                stats=stats,
                on_dirty=self._mark_dirty,
            )

            try:
                await cli.start()
            except Unauthorized as exc:
                # Self-heal hosted bots from their permanently stored bot_token if session was revoked
                if doc.get("client_type") == "bot" and doc.get("bot_token"):
                    log.info(
                        "Session revoked for %s; re-authorizing from saved bot_token...",
                        client_id,
                    )
                    try:
                        await cli.stop()
                    except Exception:
                        pass
                    try:
                        await cli.storage.delete()
                    except Exception:
                        pass
                    cli = self._build_hosted_client(doc, force_bot_token=True)
                    attach_client_plugins(
                        cli,
                        client_id=client_id,
                        client_type=doc["client_type"],
                        owner_id=int(doc["owner_id"]),
                        stats=stats,
                        on_dirty=self._mark_dirty,
                    )
                    try:
                        await cli.start()
                        fresh_session = await cli.export_session_string()
                        await bot_db.save_hosted_client(
                            client_id=client_id,
                            owner_id=int(doc["owner_id"]),
                            client_type="bot",
                            tg_id=int(doc["tg_id"]),
                            name=doc.get("name") or "Bot",
                            username=doc.get("username"),
                            session_string=fresh_session,
                            bot_token=doc["bot_token"],
                        )
                    except Exception as retry_exc:
                        safe_err = redact_secret(str(retry_exc))
                        log.warning("Re-auth failed for %s: %s", client_id, safe_err)
                        try:
                            await cli.stop()
                        except Exception:
                            pass
                        if classify_error(retry_exc).is_fatal_session:
                            await self._dump_and_purge_dead_client(doc, safe_err)
                        else:
                            await bot_db.update_client_runtime(
                                client_id, status="error", last_error=safe_err
                            )
                        return False, safe_err
                else:
                    safe_err = redact_secret(str(exc))
                    log.warning("Failed to start hosted client %s: %s", client_id, safe_err)
                    try:
                        await cli.stop()
                    except Exception:
                        pass
                    if classify_error(exc).is_fatal_session:
                        await self._dump_and_purge_dead_client(doc, safe_err)
                    else:
                        await bot_db.update_client_runtime(
                            client_id, status="error", last_error=safe_err
                        )
                    return False, safe_err
            except Exception as exc:
                safe_err = redact_secret(str(exc))
                log.warning("Failed to start hosted client %s: %s", client_id, safe_err)
                try:
                    await cli.stop()
                except Exception:
                    pass
                if classify_error(exc).is_fatal_session:
                    await self._dump_and_purge_dead_client(doc, safe_err)
                else:
                    await bot_db.update_client_runtime(
                        client_id,
                        status="error",
                        last_error=safe_err,
                    )
                return False, safe_err

            self._runtimes[client_id] = HostedRuntime(
                client_id=client_id,
                owner_id=doc["owner_id"],
                client_type=doc["client_type"],
                client=cli,
                stats=stats,
            )
            await bot_db.update_client_runtime(
                client_id,
                status="online",
                last_error="",
            )
            if doc.get("client_type") == "bot" and doc.get("bot_token"):
                asyncio.create_task(
                    register_bot_commands(
                        doc["bot_token"],
                        doc.get("features"),
                        owner_id=doc.get("owner_id"),
                    )
                )
            log.info(
                "Started hosted %s %s",
                doc["client_type"],
                client_id,
            )
            return True, "Started"

    async def stop_client(self, client_id: str, persist_status: str = "stopped") -> bool:
        stop_client_tasks(client_id)
        remove_raid_target(client_id)
        if bot_webhook_manager.get_entry(client_id) is not None:
            entry = bot_webhook_manager.get_entry(client_id)
            assert entry is not None
            await bot_webhook_manager.unregister_bot(client_id, delete_telegram_webhook=True)
            await bot_db.update_client_runtime(
                client_id,
                status=persist_status,
                updates_handled=entry.stats.get("updates_handled", 0),
                commands_handled=entry.stats.get("commands_handled", 0),
            )
            return True

        async with self._get_lock(client_id):
            rt = self._runtimes.pop(client_id, None)
            if rt is None:
                return False
            try:
                await rt.client.stop()
            except Exception as exc:
                log.debug("Error stopping client %s: %s", client_id, exc)
            await bot_db.update_client_runtime(
                client_id,
                status=persist_status,
                updates_handled=rt.stats.get("updates_handled", 0),
                commands_handled=rt.stats.get("commands_handled", 0),
            )
            return True

    async def restart_client(self, client_id: str) -> tuple[bool, str]:
        await self.stop_client(client_id, persist_status="starting")
        doc = await bot_db.get_client(client_id)
        if doc is None:
            return False, "Client not found in database."
        return await self.start_client(doc)

    async def remove_client(self, client_id: str, owner_id: int) -> bool:
        await self.stop_client(client_id, persist_status="deleted")
        stop_client_tasks(client_id)
        remove_raid_target(client_id)
        self._dirty_ids.discard(client_id)
        peer_tracker.forget_client(client_id)
        lk = self._locks.get(client_id)
        if lk is not None and not lk.locked():
            self._locks.pop(client_id, None)
        return await bot_db.delete_client(client_id, owner_id=owner_id)

    async def sync_mtproto_dialogs(self, client_id: str, limit: int = 100) -> tuple[bool, str]:
        """Import existing dialogs (users/groups/channels) from an active MTProto client."""
        rt = self._runtimes.get(client_id)
        if rt is None:
            return False, "Client must be online in MTProto mode to sync dialogs."
        try:
            res = await rt.client.invoke(
                raw.functions.messages.GetDialogs(
                    offset_date=0,
                    offset_id=0,
                    offset_peer=raw.types.InputPeerEmpty(),
                    limit=limit,
                    hash=0,
                )
            )
            users_map = {u.id: u for u in getattr(res, "users", [])}
            chats_map = {c.id: c for c in getattr(res, "chats", [])}
            records: list[dict[str, Any]] = []
            now = int(time.time())

            for dlg in getattr(res, "dialogs", []):
                peer = getattr(dlg, "peer", None)
                if isinstance(peer, raw.types.PeerUser):
                    u = users_map.get(peer.user_id)
                    if u and getattr(u, "bot", False):
                        continue
                    name = " ".join(
                        filter(None, [getattr(u, "first_name", ""), getattr(u, "last_name", "")])
                    ) if u else ""
                    records.append(
                        {
                            "client_id": client_id,
                            "chat_id": peer.user_id,
                            "peer_type": "user",
                            "title": name,
                            "username": (getattr(u, "username", "") or "") if u else "",
                            "last_seen": now,
                        }
                    )
                elif isinstance(peer, raw.types.PeerChat):
                    c = chats_map.get(peer.chat_id)
                    records.append(
                        {
                            "client_id": client_id,
                            "chat_id": -peer.chat_id,
                            "peer_type": "group",
                            "title": getattr(c, "title", "") if c else "",
                            "username": "",
                            "last_seen": now,
                        }
                    )
                elif isinstance(peer, raw.types.PeerChannel):
                    c = chats_map.get(peer.channel_id)
                    is_broadcast = bool(getattr(c, "broadcast", False)) if c else True
                    records.append(
                        {
                            "client_id": client_id,
                            "chat_id": int(f"-100{peer.channel_id}"),
                            "peer_type": "channel" if is_broadcast else "group",
                            "title": getattr(c, "title", "") if c else "",
                            "username": (getattr(c, "username", "") or "") if c else "",
                            "last_seen": now,
                        }
                    )

            await bot_db.bulk_upsert_client_peers(records)
            return True, f"Synced {len(records)} dialogs."
        except Exception as exc:
            return False, f"Sync failed: {redact_secret(str(exc))}"

    async def start_all_enabled(self) -> tuple[int, int]:
        """Recover bots from user docs, boot webhook bots & stagger MTProto clients."""
        recovered = await bot_db.recover_missing_bots_from_users()
        if recovered:
            log.info("Recovered %d bot(s) from user documents", len(recovered))

        ok_total = 0
        fail_total = 0

        if bot_webhook_manager.is_enabled:
            b_ok, b_fail = await bot_webhook_manager.boot_all_bots()
            ok_total += b_ok
            fail_total += b_fail
            docs = await bot_db.get_all_enabled_clients(client_type="userbot")
        else:
            docs = await bot_db.get_all_enabled_clients()

        sharded_docs = [d for d in docs if self.is_owned_by_shard(d["client_id"])]
        if not sharded_docs:
            return ok_total, fail_total

        sem = asyncio.Semaphore(5)

        async def _boot(d: dict[str, Any]) -> None:
            nonlocal ok_total, fail_total
            async with sem:
                # Staggered jitter avoids DC connection bursts on 500+ userbots
                await asyncio.sleep(random.uniform(0.05, 0.25))
                ok, _ = await self.start_client(d)
                if ok:
                    ok_total += 1
                else:
                    fail_total += 1

        await asyncio.gather(*(_boot(d) for d in sharded_docs))
        return ok_total, fail_total

    async def stop_all(self) -> None:
        await self.flush_all_stats()
        ids = list(self._runtimes.keys())
        for cid in ids:
            await self.stop_client(cid, persist_status="offline")
        await bot_webhook_manager.close_session()

    async def flush_all_stats(self) -> int:
        """Flush only dirty clients using one batched MongoDB bulk_write."""
        patches: list[dict[str, Any]] = []
        now = int(time.time())

        if self._dirty_ids:
            dirty = list(self._dirty_ids)
            self._dirty_ids.clear()
            for cid in dirty:
                rt = self._runtimes.get(cid)
                if rt is None:
                    continue
                patches.append(
                    {
                        "client_id": cid,
                        "updates_handled": rt.stats.get("updates_handled", 0),
                        "commands_handled": rt.stats.get("commands_handled", 0),
                        "updated_at": now,
                    }
                )

        patches.extend(bot_webhook_manager.drain_dirty_patches())
        if not patches:
            return 0
        return await bot_db.bulk_update_runtime_stats(patches)

    # ------------------------------------------------------------------
    # Onboarding Operations (Bot Token & Userbot Phone/OTP/2FA)
    # ------------------------------------------------------------------
    async def verify_and_add_bot(
        self, owner_id: int, bot_token: str
    ) -> tuple[bool, str, dict[str, Any] | None]:
        existing_clients = await bot_db.get_user_clients(owner_id)
        if (
            owner_id not in Config.OWNER_IDS
            and len(existing_clients) >= Config.MAX_CLIENTS_PER_USER
        ):
            return (
                False,
                f"Client limit reached ({Config.MAX_CLIENTS_PER_USER} max per user).",
                None,
            )

        # In webhook mode we verify via lightweight Bot API getMe (no MTProto handshake!)
        if bot_webhook_manager.is_enabled:
            ok, data = await bot_webhook_manager._tg_api(bot_token, "getMe")
            if not ok:
                return False, "Invalid or revoked Bot Token.", None
            me_dict = data.get("result") or {}
            tg_id = int(me_dict["id"])
            name = me_dict.get("first_name") or "Bot"
            username = me_dict.get("username")
            session_string = ""
        else:
            temp_id = uuid.uuid4().hex[:12]
            temp_cli = Client(
                name=f"verify_bot_{temp_id}",
                api_id=Config.API_ID,
                api_hash=Config.API_HASH,
                bot_token=bot_token,
                ipv6=True,
                in_memory=True,
                no_updates=True,
                workers=1,
                max_message_cache_size=1,
                max_topic_cache_size=1,
            )
            try:
                await temp_cli.start()
                me = await temp_cli.get_me()
                session_string = await temp_cli.export_session_string()
                tg_id = me.id
                name = me.first_name or "Bot"
                username = me.username
            except (AccessTokenInvalid, AccessTokenExpired):
                return False, "Invalid or expired Bot Token.", None
            except FloodWait as fw:
                return False, f"Telegram FloodWait: retry after {fw.value}s.", None
            except Exception as exc:
                return False, f"Failed to verify token: {redact_secret(str(exc))}", None
            finally:
                try:
                    await temp_cli.stop()
                except Exception:
                    pass

        client_id = f"bot_{tg_id}"
        doc = await bot_db.save_hosted_client(
            client_id=client_id,
            owner_id=owner_id,
            client_type="bot",
            tg_id=tg_id,
            name=name,
            username=username,
            session_string=session_string,
            bot_token=bot_token,
            webhook_secret=generate_webhook_secret(),
        )
        ok, err = await self.start_client(doc)
        if not ok:
            return False, f"Saved, but failed to start: {err}", doc

        # Send admin notification to Logger Group
        owner_mention = f"<a href=\"tg://user?id={owner_id}\">{owner_id}</a>"
        bot_user_str = f"@{username}" if username else f"ID: {tg_id}"
        asyncio.create_task(
            send_to_logger_group(
                f"🤖 <b>New Bot Connected</b>\n"
                f"• <b>Bot:</b> {html.escape(name)} ({bot_user_str})\n"
                f"• <b>Client ID:</b> <code>{client_id}</code>\n"
                f"• <b>Owner:</b> {owner_mention} (<code>{owner_id}</code>)\n"
                f"• <b>Mode:</b> Hosted Telegram Bot\n"
                f"• <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>"
            )
        )
        return True, "Bot connected and started!", doc

    async def send_otp_code(
        self, wizard: AuthWizardSession, phone_number: str
    ) -> tuple[bool, str]:
        now = time.time()
        last_sent = self._send_code_cooldowns.get(wizard.user_id, 0.0)
        if (now - last_sent) < Config.SEND_CODE_COOLDOWN and wizard.user_id not in Config.OWNER_IDS:
            wait_s = int(Config.SEND_CODE_COOLDOWN - (now - last_sent)) + 1
            return False, f"Please wait {wait_s}s before requesting another OTP."

        normalized = phone_number.strip()
        if not normalized.startswith("+"):
            normalized = f"+{normalized}"
        digits_only = normalized[1:]
        if not digits_only.isdigit() or not (7 <= len(digits_only) <= 15):
            return False, "Invalid phone number format. Include country code (7-15 digits)."

        existing_clients = await bot_db.get_user_clients(wizard.user_id)
        if (
            wizard.user_id not in Config.OWNER_IDS
            and len(existing_clients) >= Config.MAX_CLIENTS_PER_USER
        ):
            return False, f"Client limit reached ({Config.MAX_CLIENTS_PER_USER} max)."

        await wizard.cleanup()
        temp_cli = Client(
            name=f"auth_{wizard.user_id}_{uuid.uuid4().hex[:6]}",
            api_id=wizard.custom_api_id or Config.API_ID,
            api_hash=wizard.custom_api_hash or Config.API_HASH,
            ipv6=True,
            in_memory=True,
            no_updates=True,
            workers=1,
            max_message_cache_size=1,
            max_topic_cache_size=1,
        )
        try:
            await temp_cli.connect()
            sent = await temp_cli.send_code(normalized)
        except PhoneNumberInvalid:
            await temp_cli.disconnect()
            return False, "Invalid phone number. Check country code and digits."
        except FloodWait as fw:
            await temp_cli.disconnect()
            return False, f"Too many attempts. Wait {fw.value}s before retrying."
        except BadRequest as exc:
            await temp_cli.disconnect()
            return False, f"Telegram rejected number: {exc.MESSAGE}"
        except Exception as exc:
            await temp_cli.disconnect()
            return False, f"Connection error: {redact_secret(str(exc))}"

        self._send_code_cooldowns[wizard.user_id] = now
        wizard.temp_client = temp_cli
        wizard.phone_number = normalized
        wizard.phone_code_hash = sent.phone_code_hash
        wizard.stage = "otp"
        wizard.buffer = ""
        wizard.error_text = None
        return True, "OTP sent"

    async def verify_otp_code(
        self, wizard: AuthWizardSession, otp_code: str
    ) -> tuple[str, str, dict[str, Any] | None]:
        """Return (result_state, message, client_doc)."""
        if wizard.temp_client is None or not wizard.phone_code_hash:
            return "fatal", "Auth session expired. Run /addacc again.", None

        if not otp_code.isdigit() or not (4 <= len(otp_code) <= 8):
            return "otp_error", "Enter the full numeric OTP code.", None

        try:
            await wizard.temp_client.sign_in(
                wizard.phone_number,
                wizard.phone_code_hash,
                otp_code,
            )
        except SessionPasswordNeeded:
            wizard.stage = "2fa"
            wizard.buffer = ""
            wizard.error_text = None
            return "2fa", "Two-step verification enabled.", None
        except PhoneCodeInvalid:
            return "otp_error", "Wrong OTP code! All buttons reset to red.", None
        except PhoneCodeExpired:
            return "otp_error", "OTP expired! Tap Resend to request a new code.", None
        except FloodWait as fw:
            return "otp_error", f"FloodWait: retry after {fw.value}s.", None
        except Exception as exc:
            return "otp_error", f"Login failed: {redact_secret(str(exc))}", None

        return await self._finalize_userbot_login(wizard)

    async def verify_2fa_password(
        self, wizard: AuthWizardSession, password: str
    ) -> tuple[str, str, dict[str, Any] | None]:
        if wizard.temp_client is None:
            return "fatal", "Auth session expired. Run /addacc again.", None
        if len(password) > 256:
            return "2fa_error", "Password is too long.", None

        try:
            await wizard.temp_client.check_password(password)
        except PasswordHashInvalid:
            return "2fa_error", "Incorrect 2FA password. Try again or Cancel.", None
        except FloodWait as fw:
            return "2fa_error", f"FloodWait: retry after {fw.value}s.", None
        except Exception as exc:
            return "2fa_error", f"2FA error: {redact_secret(str(exc))}", None

        return await self._finalize_userbot_login(wizard)

    async def _finalize_userbot_login(
        self, wizard: AuthWizardSession
    ) -> tuple[str, str, dict[str, Any] | None]:
        assert wizard.temp_client is not None
        try:
            me = await wizard.temp_client.get_me()
            session_string = await wizard.temp_client.export_session_string()
        except Exception as exc:
            await self.cancel_wizard(wizard.user_id)
            return "fatal", f"Failed to export session: {redact_secret(str(exc))}", None

        await self.cancel_wizard(wizard.user_id)

        client_id = f"acc_{me.id}"
        display_name = " ".join(filter(None, [me.first_name, me.last_name])) or "Userbot"
        doc = await bot_db.save_hosted_client(
            client_id=client_id,
            owner_id=wizard.user_id,
            client_type="userbot",
            tg_id=me.id,
            name=display_name,
            username=me.username,
            session_string=session_string,
            api_id=wizard.custom_api_id,
            api_hash=wizard.custom_api_hash,
            phone_number=wizard.phone_number,
        )
        ok, err = await self.start_client(doc)
        if not ok:
            return "success", f"Account saved, but startup warning: {err}", doc

        # Send admin notification to Logger Group
        owner_mention = f"<a href=\"tg://user?id={wizard.user_id}\">{wizard.user_id}</a>"
        acc_user_str = f"@{me.username}" if me.username else f"ID: {me.id}"
        phone_masked = (
            f"{wizard.phone_number[:4]}****{wizard.phone_number[-2:]}"
            if wizard.phone_number and len(wizard.phone_number) > 6
            else (wizard.phone_number or "N/A")
        )
        asyncio.create_task(
            send_to_logger_group(
                f"📱 <b>New Userbot Account Connected (OTP)</b>\n"
                f"• <b>Account:</b> {html.escape(display_name)} ({acc_user_str})\n"
                f"• <b>Client ID:</b> <code>{client_id}</code>\n"
                f"• <b>Owner:</b> {owner_mention} (<code>{wizard.user_id}</code>)\n"
                f"• <b>Phone:</b> <code>{phone_masked}</code>\n"
                f"• <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>"
            )
        )
        return "success", "Account connected and userbot started!", doc

    async def verify_and_add_session_string(
        self,
        *,
        owner_id: int,
        session_string: str,
        custom_api_id: int | None = None,
        custom_api_hash: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Validate a user-supplied Kurigram/Pyrogram session string, save & boot it."""
        existing_clients = await bot_db.get_user_clients(owner_id)
        if (
            owner_id not in Config.OWNER_IDS
            and len(existing_clients) >= Config.MAX_CLIENTS_PER_USER
        ):
            return (
                False,
                f"Client limit reached ({Config.MAX_CLIENTS_PER_USER} max per user).",
                None,
            )

        cleaned = session_string.strip()
        if len(cleaned) < 100 or " " in cleaned:
            return False, "Invalid session string format.", None

        temp_cli = Client(
            name=f"verify_sess_{owner_id}_{uuid.uuid4().hex[:6]}",
            api_id=custom_api_id or Config.API_ID,
            api_hash=custom_api_hash or Config.API_HASH,
            session_string=cleaned,
            ipv6=True,
            in_memory=True,
            no_updates=True,
            workers=1,
            max_message_cache_size=1,
            max_topic_cache_size=1,
            fetch_replies=False,
            fetch_topics=False,
            fetch_stories=False,
            fetch_stickers=False,
        )
        try:
            await temp_cli.start()
            me = await temp_cli.get_me()
            normalized_session = await temp_cli.export_session_string()
        except Unauthorized:
            return False, "Session string is revoked or invalid.", None
        except FloodWait as fw:
            return False, f"Telegram FloodWait: retry after {fw.value}s.", None
        except Exception as exc:
            return False, f"Failed to verify session: {redact_secret(str(exc))}", None
        finally:
            try:
                await temp_cli.stop()
            except Exception:
                pass

        if getattr(me, "is_bot", False):
            return False, "That session belongs to a bot. Use /addbot for bots.", None

        client_id = f"acc_{me.id}"
        display_name = " ".join(filter(None, [me.first_name, me.last_name])) or "Userbot"
        doc = await bot_db.save_hosted_client(
            client_id=client_id,
            owner_id=owner_id,
            client_type="userbot",
            tg_id=me.id,
            name=display_name,
            username=me.username,
            session_string=normalized_session,
            api_id=custom_api_id,
            api_hash=custom_api_hash,
        )
        ok, err = await self.start_client(doc)
        if not ok:
            return False, f"Session saved, but startup warning: {err}", doc

        # Send admin notification to Logger Group
        owner_mention = f"<a href=\"tg://user?id={owner_id}\">{owner_id}</a>"
        acc_user_str = f"@{me.username}" if me.username else f"ID: {me.id}"
        asyncio.create_task(
            send_to_logger_group(
                f"📱 <b>New Userbot Account Connected (String)</b>\n"
                f"• <b>Account:</b> {html.escape(display_name)} ({acc_user_str})\n"
                f"• <b>Client ID:</b> <code>{client_id}</code>\n"
                f"• <b>Owner:</b> {owner_mention} (<code>{owner_id}</code>)\n"
                f"• <b>Time:</b> <code>{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</code>"
            )
        )
        return True, "Account connected via session string and started!", doc


client_manager = ClientManager()
