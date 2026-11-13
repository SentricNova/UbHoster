"""Application configuration loaded from environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _parse_int_list(raw: str) -> list[int]:
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            values.append(int(part))
    return values


class Config:
    """Centralized runtime settings."""

    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

    # Encryption key for session_string / bot_token at rest (recommended: 32+ char random secret)
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    MONGO_URI: str = (
        os.getenv("MONGO_URI") or os.getenv("MONGO_DB") or "mongodb://localhost:27017"
    )
    MONGO_DB_NAME: str = (
        os.getenv("MONGO_DB_NAME") or os.getenv("MONGODB_DB_NAME") or "userbot_hosting"
    )
    LMDB_PATH: str = os.getenv("LMDB_PATH", str(BASE_DIR / "data" / "lmdb_cache"))
    LMDB_MAP_SIZE: int = int(os.getenv("LMDB_MAP_SIZE", str(1024 * 1024 * 1024)))  # 1 GB virtual map
    RAW_INFO_DIR: Path = Path(os.getenv("RAW_INFO_DIR", str(BASE_DIR / "data" / "raw_info")))

    OWNER_IDS: list[int] = list(
        dict.fromkeys(
            _parse_int_list(os.getenv("OWNER_IDS", ""))
            + _parse_int_list(os.getenv("OWNER_ID", ""))
            + _parse_int_list(os.getenv("SUDO_USERS", ""))
        )
    )
    LOGGER_GROUP: int = int(os.getenv("LOGGER_GROUP", "0") or "0")
    MAX_CLIENTS_PER_USER: int = int(os.getenv("MAX_CLIENTS_PER_USER", "5"))
    MAX_SUDO_PER_CLIENT: int = int(os.getenv("MAX_SUDO_PER_CLIENT", "10"))
    INACTIVE_SLEEP_SECONDS: int = int(os.getenv("INACTIVE_SLEEP_SECONDS", str(48 * 3600)))

    # Worker & Cache tuning for 500+ userbots + 10,000+ bots
    BOT_WORKERS: int = int(os.getenv("BOT_WORKERS", "4"))
    CLIENT_WORKERS: int = int(os.getenv("CLIENT_WORKERS", "1"))
    MAX_PEERS_IN_MEM: int = int(os.getenv("MAX_PEERS_IN_MEM", "256"))

    # Horizontal sharding for MTProto userbot processes
    SHARD_INDEX: int = int(os.getenv("SHARD_INDEX", "0"))
    TOTAL_SHARDS: int = max(1, int(os.getenv("TOTAL_SHARDS", "1")))

    # Webhook base URL for 10,000-bot zero-socket hosting (e.g. https://bots.example.com)
    # Leave empty for local development to run hosted bots in raw MTProto fallback mode.
    WEBHOOK_BASE_URL: str = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")

    # Rate limits & security
    SEND_CODE_COOLDOWN: float = float(os.getenv("SEND_CODE_COOLDOWN", "15.0"))
    WIZARD_TTL_SECONDS: float = float(os.getenv("WIZARD_TTL_SECONDS", "600.0"))
    HTTP_MAX_BODY_BYTES: int = int(os.getenv("HTTP_MAX_BODY_BYTES", str(1024 * 1024)))

    # HTTP server for UptimeRobot & Bot Webhooks
    HTTP_HOST: str = os.getenv("HTTP_HOST", "0.0.0.0")
    HTTP_PORT: int = int(os.getenv("PORT", os.getenv("HTTP_PORT", "8080")))

    # Welcome banner media URL for /start
    START_MEDIA_URL: str = os.getenv(
        "START_MEDIA_URL",
        "https://i.ibb.co/FLj8NKqX/file-00000000f5708211b043eaf4ce177e49.png",
    ).strip()

    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def validate(cls) -> None:
        missing: list[str] = []
        if not cls.API_ID:
            missing.append("API_ID")
        if not cls.API_HASH or cls.API_HASH == "your_api_hash_here":
            missing.append("API_HASH")
        if not cls.BOT_TOKEN or cls.BOT_TOKEN == "your_bot_token_here":
            missing.append("BOT_TOKEN")
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}. "
                "Please configure them in .env or the environment."
            )
        if not (0 <= cls.SHARD_INDEX < cls.TOTAL_SHARDS):
            raise RuntimeError(
                f"Invalid SHARD_INDEX={cls.SHARD_INDEX} for TOTAL_SHARDS={cls.TOTAL_SHARDS}"
            )
