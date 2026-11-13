"""Async PyMongo + LMDB hybrid database engine with batched bulk persistence."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import lmdb
import orjson
from pymongo import AsyncMongoClient, UpdateOne
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from config import Config

log = logging.getLogger(__name__)


def _encode(data: Any) -> bytes:
    return orjson.dumps(data)


def _decode(raw_bytes: bytes | None) -> Any:
    if raw_bytes is None:
        return None
    return orjson.loads(raw_bytes)


class MongoLMDBEngine:
    """Manages an AsyncMongoClient paired with an LMDB write-through/read-through cache."""

    SUB_DBS = (
        "users",
        "clients",
        "meta",
        "tg_sessions",
        "tg_peers",
        "tg_states",
    )

    def __init__(
        self,
        mongo_uri: str = Config.MONGO_URI,
        db_name: str = Config.MONGO_DB_NAME,
        lmdb_path: str = Config.LMDB_PATH,
        lmdb_map_size: int = Config.LMDB_MAP_SIZE,
    ) -> None:
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.lmdb_path = Path(lmdb_path)
        self.lmdb_map_size = lmdb_map_size

        self._mongo: AsyncMongoClient | None = None
        self._db: AsyncDatabase | None = None
        self._env: lmdb.Environment | None = None
        self._sub_dbs: dict[str, Any] = {}

    @property
    def is_connected(self) -> bool:
        return self._mongo is not None and self._env is not None

    @property
    def is_lmdb_open(self) -> bool:
        return self._env is not None

    @property
    def mongo_db(self) -> AsyncDatabase:
        if self._db is None:
            raise RuntimeError("MongoLMDBEngine is not initialized. Call connect() first.")
        return self._db

    def collection(self, name: str) -> AsyncCollection:
        return self.mongo_db[name]

    def _get_sub_db(self, name: str):
        if self._env is None:
            raise RuntimeError("LMDB environment is not opened. Call connect() first.")
        if name not in self._sub_dbs:
            self._sub_dbs[name] = self._env.open_db(name.encode("utf-8"))
        return self._sub_dbs[name]

    def ensure_lmdb(self) -> None:
        """Open LMDB environment (idempotent)."""
        if self._env is None:
            self.lmdb_path.mkdir(parents=True, exist_ok=True)
            self._env = lmdb.open(
                str(self.lmdb_path),
                map_size=self.lmdb_map_size,
                max_dbs=16,
                max_readers=512,
                writemap=False,
                sync=True,
                metasync=True,
            )
            for name in self.SUB_DBS:
                self._get_sub_db(name)
            log.info("LMDB cache opened at %s", self.lmdb_path)

    async def connect(self) -> None:
        """Open both LMDB environment and AsyncMongoClient connection."""
        self.ensure_lmdb()

        if self._mongo is None:
            self._mongo = AsyncMongoClient(
                self.mongo_uri,
                maxPoolSize=50,
                serverSelectionTimeoutMS=10000,
                connectTimeoutMS=10000,
            )
            self._db = self._mongo[self.db_name]
            for attempt in range(1, 4):
                try:
                    await self._mongo.admin.command({"ping": 1})
                    log.info("Connected to MongoDB database '%s'", self.db_name)
                    break
                except Exception as exc:
                    if attempt == 3:
                        raise
                    log.warning("MongoDB ping failed (attempt %d/3): %s; retrying...", attempt, exc)
                    await asyncio.sleep(2)

    async def close(self) -> None:
        """Close MongoDB and LMDB handles gracefully."""
        if self._mongo is not None:
            await self._mongo.close()
            self._mongo = None
            self._db = None
        if self._env is not None:
            self._env.sync()
            self._env.close()
            self._env = None
            self._sub_dbs.clear()

    async def ping(self) -> tuple[float, float]:
        """Return (mongo_latency_ms, lmdb_latency_ms)."""
        t0 = time.perf_counter()
        await self._mongo.admin.command({"ping": 1})
        mongo_ms = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        self.lmdb_get("meta", "__health__")
        lmdb_ms = (time.perf_counter() - t1) * 1000.0
        return mongo_ms, lmdb_ms

    # ------------------------------------------------------------------
    # Low-level LMDB operations
    # ------------------------------------------------------------------
    def lmdb_get(self, db_name: str, key: str) -> Any:
        sub_db = self._get_sub_db(db_name)
        with self._env.begin(db=sub_db, write=False) as txn:
            return _decode(txn.get(key.encode("utf-8")))

    def lmdb_put(self, db_name: str, key: str, value: Any) -> None:
        sub_db = self._get_sub_db(db_name)
        payload = _encode(value)
        with self._env.begin(db=sub_db, write=True) as txn:
            txn.put(key.encode("utf-8"), payload)

    def lmdb_put_many(self, db_name: str, items: list[tuple[str, Any]]) -> None:
        if not items:
            return
        sub_db = self._get_sub_db(db_name)
        with self._env.begin(db=sub_db, write=True) as txn:
            for key, value in items:
                txn.put(key.encode("utf-8"), _encode(value))

    def lmdb_delete(self, db_name: str, key: str) -> bool:
        sub_db = self._get_sub_db(db_name)
        with self._env.begin(db=sub_db, write=True) as txn:
            return bool(txn.delete(key.encode("utf-8")))

    def lmdb_delete_prefix(self, db_name: str, prefix: str) -> int:
        sub_db = self._get_sub_db(db_name)
        prefix_bytes = prefix.encode("utf-8")
        deleted = 0
        with self._env.begin(db=sub_db, write=True) as txn:
            cursor = txn.cursor()
            if not cursor.set_range(prefix_bytes):
                return 0
            keys_to_delete: list[bytes] = []
            for key_bytes, _ in cursor:
                if not key_bytes.startswith(prefix_bytes):
                    break
                keys_to_delete.append(bytes(key_bytes))
            for k in keys_to_delete:
                if txn.delete(k):
                    deleted += 1
        return deleted

    def lmdb_scan(self, db_name: str, prefix: str = "") -> list[dict[str, Any]]:
        sub_db = self._get_sub_db(db_name)
        prefix_bytes = prefix.encode("utf-8")
        results: list[dict[str, Any]] = []
        with self._env.begin(db=sub_db, write=False) as txn:
            cursor = txn.cursor()
            if prefix_bytes:
                if not cursor.set_range(prefix_bytes):
                    return results
            for key_bytes, val_bytes in cursor:
                if prefix_bytes and not key_bytes.startswith(prefix_bytes):
                    break
                decoded = _decode(val_bytes)
                if decoded is not None:
                    results.append(decoded)
        return results

    def lmdb_count(self, db_name: str) -> int:
        sub_db = self._get_sub_db(db_name)
        with self._env.begin(db=sub_db, write=False) as txn:
            return txn.stat()["entries"]

    # ------------------------------------------------------------------
    # Hybrid Async MongoDB + LMDB operations
    # ------------------------------------------------------------------
    async def find_one_cached(
        self,
        collection_name: str,
        lmdb_db: str,
        cache_key: str,
        mongo_filter: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Read-through cache lookup: LMDB first, then MongoDB."""
        cached = self.lmdb_get(lmdb_db, cache_key)
        if cached is not None:
            return cached

        doc = await self.collection(collection_name).find_one(mongo_filter, {"_id": 0})
        if doc is not None:
            self.lmdb_put(lmdb_db, cache_key, doc)
        return doc

    async def upsert_cached(
        self,
        collection_name: str,
        lmdb_db: str,
        cache_key: str,
        mongo_filter: dict[str, Any],
        document: dict[str, Any],
    ) -> dict[str, Any]:
        """Write-through upsert to MongoDB and LMDB."""
        await self.collection(collection_name).update_one(
            mongo_filter,
            {"$set": document},
            upsert=True,
        )
        cached = self.lmdb_get(lmdb_db, cache_key)
        if isinstance(cached, dict):
            final_doc = {**cached, **document}
        else:
            merged = await self.collection(collection_name).find_one(mongo_filter, {"_id": 0})
            final_doc = merged if merged is not None else document
        self.lmdb_put(lmdb_db, cache_key, final_doc)
        return final_doc

    async def bulk_patch_cached(
        self,
        collection_name: str,
        lmdb_db: str,
        key_field: str,
        patches: list[dict[str, Any]],
    ) -> int:
        """Batch-update multiple documents in MongoDB via one bulk_write and update LMDB."""
        if not patches:
            return 0

        ops: list[UpdateOne] = []
        lmdb_updates: list[tuple[str, Any]] = []
        for patch in patches:
            key_val = patch.get(key_field)
            if key_val is None:
                continue
            cache_key = str(key_val)
            fields = {k: v for k, v in patch.items() if k != key_field}
            if not fields:
                continue
            ops.append(UpdateOne({key_field: key_val}, {"$set": fields}))
            existing = self.lmdb_get(lmdb_db, cache_key)
            if isinstance(existing, dict):
                lmdb_updates.append((cache_key, {**existing, **fields}))

        if not ops:
            return 0

        res = await self.collection(collection_name).bulk_write(ops, ordered=False)
        self.lmdb_put_many(lmdb_db, lmdb_updates)
        return res.modified_count + res.upserted_count

    async def delete_cached(
        self,
        collection_name: str,
        lmdb_db: str,
        cache_key: str,
        mongo_filter: dict[str, Any],
    ) -> int:
        """Delete from both MongoDB and LMDB."""
        res = await self.collection(collection_name).delete_one(mongo_filter)
        self.lmdb_delete(lmdb_db, cache_key)
        return res.deleted_count

    async def find_many_mongo(
        self,
        collection_name: str,
        mongo_filter: dict[str, Any] | None = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        cursor = self.collection(collection_name).find(mongo_filter or {}, {"_id": 0})
        if limit > 0:
            cursor = cursor.limit(limit)
        return [doc async for doc in cursor]

    async def sync_collection_to_lmdb(
        self,
        collection_name: str,
        lmdb_db: str,
        key_field: str,
    ) -> int:
        """Warm/refresh an LMDB sub-database from MongoDB."""
        docs = await self.find_many_mongo(collection_name)
        sub_db = self._get_sub_db(lmdb_db)
        count = 0
        with self._env.begin(db=sub_db, write=True) as txn:
            for doc in docs:
                key_val = doc.get(key_field)
                if key_val is None:
                    continue
                txn.put(str(key_val).encode("utf-8"), _encode(doc))
                count += 1
        return count


db_engine = MongoLMDBEngine()
