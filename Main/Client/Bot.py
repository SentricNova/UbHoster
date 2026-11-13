"""Main Kurigram hosting bot client and raw MTProto helper functions."""

from __future__ import annotations

import random
import time

from pyrogram import Client, raw

from config import Config


def create_main_bot() -> Client:
    """Create the main hosting bot Client configured with Main/Plugins."""
    return Client(
        name="UserbotHostingBot",
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        bot_token=Config.BOT_TOKEN,
        ipv6=False,
        in_memory=True,
        workers=Config.BOT_WORKERS,
        max_message_cache_size=100,
        max_topic_cache_size=1,
        skip_updates=True,
        fetch_replies=False,
        fetch_topics=False,
        fetch_stories=False,
        fetch_stickers=False,
        sleep_threshold=15,
        plugins={"root": "Main/Plugins"},
    )


async def raw_ping(client: Client) -> float:
    """Measure MTProto round-trip latency using raw.functions.Ping."""
    start = time.perf_counter()
    await client.invoke(raw.functions.Ping(ping_id=random.randint(1, 2**62)))
    return (time.perf_counter() - start) * 1000.0


async def raw_answer_callback(
    client: Client,
    query_id: int | str,
    text: str | None = None,
    alert: bool = False,
    show_alert: bool | None = None,
) -> None:
    """Answer a callback query directly via raw.functions.messages.SetBotCallbackAnswer."""
    is_alert = alert if show_alert is None else bool(show_alert)
    try:
        await client.invoke(
            raw.functions.messages.SetBotCallbackAnswer(
                query_id=int(query_id),
                alert=is_alert or None,
                message=text,
            )
        )
    except Exception:
        pass
