"""Cryptographic helpers for secrets at rest and webhook verification."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from cryptography.fernet import Fernet, InvalidToken

from config import Config

log = logging.getLogger(__name__)

_ENC_PREFIX = "enc:v1:"
_FERNET_INSTANCE: Fernet | None = None


def _get_fernet() -> Fernet:
    global _FERNET_INSTANCE
    if _FERNET_INSTANCE is not None:
        return _FERNET_INSTANCE

    if Config.SECRET_KEY:
        material = Config.SECRET_KEY.encode("utf-8")
    else:
        # Deterministic fallback derived from API credentials so existing setups keep working;
        # production deployments should still set a dedicated SECRET_KEY in .env.
        material = f"{Config.API_ID}:{Config.API_HASH}:{Config.BOT_TOKEN}".encode("utf-8")
        log.warning(
            "SECRET_KEY is not set in .env; deriving encryption key from bot credentials."
        )

    digest = hashlib.sha256(b"userbot-hosting:v1:" + material).digest()
    key = base64.urlsafe_b64encode(digest)
    _FERNET_INSTANCE = Fernet(key)
    return _FERNET_INSTANCE


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a non-empty secret string with Fernet, returning an enc:v1: token."""
    if not plaintext:
        return ""
    if plaintext.startswith(_ENC_PREFIX):
        return plaintext
    token = _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: str) -> str:
    """Decrypt an enc:v1: secret, or pass through legacy unencrypted values safely."""
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return value
    raw_token = value[len(_ENC_PREFIX) :].encode("ascii")
    try:
        return _get_fernet().decrypt(raw_token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt stored credential (check SECRET_KEY).") from exc


def generate_webhook_secret() -> str:
    """Generate a URL-safe secret token valid for Telegram setWebhook secret_token."""
    return secrets.token_urlsafe(32)


def constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing side-channels."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def redact_secret(text: str) -> str:
    """Mask tokens or long secrets if accidentally included in error messages."""
    if not text:
        return ""
    if len(text) > 120:
        return text[:120] + "..."
    return text
