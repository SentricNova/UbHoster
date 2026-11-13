"""Bounded-memory LMDB-backed Pyrogram Storage engine for scalable userbot hosting.

Avoids Pyrogram's MemoryStorage (which grows peer dicts unboundedly in RAM) and
avoids per-client SQLite connections/files when running 500+ userbots.
"""

from __future__ import annotations

import base64
import logging
import struct
import time
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from pyrogram import raw, utils
from pyrogram.storage.sqlite_storage import PROD, TEST, get_input_peer
from pyrogram.storage.storage import Storage, UpdateState

from config import Config
from Main.Database.mongodb import MongoLMDBEngine, db_engine

log = logging.getLogger(__name__)

PROD6 = {
    1: "2001:b28:f23d:f001::a",
    2: "2001:67c:4e8:f002::a",
    3: "2001:b28:f23d:f003::a",
    4: "2001:67c:4e8:f004::a",
    5: "2001:b28:f23f:f005::a",
}

TEST6 = {
    1: "2001:b28:f23d:f001::e",
    2: "2001:67c:4e8:f002::e",
    3: "2001:b28:f23d:f003::e",
}


class LMDBStorage(Storage):
    """Kurigram Storage engine backed by shared LMDB + bounded in-memory LRU peer cache."""

    USERNAME_TTL = 8 * 60 * 60

    def __init__(
        self,
        name: str,
        *,
        session_string: str | None = None,
        engine: MongoLMDBEngine = db_engine,
        max_peers_in_mem: int = Config.MAX_PEERS_IN_MEM,
        ipv6: bool = True,
    ) -> None:
        self.name = name
        self.session_string = session_string
        self.engine = engine
        self.max_peers_in_mem = max(32, max_peers_in_mem)
        self.ipv6 = ipv6

        self._session_key = f"sess:{name}"
        self._peer_prefix = f"peer:{name}:"
        self._uname_prefix = f"uname:{name}:"
        self._phone_prefix = f"phone:{name}:"
        self._state_prefix = f"state:{name}:"

        self._session: dict[str, Any] = {}
        # Bounded LRU in RAM for hot peer IDs
        self._peer_lru: OrderedDict[int, tuple[int, str, str | None]] = OrderedDict()

    def _lru_put(self, peer_id: int, entry: tuple[int, str, str | None]) -> None:
        if peer_id in self._peer_lru:
            self._peer_lru.move_to_end(peer_id)
        self._peer_lru[peer_id] = entry
        while len(self._peer_lru) > self.max_peers_in_mem:
            self._peer_lru.popitem(last=False)

    async def open(self) -> None:
        self.engine.ensure_lmdb()
        prod_table = PROD6 if self.ipv6 else PROD
        test_table = TEST6 if self.ipv6 else TEST

        stored = self.engine.lmdb_get("tg_sessions", self._session_key)
        if isinstance(stored, dict):
            self._session = stored
        else:
            self._session = {
                "dc_id": 2,
                "server_address": prod_table.get(2, "2001:67c:4e8:f002::a" if self.ipv6 else "149.154.167.51"),
                "port": 443,
                "api_id": None,
                "test_mode": False,
                "auth_key": None,
                "date": 0,
                "user_id": None,
                "is_bot": False,
            }

        if self.session_string:
            if len(self.session_string) in (
                self.SESSION_STRING_SIZE,
                self.SESSION_STRING_SIZE_64,
            ):
                dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
                    (
                        self.OLD_SESSION_STRING_FORMAT
                        if len(self.session_string) == self.SESSION_STRING_SIZE
                        else self.OLD_SESSION_STRING_FORMAT_64
                    ),
                    base64.urlsafe_b64decode(
                        self.session_string + "=" * (-len(self.session_string) % 4)
                    ),
                )
                api_id = self._session.get("api_id")
            else:
                dc_id, api_id, test_mode, auth_key, user_id, is_bot = struct.unpack(
                    self.SESSION_STRING_FORMAT,
                    base64.urlsafe_b64decode(
                        self.session_string + "=" * (-len(self.session_string) % 4)
                    ),
                )

            table = test_table if test_mode else prod_table
            self._session.update(
                {
                    "dc_id": int(dc_id),
                    "server_address": table.get(int(dc_id), prod_table.get(2, "2001:67c:4e8:f002::a" if self.ipv6 else "149.154.167.51")),
                    "port": 80 if test_mode else 443,
                    "api_id": api_id,
                    "test_mode": bool(test_mode),
                    "auth_key": base64.b64encode(auth_key).decode("ascii"),
                    "user_id": int(user_id),
                    "is_bot": bool(is_bot),
                    "date": 0,
                }
            )
            self.engine.lmdb_put("tg_sessions", self._session_key, self._session)

        # Synchronize stored server_address with the active ipv6 setting
        current_addr = str(self._session.get("server_address") or "")
        dc = int(self._session.get("dc_id", 2) or 2)
        is_test = bool(self._session.get("test_mode", False))
        if self.ipv6 and ":" not in current_addr:
            table = TEST6 if is_test else PROD6
            self._session["server_address"] = table.get(dc, "2001:67c:4e8:f002::a")
            self.engine.lmdb_put("tg_sessions", self._session_key, self._session)
        elif not self.ipv6 and ":" in current_addr:
            table = TEST if is_test else PROD
            self._session["server_address"] = table.get(dc, "149.154.167.51")
            self.engine.lmdb_put("tg_sessions", self._session_key, self._session)

    async def save(self) -> None:
        self._session["date"] = int(time.time())
        if self.engine.is_lmdb_open:
            self.engine.lmdb_put("tg_sessions", self._session_key, self._session)

    async def close(self) -> None:
        self._peer_lru.clear()

    async def delete(self) -> None:
        self._peer_lru.clear()
        if self.engine.is_lmdb_open:
            self.engine.lmdb_delete("tg_sessions", self._session_key)
            self.engine.lmdb_delete_prefix("tg_peers", self._peer_prefix)
            self.engine.lmdb_delete_prefix("tg_peers", self._uname_prefix)
            self.engine.lmdb_delete_prefix("tg_peers", self._phone_prefix)
            self.engine.lmdb_delete_prefix("tg_states", self._state_prefix)

    async def update_peers(self, peers: Iterable[tuple[int, int, str, str | None]]) -> None:
        now = int(time.time())
        batch: list[tuple[str, Any]] = []
        for peer_id, access_hash, peer_type, phone_number in peers:
            entry = (int(access_hash), str(peer_type), phone_number)
            self._lru_put(int(peer_id), entry)
            batch.append(
                (
                    f"{self._peer_prefix}{peer_id}",
                    {
                        "id": int(peer_id),
                        "access_hash": int(access_hash),
                        "type": str(peer_type),
                        "phone": phone_number,
                        "ts": now,
                    },
                )
            )
            if phone_number:
                batch.append((f"{self._phone_prefix}{phone_number}", int(peer_id)))
        self.engine.lmdb_put_many("tg_peers", batch)

    async def update_usernames(self, usernames: Iterable[tuple[int, list[str | None]]]) -> None:
        now = int(time.time())
        batch: list[tuple[str, Any]] = []
        for peer_id, names in usernames:
            for uname in names:
                if uname:
                    batch.append(
                        (
                            f"{self._uname_prefix}{uname.lower()}",
                            {"id": int(peer_id), "ts": now},
                        )
                    )
        self.engine.lmdb_put_many("tg_peers", batch)

    async def get_update_states(
        self, ids: int | Iterable[int] | None = None
    ) -> list[UpdateState]:
        if ids is None:
            docs = self.engine.lmdb_scan("tg_states", prefix=self._state_prefix)
        else:
            state_ids = (ids,) if isinstance(ids, int) else tuple(ids)
            docs = []
            for sid in state_ids:
                d = self.engine.lmdb_get("tg_states", f"{self._state_prefix}{sid}")
                if d is not None:
                    docs.append(d)

        docs.sort(key=lambda d: d.get("date") or 0)
        return [
            UpdateState(
                id=d["id"],
                pts=d.get("pts"),
                qts=d.get("qts"),
                date=d.get("date"),
                seq=d.get("seq"),
            )
            for d in docs
        ]

    async def set_update_state(self, update_state: UpdateState | Iterable[UpdateState]) -> None:
        states = [update_state] if isinstance(update_state, UpdateState) else update_state
        batch: list[tuple[str, Any]] = []
        for st in states:
            key = f"{self._state_prefix}{st.id}"
            existing = self.engine.lmdb_get("tg_states", key) or {}
            merged = {
                "id": st.id,
                "pts": st.pts if st.pts is not None else existing.get("pts"),
                "qts": st.qts if st.qts is not None else existing.get("qts"),
                "date": st.date if st.date is not None else existing.get("date"),
                "seq": st.seq if st.seq is not None else existing.get("seq"),
            }
            batch.append((key, merged))
        self.engine.lmdb_put_many("tg_states", batch)

    async def delete_update_state(self, state_id: int | Iterable[int]) -> None:
        ids = (state_id,) if isinstance(state_id, int) else tuple(state_id)
        for sid in ids:
            self.engine.lmdb_delete("tg_states", f"{self._state_prefix}{sid}")

    async def get_peer_by_id(self, peer_id: int) -> raw.base.InputPeer:
        cached = self._peer_lru.get(peer_id)
        if cached is not None:
            self._peer_lru.move_to_end(peer_id)
            return get_input_peer(peer_id, cached[0], cached[1])

        doc = self.engine.lmdb_get("tg_peers", f"{self._peer_prefix}{peer_id}")
        if doc is None:
            raise KeyError(f"ID not found: {peer_id}")

        self._lru_put(peer_id, (doc["access_hash"], doc["type"], doc.get("phone")))
        return get_input_peer(peer_id, doc["access_hash"], doc["type"])

    async def get_peer_by_username(self, username: str) -> raw.base.InputPeer:
        uname_doc = self.engine.lmdb_get("tg_peers", f"{self._uname_prefix}{username.lower()}")
        if uname_doc is None:
            raise KeyError(f"Username not found: {username}")

        if abs(time.time() - uname_doc.get("ts", 0)) > self.USERNAME_TTL:
            raise KeyError(f"Username expired: {username}")

        return await self.get_peer_by_id(uname_doc["id"])

    async def get_peer_by_phone_number(self, phone_number: str) -> raw.base.InputPeer:
        peer_id = self.engine.lmdb_get("tg_peers", f"{self._phone_prefix}{phone_number}")
        if peer_id is None:
            raise KeyError(f"Phone number not found: {phone_number}")

        return await self.get_peer_by_id(int(peer_id))

    async def _accessor(self, key: str, value: Any = object) -> Any:
        if value is object:
            val = self._session.get(key)
            if key == "auth_key" and isinstance(val, str):
                return base64.b64decode(val.encode("ascii"))
            return val

        if key == "auth_key" and isinstance(value, (bytes, bytearray)):
            self._session[key] = base64.b64encode(bytes(value)).decode("ascii")
        else:
            self._session[key] = value
        if self.engine.is_lmdb_open:
            self.engine.lmdb_put("tg_sessions", self._session_key, self._session)
        return value

    async def dc_id(self, value: int | type[object] = object) -> int:
        return await self._accessor("dc_id", value)

    async def server_address(self, value: str | type[object] = object) -> str:
        return await self._accessor("server_address", value)

    async def port(self, value: int | type[object] = object) -> int:
        return await self._accessor("port", value)

    async def api_id(self, value: int | type[object] = object) -> int:
        return await self._accessor("api_id", value)

    async def test_mode(self, value: bool | type[object] = object) -> bool:
        return await self._accessor("test_mode", value)

    async def auth_key(self, value: bytes | type[object] = object) -> bytes:
        return await self._accessor("auth_key", value)

    async def date(self, value: int | type[object] = object) -> int:
        return await self._accessor("date", value)

    async def user_id(self, value: int | type[object] = object) -> int:
        return await self._accessor("user_id", value)

    async def is_bot(self, value: bool | type[object] = object) -> bool:
        return await self._accessor("is_bot", value)
