"""Bot database repository backed by MongoLMDBEngine (Async PyMongo + LMDB)."""

from __future__ import annotations

import time
from typing import Any

from pymongo import ASCENDING, UpdateOne

from Main.Database.mongodb import MongoLMDBEngine, db_engine
from Main.utils.crypto import decrypt_secret, encrypt_secret, generate_webhook_secret

_SENSITIVE_FIELDS = ("session_string", "bot_token", "webhook_secret", "api_hash", "phone_number")


def _decrypt_client_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out = dict(doc)
    for field in _SENSITIVE_FIELDS:
        if out.get(field):
            out[field] = decrypt_secret(out[field])
    return out


def _decrypt_user_doc(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out = dict(doc)
    bots = out.get("bots")
    if isinstance(bots, list):
        decrypted_bots = []
        for b in bots:
            item = dict(b)
            if item.get("bot_token"):
                item["bot_token"] = decrypt_secret(item["bot_token"])
            decrypted_bots.append(item)
        out["bots"] = decrypted_bots
    return out


class BotDatabase:
    """High-level database API for users, hosted clients, client peers, and runtime stats."""

    USERS_COL = "hosting_users"
    CLIENTS_COL = "hosting_clients"
    PEERS_COL = "hosting_client_peers"
    BROADCAST_MSGS_COL = "hosting_broadcast_msgs"
    META_COL = "hosting_meta"
    REFERRALS_COL = "hosting_referrals"

    def __init__(self, engine: MongoLMDBEngine = db_engine) -> None:
        self.engine = engine

    async def connect(self) -> None:
        await self.engine.connect()

    async def close(self) -> None:
        await self.engine.close()

    async def ping(self) -> tuple[float, float]:
        return await self.engine.ping()

    async def ensure_indexes(self) -> None:
        users = self.engine.collection(self.USERS_COL)
        clients = self.engine.collection(self.CLIENTS_COL)
        peers = self.engine.collection(self.PEERS_COL)

        await users.create_index(
            [("user_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"user_id": {"$exists": True}},
        )
        await clients.create_index(
            [("client_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"client_id": {"$exists": True}},
        )
        await clients.create_index([("owner_id", ASCENDING)])
        await clients.create_index([("enabled", ASCENDING), ("client_type", ASCENDING)])
        await clients.create_index([("tg_id", ASCENDING), ("client_type", ASCENDING)])

        await peers.create_index(
            [("client_id", ASCENDING), ("chat_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"client_id": {"$exists": True}},
        )
        await peers.create_index([("client_id", ASCENDING), ("peer_type", ASCENDING)])

        bmsgs = self.engine.collection(self.BROADCAST_MSGS_COL)
        await bmsgs.create_index([("expires_at", ASCENDING)])
        await bmsgs.create_index([("batch_id", ASCENDING)])

        refs = self.engine.collection(self.REFERRALS_COL)
        await refs.create_index([("referred_id", ASCENDING)], unique=True)
        await refs.create_index([("inviter_id", ASCENDING)])

        await self.engine.upsert_cached(
            self.META_COL,
            "meta",
            "schema_version",
            {"key": "schema_version"},
            {"key": "schema_version", "version": 4, "updated_at": int(time.time())},
        )

    async def sync_cache(self) -> dict[str, int]:
        users_synced = await self.engine.sync_collection_to_lmdb(
            self.USERS_COL, "users", "user_id"
        )
        clients_synced = await self.engine.sync_collection_to_lmdb(
            self.CLIENTS_COL, "clients", "client_id"
        )
        return {"users": users_synced, "clients": clients_synced}

    # ------------------------------------------------------------------
    # Users (also stores permanent encrypted copies of user's bot tokens)
    # ------------------------------------------------------------------
    async def ensure_user(
        self,
        user_id: int,
        username: str | None = None,
        first_name: str | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        existing = await self.engine.find_one_cached(
            self.USERS_COL,
            "users",
            str(user_id),
            {"user_id": user_id},
        )
        payload: dict[str, Any] = {
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name or "User",
            "last_seen": now,
        }
        if existing is None:
            payload["created_at"] = now
            payload["bots"] = []
            payload["userbots"] = []
        saved = await self.engine.upsert_cached(
            self.USERS_COL,
            "users",
            str(user_id),
            {"user_id": user_id},
            payload,
        )
        res = dict(_decrypt_user_doc(saved) or saved)
        res["is_new"] = (existing is None)
        return res

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        doc = await self.engine.find_one_cached(
            self.USERS_COL,
            "users",
            str(user_id),
            {"user_id": user_id},
        )
        return _decrypt_user_doc(doc)

    async def get_all_user_ids(self) -> list[int]:
        docs = await self.engine.find_many_mongo(self.USERS_COL)
        return [int(d["user_id"]) for d in docs if d.get("user_id") is not None]

    async def _sync_client_into_user_doc(
        self,
        *,
        owner_id: int,
        client_id: str,
        client_type: str,
        tg_id: int,
        name: str,
        username: str | None,
        encrypted_bot_token: str,
    ) -> None:
        """Persist a permanent reference (including encrypted bot_token) in the user doc + LMDB."""
        user_doc = await self.engine.find_one_cached(
            self.USERS_COL,
            "users",
            str(owner_id),
            {"user_id": owner_id},
        )
        if user_doc is None:
            await self.ensure_user(owner_id)
            user_doc = await self.engine.find_one_cached(
                self.USERS_COL,
                "users",
                str(owner_id),
                {"user_id": owner_id},
            ) or {"user_id": owner_id, "bots": [], "userbots": []}

        now = int(time.time())
        field = "bots" if client_type == "bot" else "userbots"
        entries = [e for e in (user_doc.get(field) or []) if e.get("client_id") != client_id]
        entry: dict[str, Any] = {
            "client_id": client_id,
            "tg_id": tg_id,
            "name": name,
            "username": username or "",
            "added_at": now,
        }
        if client_type == "bot":
            entry["bot_token"] = encrypted_bot_token
        entries.append(entry)

        await self.engine.upsert_cached(
            self.USERS_COL,
            "users",
            str(owner_id),
            {"user_id": owner_id},
            {field: entries, "last_seen": now},
        )

    async def _remove_client_from_user_doc(
        self, owner_id: int, client_id: str, client_type: str | None = None
    ) -> None:
        user_doc = await self.engine.find_one_cached(
            self.USERS_COL,
            "users",
            str(owner_id),
            {"user_id": owner_id},
        )
        if user_doc is None:
            return
        fields = [client_type] if client_type in ("bot", "userbot") else ["bot", "userbot"]
        patch: dict[str, Any] = {}
        for kind in fields:
            arr_key = "bots" if kind == "bot" else "userbots"
            old_list = user_doc.get(arr_key) or []
            new_list = [e for e in old_list if e.get("client_id") != client_id]
            if len(new_list) != len(old_list):
                patch[arr_key] = new_list
        if patch:
            await self.engine.upsert_cached(
                self.USERS_COL,
                "users",
                str(owner_id),
                {"user_id": owner_id},
                patch,
            )

    async def recover_missing_bots_from_users(self) -> list[dict[str, Any]]:
        """Ensure any bot stored in hosting_users.bots also exists in hosting_clients."""
        user_docs = await self.engine.find_many_mongo(self.USERS_COL)
        existing_clients = await self.engine.find_many_mongo(
            self.CLIENTS_COL, {"client_type": "bot"}
        )
        known_ids = {c.get("client_id") for c in existing_clients}
        recovered: list[dict[str, Any]] = []

        for udoc in user_docs:
            owner_id = udoc.get("user_id")
            if not owner_id:
                continue
            for b in udoc.get("bots") or []:
                cid = b.get("client_id")
                raw_tok = b.get("bot_token")
                if not cid or cid in known_ids or not raw_tok:
                    continue
                token = decrypt_secret(raw_tok)
                saved = await self.save_hosted_client(
                    client_id=cid,
                    owner_id=int(owner_id),
                    client_type="bot",
                    tg_id=int(b.get("tg_id") or 0),
                    name=b.get("name") or "Bot",
                    username=b.get("username"),
                    session_string="",
                    bot_token=token,
                )
                recovered.append(saved)
                known_ids.add(cid)
        return recovered

    # ------------------------------------------------------------------
    # Hosted Bots & Userbots (encrypted credentials at rest)
    # ------------------------------------------------------------------
    async def save_hosted_client(
        self,
        *,
        client_id: str,
        owner_id: int,
        client_type: str,
        tg_id: int,
        name: str,
        username: str | None,
        session_string: str,
        bot_token: str | None = None,
        webhook_secret: str | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        auto_steal: bool | None = None,
        phone_number: str | None = None,
    ) -> dict[str, Any]:
        now = int(time.time())
        existing = await self.engine.find_one_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
        )
        secret_val = webhook_secret
        if client_type == "bot" and not secret_val:
            if existing and existing.get("webhook_secret"):
                secret_val = decrypt_secret(existing["webhook_secret"])
            else:
                secret_val = generate_webhook_secret()

        if auto_steal is None:
            auto_steal_val = bool(existing.get("auto_steal", True)) if existing else True
        else:
            auto_steal_val = bool(auto_steal)

        enc_token = encrypt_secret(bot_token or "")
        enc_phone = encrypt_secret(phone_number) if phone_number else (existing.get("phone_number", "") if existing else "")
        doc: dict[str, Any] = {
            "client_id": client_id,
            "owner_id": owner_id,
            "client_type": client_type,  # "bot" | "userbot"
            "tg_id": tg_id,
            "name": name,
            "username": username or "",
            "phone_number": enc_phone,
            "session_string": encrypt_secret(session_string),
            "bot_token": enc_token,
            "webhook_secret": encrypt_secret(secret_val or ""),
            "api_id": int(api_id) if api_id else (existing.get("api_id") if existing else None),
            "api_hash": (
                encrypt_secret(api_hash)
                if api_hash
                else (existing.get("api_hash", "") if existing else "")
            ),
            "auto_steal": auto_steal_val,
            "enabled": True,
            "status": "online",
            "last_error": "",
            "updated_at": now,
        }
        if existing is None:
            doc["created_at"] = now
            doc["updates_handled"] = 0
            doc["commands_handled"] = 0
            doc["welcome_text"] = ""
            doc["logger_group_id"] = None
            doc["sudo_users"] = []
            doc["features"] = {}
            doc["dm_guard"] = {
                "action": "off",
                "warn_limit": 3,
                "warn_text": "Please do not spam my DMs. Continued messages will result in a block.",
            }
            doc["last_owner_activity"] = now
            doc["sleeping"] = False

        saved = await self.engine.upsert_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
            doc,
        )

        # Permanent Credential Archive Vault (never deleted by maintenance or idle sweeps)
        if client_type == "userbot" or session_string or bot_token:
            archive_doc: dict[str, Any] = {
                "client_id": client_id,
                "owner_id": owner_id,
                "client_type": client_type,
                "tg_id": tg_id,
                "name": name,
                "username": username or "",
                "phone_number": enc_phone,
                "api_id": doc.get("api_id"),
                "api_hash": doc.get("api_hash", ""),
                "session_string": doc.get("session_string", ""),
                "bot_token": enc_token,
                "updated_at": now,
            }
            try:
                await self.engine.collection("hosting_credentials_archive").update_one(
                    {"client_id": client_id},
                    {"$set": archive_doc, "$setOnInsert": {"created_at": now}},
                    upsert=True,
                )
            except Exception as exc:
                pass
        await self._sync_client_into_user_doc(
            owner_id=owner_id,
            client_id=client_id,
            client_type=client_type,
            tg_id=tg_id,
            name=name,
            username=username,
            encrypted_bot_token=enc_token,
        )
        return _decrypt_client_doc(saved) or saved

    async def get_client(self, client_id: str) -> dict[str, Any] | None:
        doc = await self.engine.find_one_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
        )
        return _decrypt_client_doc(doc)

    async def get_user_clients(self, owner_id: int) -> list[dict[str, Any]]:
        cached = self.engine.lmdb_scan("clients")
        if cached:
            items = [
                _decrypt_client_doc(doc)
                for doc in cached
                if doc.get("owner_id") == owner_id
            ]
            items = [d for d in items if d is not None]
            items.sort(key=lambda d: d.get("created_at", 0))
            return items

        docs = await self.engine.find_many_mongo(self.CLIENTS_COL, {"owner_id": owner_id})
        for doc in docs:
            if cid := doc.get("client_id"):
                self.engine.lmdb_put("clients", cid, doc)
        decrypted = [_decrypt_client_doc(d) for d in docs]
        result = [d for d in decrypted if d is not None]
        result.sort(key=lambda d: d.get("created_at", 0))
        return result

    async def get_all_enabled_clients(
        self, client_type: str | None = None
    ) -> list[dict[str, Any]]:
        mongo_filter: dict[str, Any] = {"enabled": True}
        if client_type is not None:
            mongo_filter["client_type"] = client_type

        docs = await self.engine.find_many_mongo(self.CLIENTS_COL, mongo_filter)
        lmdb_batch: list[tuple[str, Any]] = []
        result: list[dict[str, Any]] = []
        for doc in docs:
            if cid := doc.get("client_id"):
                lmdb_batch.append((cid, doc))
            dec = _decrypt_client_doc(doc)
            if dec is not None:
                result.append(dec)
        self.engine.lmdb_put_many("clients", lmdb_batch)
        return result

    async def set_client_auto_steal(self, client_id: str, enabled: bool) -> None:
        await self.engine.upsert_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
            {"auto_steal": bool(enabled), "updated_at": int(time.time())},
        )

    async def update_client_settings(
        self, client_id: str, patch: dict[str, Any]
    ) -> dict[str, Any] | None:
        existing = await self.get_client(client_id)
        if existing is None:
            return None
        data = {**patch, "updated_at": int(time.time())}
        saved = await self.engine.upsert_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
            data,
        )
        return _decrypt_client_doc(saved)

    async def touch_owner_activity(self, owner_id: int) -> None:
        """Update last_owner_activity on user's clients (resets 48h sleep timer & warning)."""
        now = int(time.time())
        clients = await self.get_user_clients(owner_id)
        patches = [
            {
                "client_id": c["client_id"],
                "last_owner_activity": now,
                "sleeping": False,
                "inactivity_warned_at": 0,
            }
            for c in clients
        ]
        if patches:
            await self.bulk_update_runtime_stats(patches)

    async def set_client_enabled(self, client_id: str, enabled: bool) -> dict[str, Any] | None:
        existing = await self.get_client(client_id)
        if existing is None:
            return None
        saved = await self.engine.upsert_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
            {
                "enabled": enabled,
                "status": "starting" if enabled else "stopped",
                "updated_at": int(time.time()),
            },
        )
        return _decrypt_client_doc(saved)

    async def update_client_runtime(
        self,
        client_id: str,
        *,
        status: str | None = None,
        updates_handled: int | None = None,
        commands_handled: int | None = None,
        last_error: str | None = None,
    ) -> None:
        patch: dict[str, Any] = {"updated_at": int(time.time())}
        if status is not None:
            patch["status"] = status
        if updates_handled is not None:
            patch["updates_handled"] = updates_handled
        if commands_handled is not None:
            patch["commands_handled"] = commands_handled
        if last_error is not None:
            patch["last_error"] = last_error

        existing = await self.engine.find_one_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
        )
        if existing is None:
            return
        await self.engine.upsert_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            {"client_id": client_id},
            patch,
        )

    async def bulk_update_runtime_stats(self, patches: list[dict[str, Any]]) -> int:
        """Batch-flush runtime counters using one MongoDB bulk_write + LMDB batch."""
        return await self.engine.bulk_patch_cached(
            self.CLIENTS_COL,
            "clients",
            "client_id",
            patches,
        )

    async def delete_client(self, client_id: str, owner_id: int | None = None) -> bool:
        existing = await self.get_client(client_id)
        mongo_filter: dict[str, Any] = {"client_id": client_id}
        if owner_id is not None:
            mongo_filter["owner_id"] = owner_id
        deleted = await self.engine.delete_cached(
            self.CLIENTS_COL,
            "clients",
            client_id,
            mongo_filter,
        )
        if deleted > 0:
            target_owner = owner_id or (existing.get("owner_id") if existing else None)
            if target_owner is not None:
                await self._remove_client_from_user_doc(
                    int(target_owner),
                    client_id,
                    existing.get("client_type") if existing else None,
                )
            await self.delete_all_client_peers(client_id)
        return deleted > 0

    async def get_global_counts(self) -> dict[str, int]:
        users_count = await self.engine.collection(self.USERS_COL).estimated_document_count()
        total_clients = await self.engine.collection(self.CLIENTS_COL).estimated_document_count()
        bots_count = await self.engine.collection(self.CLIENTS_COL).count_documents(
            {"client_type": "bot"}
        )
        userbots_count = await self.engine.collection(self.CLIENTS_COL).count_documents(
            {"client_type": "userbot"}
        )
        return {
            "users": users_count,
            "total_clients": total_clients,
            "bots": bots_count,
            "userbots": userbots_count,
        }

    # ------------------------------------------------------------------
    # Tracked Users / Groups / Channels per Hosted Client
    # ------------------------------------------------------------------
    async def bulk_upsert_client_peers(self, records: list[dict[str, Any]]) -> int:
        if not records:
            return 0
        ops: list[UpdateOne] = []
        for rec in records:
            cid = rec["client_id"]
            chat_id = int(rec["chat_id"])
            now = int(rec.get("last_seen", time.time()))
            ops.append(
                UpdateOne(
                    {"client_id": cid, "chat_id": chat_id},
                    {
                        "$set": {
                            "peer_type": rec["peer_type"],
                            "title": rec.get("title", ""),
                            "username": rec.get("username", ""),
                            "last_seen": now,
                        },
                        "$setOnInsert": {
                            "client_id": cid,
                            "chat_id": chat_id,
                            "first_seen": now,
                        },
                    },
                    upsert=True,
                )
            )
        res = await self.engine.collection(self.PEERS_COL).bulk_write(ops, ordered=False)
        return res.upserted_count + res.modified_count

    async def get_client_peer_counts(self, client_id: str) -> dict[str, int]:
        col = self.engine.collection(self.PEERS_COL)
        pipeline = [
            {"$match": {"client_id": client_id}},
            {"$group": {"_id": "$peer_type", "count": {"$sum": 1}}},
        ]
        counts = {"user": 0, "group": 0, "channel": 0}
        cursor = await col.aggregate(pipeline)
        async for row in cursor:
            ptype = row.get("_id")
            if ptype in counts:
                counts[ptype] = int(row.get("count", 0))
        counts["total"] = counts["user"] + counts["group"] + counts["channel"]
        return counts

    async def get_client_peers(
        self,
        client_id: str,
        *,
        peer_type: str | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"client_id": client_id}
        if peer_type in ("user", "group", "channel"):
            query["peer_type"] = peer_type
        cursor = (
            self.engine.collection(self.PEERS_COL)
            .find(query, {"_id": 0})
            .sort("last_seen", -1)
            .skip(skip)
        )
        if limit > 0:
            cursor = cursor.limit(limit)
        return [doc async for doc in cursor]

    async def get_all_broadcast_chat_ids(
        self, client_id: str, peer_type: str | None = None
    ) -> list[int]:
        query: dict[str, Any] = {"client_id": client_id}
        if peer_type in ("user", "group", "channel"):
            query["peer_type"] = peer_type
        cursor = self.engine.collection(self.PEERS_COL).find(query, {"_id": 0, "chat_id": 1})
        return [int(doc["chat_id"]) async for doc in cursor if doc.get("chat_id") is not None]

    async def delete_client_peer(self, client_id: str, chat_id: int) -> bool:
        res = await self.engine.collection(self.PEERS_COL).delete_one(
            {"client_id": client_id, "chat_id": int(chat_id)}
        )
        return res.deleted_count > 0

    async def delete_client_peers_bulk(self, client_id: str, chat_ids: list[int]) -> int:
        if not chat_ids:
            return 0
        res = await self.engine.collection(self.PEERS_COL).delete_many(
            {"client_id": client_id, "chat_id": {"$in": [int(c) for c in chat_ids]}}
        )
        return res.deleted_count

    async def delete_all_client_peers(self, client_id: str) -> int:
        res = await self.engine.collection(self.PEERS_COL).delete_many({"client_id": client_id})
        return res.deleted_count

    # ------------------------------------------------------------------
    # Global Settings: Maintenance, ForceJoin (24h LMDB Cache), Broadcast Msgs
    # ------------------------------------------------------------------
    async def get_maintenance(self) -> dict[str, Any]:
        doc = await self.engine.find_one_cached(
            self.META_COL,
            "meta",
            "maintenance_mode",
            {"key": "maintenance_mode"},
        )
        return doc or {"key": "maintenance_mode", "enabled": False, "reason": ""}

    async def set_maintenance(self, enabled: bool, reason: str = "") -> dict[str, Any]:
        return await self.engine.upsert_cached(
            self.META_COL,
            "meta",
            "maintenance_mode",
            {"key": "maintenance_mode"},
            {
                "key": "maintenance_mode",
                "enabled": bool(enabled),
                "reason": reason,
                "updated_at": int(time.time()),
            },
        )

    async def get_force_join(self) -> dict[str, Any]:
        doc = await self.engine.find_one_cached(
            self.META_COL,
            "meta",
            "force_join",
            {"key": "force_join"},
        )
        return doc or {"key": "force_join", "scope": "off", "chats": []}

    async def set_force_join(self, *, scope: str, chats: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.engine.upsert_cached(
            self.META_COL,
            "meta",
            "force_join",
            {"key": "force_join"},
            {
                "key": "force_join",
                "scope": scope,
                "chats": chats,
                "updated_at": int(time.time()),
            },
        )

    # ------------------------------------------------------------------
    # Affiliate & Referral Program (Invite X users to unlock premium)
    # ------------------------------------------------------------------
    async def get_affiliate_settings(self) -> dict[str, Any]:
        doc = await self.engine.find_one_cached(
            self.META_COL,
            "meta",
            "affiliate_settings",
            {"key": "affiliate_settings"},
        )
        return doc or {
            "key": "affiliate_settings",
            "enabled": False,
            "required_invites": 3,
            "custom_text": "",
        }

    async def set_affiliate_settings(
        self,
        enabled: bool,
        required_invites: int = 3,
        custom_text: str = "",
    ) -> dict[str, Any]:
        return await self.engine.upsert_cached(
            self.META_COL,
            "meta",
            "affiliate_settings",
            {"key": "affiliate_settings"},
            {
                "key": "affiliate_settings",
                "enabled": bool(enabled),
                "required_invites": max(1, int(required_invites)),
                "custom_text": custom_text,
                "updated_at": int(time.time()),
            },
        )

    async def record_referral(
        self, inviter_id: int, new_user_id: int
    ) -> tuple[bool, int, str]:
        if inviter_id == new_user_id:
            return False, 0, "Self-referrals are not permitted."

        col = self.engine.collection(self.REFERRALS_COL)
        existing = await col.find_one({"referred_id": int(new_user_id)})
        if existing is not None:
            return False, 0, "User is already registered."

        now = int(time.time())
        try:
            await col.insert_one(
                {
                    "inviter_id": int(inviter_id),
                    "referred_id": int(new_user_id),
                    "created_at": now,
                }
            )
        except Exception as exc:
            return False, 0, f"Referral record error: {exc}"

        await self.engine.collection(self.USERS_COL).update_one(
            {"user_id": int(inviter_id)},
            {"$inc": {"referral_count": 1}},
        )
        new_count = await col.count_documents({"inviter_id": int(inviter_id)})
        return True, new_count, "Referral recorded successfully."

    async def get_user_referral_info(self, user_id: int) -> dict[str, Any]:
        settings = await self.get_affiliate_settings()
        enabled = bool(settings.get("enabled", False))
        required = int(settings.get("required_invites", 3))

        col = self.engine.collection(self.REFERRALS_COL)
        count = await col.count_documents({"inviter_id": int(user_id)})
        unlocked = (not enabled) or (count >= required) or (int(user_id) in Config.OWNER_IDS)

        return {
            "user_id": int(user_id),
            "invited_count": count,
            "required_invites": required,
            "enabled": enabled,
            "unlocked": unlocked,
            "custom_text": str(settings.get("custom_text") or ""),
        }

    async def get_top_referrers(self, limit: int = 10) -> list[dict[str, Any]]:
        pipeline = [
            {"$group": {"_id": "$inviter_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": max(1, limit)},
        ]
        cursor = self.engine.collection(self.REFERRALS_COL).aggregate(pipeline)
        results: list[dict[str, Any]] = []
        async for doc in cursor:
            uid = doc["_id"]
            user_doc = await self.get_user(int(uid))
            uname = user_doc.get("username") if user_doc else None
            name = user_doc.get("first_name") if user_doc else str(uid)
            results.append({
                "user_id": uid,
                "username": uname,
                "name": name,
                "count": doc["count"],
            })
        return results

    def is_force_join_cached(self, chat_id: int, user_id: int) -> bool:
        val = self.engine.lmdb_get("meta", f"fj:{chat_id}:{user_id}")
        if not isinstance(val, dict):
            return False
        return int(val.get("exp", 0)) > int(time.time())

    def cache_force_join_member(
        self, chat_id: int, user_id: int, ttl_seconds: int = 86400
    ) -> None:
        self.engine.lmdb_put(
            "meta",
            f"fj:{chat_id}:{user_id}",
            {"exp": int(time.time()) + ttl_seconds},
        )

    async def save_broadcast_sent_msgs(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        await self.engine.collection(self.BROADCAST_MSGS_COL).insert_many(
            records, ordered=False
        )

    async def get_expired_broadcast_msgs(self, now: int | None = None) -> list[dict[str, Any]]:
        ts = now or int(time.time())
        cursor = self.engine.collection(self.BROADCAST_MSGS_COL).find(
            {"expires_at": {"$gt": 0, "$lte": ts}},
            {"_id": 1, "sender_id": 1, "chat_id": 1, "message_id": 1},
        )
        return [doc async for doc in cursor]

    async def get_all_broadcast_msgs(self) -> list[dict[str, Any]]:
        cursor = self.engine.collection(self.BROADCAST_MSGS_COL).find(
            {},
            {"_id": 1, "sender_id": 1, "chat_id": 1, "message_id": 1},
        )
        return [doc async for doc in cursor]

    async def delete_broadcast_msg_records(self, object_ids: list[Any]) -> int:
        if not object_ids:
            return 0
        res = await self.engine.collection(self.BROADCAST_MSGS_COL).delete_many(
            {"_id": {"$in": object_ids}}
        )
        return res.deleted_count

    async def clean_lmdb_cache(self) -> dict[str, int]:
        """Remove temporary cache entries (force-join cache, peer caches) and resync."""
        fj_removed = self.engine.lmdb_delete_prefix("meta", "fj:")
        peers_removed = self.engine.lmdb_delete_prefix("tg_peers", "uname:")
        synced = await self.sync_cache()
        return {
            "fj_removed": fj_removed,
            "uname_removed": peers_removed,
            "users_synced": synced["users"],
            "clients_synced": synced["clients"],
        }

    # ------------------------------------------------------------------
    # Ephemeral Conversation State (LMDB-backed, zero Mongo overhead)
    # ------------------------------------------------------------------
    async def get_state(self, user_id: int) -> dict[str, Any] | None:
        val = self.engine.lmdb_get("meta", f"state:{user_id}")
        if not isinstance(val, dict):
            return None
        exp = int(val.get("_exp", 0))
        if exp and exp < int(time.time()):
            self.engine.lmdb_delete("meta", f"state:{user_id}")
            return None
        return val

    async def set_state(
        self, user_id: int, state: dict[str, Any], ttl_seconds: int = 900
    ) -> None:
        payload = {**state, "_exp": int(time.time()) + ttl_seconds}
        self.engine.lmdb_put("meta", f"state:{user_id}", payload)

    async def clear_state(self, user_id: int) -> None:
        self.engine.lmdb_delete("meta", f"state:{user_id}")


bot_db = BotDatabase()
