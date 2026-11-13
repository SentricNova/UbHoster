"""Database migration & LMDB cache warmup script.

Run with:
    python -m Main.migrate
"""

from __future__ import annotations

import asyncio
import logging

from config import Config
from Main.Client.Mongo import bot_db

logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("Main.migrate")


async def run_migration() -> None:
    log.info("Starting database migration and LMDB cache sync...")
    await bot_db.connect()
    try:
        await bot_db.ensure_indexes()
        synced = await bot_db.sync_cache()
        log.info(
            "Migration complete. Indexes ensured; LMDB synced (users=%d, clients=%d).",
            synced["users"],
            synced["clients"],
        )
    finally:
        await bot_db.close()


def main() -> None:
    asyncio.run(run_migration())


if __name__ == "__main__":
    main()
