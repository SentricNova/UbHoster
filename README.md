# Userbot & Bot Hosting Platform (Kurigram Raw + Webhook Scale Engine)

A Telegram bot & userbot hosting platform engineered for **500+ MTProto userbots + 10,000+ hosted bots** with **low RAM**, **encrypted credentials at rest**, **LMDB bounded peer storage**, and **aiohttp** endpoints for **UptimeRobot** and **Bot API Webhooks**.

## Architecture Highlights
- **10,000+ Hosted Bots (Zero-Socket Webhook Mode)**: When `WEBHOOK_BASE_URL` is configured, all hosted bots register Telegram webhooks with cryptographic `X-Telegram-Bot-Api-Secret-Token` verification (`hmac.compare_digest`) and share a single `aiohttp.ClientSession` pool (~1 KB RAM per bot). Falls back automatically to raw MTProto mode when `WEBHOOK_BASE_URL` is empty for local development.
- **500+ Hosted Userbots (Raw MTProto + Bounded `LMDBStorage`)**:
  - Uses [`LMDBStorage`](file:///d:/Projects/AntiGravity/Userbot-Hosting/Main/utils/lmdb_storage.py) instead of Pyrogram's unbounded `MemoryStorage`, with an LRU peer cap (`MAX_PEERS_IN_MEM=256`).
  - Disables heavy eager fetching (`fetch_replies=False`, `fetch_topics=False`, `fetch_stories=False`, `fetch_stickers=False`, `max_message_cache_size=1`).
  - Uses one [`RawUpdateHandler`](file:///d:/Projects/AntiGravity/Userbot-Hosting/Main/ClientPlugin/loader.py) per userbot to skip `Message` parsing on non-command updates.
  - Supports horizontal sharding across processes/containers via `SHARD_INDEX` and `TOTAL_SHARDS`.
- **Security Hardening**:
  - Fernet encryption at rest for `session_string`, `bot_token`, and `webhook_secret` (`Main/utils/crypto.py`).
  - Anti-ban `/addacc` **Rich Message Table** (`InputRichBlockTable` + `RichTextButton`) with green/red buttons + inline keypad so phone numbers & OTPs are never sent in chat.
  - Per-user `send_code` cooldown, keypad lock against race conditions, auto-expiring wizards, and 1 MB HTTP body cap.
- **Batched DB Persistence**: Dirty client stats flush every 60s via one MongoDB `bulk_write` + LMDB batch update.

## Quick Start

1. Configure `.env` (`API_ID`, `API_HASH`, `BOT_TOKEN`, `SECRET_KEY`, `MONGO_URI`, and optionally `WEBHOOK_BASE_URL`).
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Run database migration & LMDB warmup:
   ```bash
   python -m Main.migrate
   ```
4. Start the platform:
   ```bash
   python -m Main
   ```

## HTTP Endpoints
- `GET` / `HEAD` `/` — UptimeRobot ping & shard status
- `GET` / `HEAD` `/health` — DB latency, hosted bot/userbot counts, RAM usage
- `POST` `/webhook/{client_id}` — Secret-token-verified Bot API webhook receiver

## Docker Deployment
```bash
docker compose up -d --build
```
