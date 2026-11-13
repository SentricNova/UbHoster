"""Reusable Kurigram/Pyrogram centralized error handler & logger-group reporter.

Imports all Kurigram RPCError subclasses and classifies every exception into:
  1. Known Telegram/operational errors -> concise, human-friendly explanation.
  2. Code/syntax/unexpected runtime bugs -> full traceback sent to the main
     logger group (never leaked to end users) + safe notice to the caller.
"""

from __future__ import annotations

import functools
import inspect
import logging
import traceback
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Awaitable, Callable

from pyrogram import Client
import pyrogram.errors as tg_errors
from pyrogram.errors import (  # noqa: F401 - explicitly import all error namespaces
    BadRequest,
    Flood,
    Forbidden,
    InternalServerError,
    NotAcceptable,
    RPCError,
    SeeOther,
    ServiceUnavailable,
    Unauthorized,
)
from pyrogram.errors.exceptions.all import exceptions as _ALL_TG_EXCEPTIONS

log = logging.getLogger(__name__)

# Dynamically bind all Kurigram error classes into module namespace for direct inspection
_FATAL_SESSION_NAMES = frozenset(
    {
        "AccessTokenExpired",
        "AccessTokenInvalid",
        "AuthKeyDuplicated",
        "AuthKeyInvalid",
        "AuthKeyPermEmpty",
        "AuthKeyUnregistered",
        "SessionExpired",
        "SessionRevoked",
        "UserDeactivated",
        "UserDeactivatedBan",
    }
)

_HUMAN_ERROR_MAP: dict[str, tuple[str, str]] = {
    "ChatAdminRequired": (
        "Admin Required",
        "This bot isn't an admin in this chat. Make sure the bot is promoted to admin.",
    ),
    "ChatWriteForbidden": (
        "Write Forbidden",
        "This client is not allowed to send messages in this chat.",
    ),
    "RightForbidden": (
        "Permission Missing",
        "This client lacks the required admin right for this action.",
    ),
    "UserAdminInvalid": (
        "Target is Admin",
        "Cannot perform this moderation action on an administrator.",
    ),
    "AdminRankInvalid": (
        "Invalid Admin Rank",
        "The specified admin title or rank is invalid.",
    ),
    "UserNotParticipant": (
        "Not a Member",
        "The target user is not a member of this chat.",
    ),
    "ChannelPrivate": (
        "Private / Inaccessible Chat",
        "This chat is private or the client was removed/banned from it.",
    ),
    "ChannelInvalid": (
        "Invalid Channel",
        "The target channel or supergroup could not be accessed.",
    ),
    "ChatInvalid": (
        "Invalid Chat",
        "The target chat ID or link is invalid.",
    ),
    "PeerIdInvalid": (
        "Peer Not Found",
        "Cannot find that user/chat. Make sure the client has interacted with it.",
    ),
    "UsernameInvalid": (
        "Invalid Username",
        "The provided @username is invalid.",
    ),
    "UsernameNotOccupied": (
        "Username Not Found",
        "No user, group, or channel exists with that @username.",
    ),
    "UserIsBlocked": (
        "Blocked by User",
        "This user has blocked the bot/account.",
    ),
    "InputUserDeactivated": (
        "Deleted Account",
        "The target user account has been deleted.",
    ),
    "UserDeactivated": (
        "Account Deleted",
        "This Telegram account has been deleted.",
    ),
    "UserDeactivatedBan": (
        "Account Banned/Frozen",
        "This Telegram account has been banned or frozen by Telegram.",
    ),
    "SessionRevoked": (
        "Session Revoked",
        "This session was revoked or terminated by the account owner.",
    ),
    "AuthKeyUnregistered": (
        "Session Unregistered",
        "This session key is no longer valid on Telegram.",
    ),
    "AccessTokenExpired": (
        "Bot Token Expired",
        "This bot token has expired or was revoked in @BotFather.",
    ),
    "AccessTokenInvalid": (
        "Invalid Bot Token",
        "This bot token is invalid.",
    ),
    "MessageNotModified": (
        "No Changes",
        "The message content is already identical.",
    ),
    "MessageIdInvalid": (
        "Message Not Found",
        "The target message no longer exists or cannot be accessed.",
    ),
    "MessageDeleteForbidden": (
        "Cannot Delete Message",
        "Missing permission to delete that message.",
    ),
    "BotMethodInvalid": (
        "Bot Method Not Allowed",
        "Bots cannot use this method (user account only).",
    ),
    "UserRestricted": (
        "Account Restricted",
        "This account is currently limited/restricted by Telegram.",
    ),
    "FloodWait": (
        "Rate Limited",
        "Telegram rate limit reached. Please wait a few seconds and try again.",
    ),
    "SlowmodeWait": (
        "Slowmode Active",
        "Slowmode is enabled in this chat. Wait before sending again.",
    ),
}


@dataclass(slots=True)
class HandledError:
    """Structured result returned by classify_error / run_safe."""

    short: str
    description: str
    is_code_error: bool = False
    is_fatal_session: bool = False
    wait_seconds: int = 0
    error_name: str = ""


_MAIN_BOT_REF: Client | None = None
_MAIN_LOGGER_GROUP: int = 0


def configure_error_reporter(main_bot: Client | None, logger_group_id: int) -> None:
    """Bind the main bot instance and logger group for automatic bug reporting."""
    global _MAIN_BOT_REF, _MAIN_LOGGER_GROUP
    _MAIN_BOT_REF = main_bot
    _MAIN_LOGGER_GROUP = int(logger_group_id or 0)


def get_main_bot() -> Client | None:
    """Return the active main bot instance if bound."""
    return _MAIN_BOT_REF


async def send_to_logger_group(text: str, parse_mode=None, **kwargs) -> bool:
    """Send an operational or audit notification to the main bot's logger group."""
    from config import Config
    target_group = _MAIN_LOGGER_GROUP or Config.LOGGER_GROUP
    if not target_group or _MAIN_BOT_REF is None:
        return False
    try:
        from pyrogram import enums
        pm = parse_mode if parse_mode is not None else enums.ParseMode.HTML
        await _MAIN_BOT_REF.send_message(target_group, text, parse_mode=pm, **kwargs)
        return True
    except Exception as exc:
        log.debug("Failed sending to logger group: %s", exc)
        return False


def classify_error(exc: BaseException) -> HandledError:
    """Classify any Kurigram RPCError or Python exception into a human-friendly HandledError."""
    err_name = type(exc).__name__

    if isinstance(exc, (tg_errors.FloodWait, tg_errors.SlowmodeWait)):
        wait_s = int(getattr(exc, "value", 5) or 5)
        short, _ = _HUMAN_ERROR_MAP.get(err_name, ("Rate Limited", ""))
        return HandledError(
            short=short,
            description=f"Telegram rate limit active. Please wait {wait_s} seconds.",
            wait_seconds=wait_s,
            error_name=err_name,
        )

    if isinstance(exc, RPCError):
        is_fatal = err_name in _FATAL_SESSION_NAMES
        if err_name in _HUMAN_ERROR_MAP:
            short, desc = _HUMAN_ERROR_MAP[err_name]
        else:
            raw_msg = getattr(exc, "MESSAGE", "") or str(exc)
            short = err_name
            desc = raw_msg.format(value=getattr(exc, "value", "")) if "{value}" in raw_msg else raw_msg
        return HandledError(
            short=short,
            description=desc,
            is_fatal_session=is_fatal,
            error_name=err_name,
        )

    if isinstance(exc, (ValueError, KeyError)) and not isinstance(exc, LookupError):
        return HandledError(
            short="Invalid Input",
            description=str(exc) or "Invalid input provided.",
            is_code_error=False,
            error_name=err_name,
        )

    # Syntax / Type / Attribute / Name / unexpected runtime bug -> report to main bot owner
    return HandledError(
        short="Internal Code Error",
        description=(
            "This error was related to the code and has been reported to the bot owner. "
            "They will fix it in the meantime."
        ),
        is_code_error=True,
        error_name=err_name,
    )


async def report_code_error_to_logger(
    exc: BaseException,
    *,
    context_label: str = "",
    main_bot: Client | None = None,
    logger_group: int | None = None,
) -> None:
    """Send full traceback of a code/syntax error to the main bot's logger group."""
    bot = main_bot or _MAIN_BOT_REF
    group_id = logger_group if logger_group is not None else _MAIN_LOGGER_GROUP
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log.error("Code error in %s:\n%s", context_label or "handler", tb_text)

    if bot is None or not group_id:
        return

    header = (
        f"🚨 **Code Error Report**\n"
        f"• **Context:** `{context_label or 'unknown'}`\n"
        f"• **Error:** `{type(exc).__name__}: {str(exc)[:200]}`"
    )
    try:
        if len(tb_text) <= 3200:
            await bot.send_message(
                group_id,
                f"{header}\n\n<pre><code>{tb_text}</code></pre>",
            )
        else:
            bio = BytesIO(tb_text.encode("utf-8"))
            bio.name = f"error_{type(exc).__name__}.log"
            await bot.send_document(group_id, bio, caption=header)
    except Exception as report_exc:
        log.debug("Failed sending error report to logger group %s: %s", group_id, report_exc)


async def run_safe(
    target: Callable[..., Awaitable[Any]] | Awaitable[Any],
    *args: Any,
    context_label: str = "",
    main_bot: Client | None = None,
    logger_group: int | None = None,
    **kwargs: Any,
) -> tuple[bool, Any, HandledError | None]:
    """Execute an async callable or coroutine with centralized Kurigram & code error handling.

    Returns (ok, result, handled_error).
    """
    try:
        if inspect.isawaitable(target):
            res = await target
        else:
            res = await target(*args, **kwargs)
        return True, res, None
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        handled = classify_error(exc)
        if handled.is_code_error:
            await report_code_error_to_logger(
                exc,
                context_label=context_label or getattr(target, "__name__", "task"),
                main_bot=main_bot,
                logger_group=logger_group,
            )
        return False, None, handled


def safe_handler(context_label: str = "") -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorator wrapping any async command/callback handler with run_safe."""

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            ok, res, err = await run_safe(
                func,
                *args,
                context_label=context_label or func.__name__,
                **kwargs,
            )
            if not ok and err is not None:
                # Best-effort reply if first or second arg is a Context / Message
                for obj in args[:2]:
                    if hasattr(obj, "reply_raw"):
                        try:
                            await obj.reply_raw(f"⚠️ **{err.short}:** {err.description}", reply=True)
                        except Exception:
                            pass
                        break
                    if hasattr(obj, "reply_text"):
                        try:
                            await obj.reply_text(f"⚠️ **{err.short}:** {err.description}", quote=True)
                        except Exception:
                            pass
                        break
            return res

        return wrapper

    return decorator


def known_kurigram_error_count() -> int:
    """Return total number of error classes registered in Kurigram."""
    return len(_ALL_TG_EXCEPTIONS)
