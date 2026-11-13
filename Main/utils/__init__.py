"""Shared utility exports with lazy loading to prevent package import cycles."""

from __future__ import annotations

import importlib
from typing import Any

_EXPORTS: dict[str, tuple[str, str]] = {
    "BotWebhookManager": ("Main.utils.bot_webhook", "BotWebhookManager"),
    "bot_webhook_manager": ("Main.utils.bot_webhook", "bot_webhook_manager"),
    "AuthWizardSession": ("Main.utils.client_manager", "AuthWizardSession"),
    "ClientManager": ("Main.utils.client_manager", "ClientManager"),
    "constant_time_equals": ("Main.utils.crypto", "constant_time_equals"),
    "decrypt_secret": ("Main.utils.crypto", "decrypt_secret"),
    "encrypt_secret": ("Main.utils.crypto", "encrypt_secret"),
    "generate_webhook_secret": ("Main.utils.crypto", "generate_webhook_secret"),
    "redact_secret": ("Main.utils.crypto", "redact_secret"),
    "start_http_server": ("Main.utils.http_server", "start_http_server"),
    "stop_http_server": ("Main.utils.http_server", "stop_http_server"),
    "addacc_api_creds_keyboard": ("Main.utils.keyboard", "addacc_api_creds_keyboard"),
    "addacc_method_keyboard": ("Main.utils.keyboard", "addacc_method_keyboard"),
    "addacc_session_keyboard": ("Main.utils.keyboard", "addacc_session_keyboard"),
    "addbot_wizard_keyboard": ("Main.utils.keyboard", "addbot_wizard_keyboard"),
    "client_delete_confirm_keyboard": ("Main.utils.keyboard", "client_delete_confirm_keyboard"),
    "client_detail_keyboard": ("Main.utils.keyboard", "client_detail_keyboard"),
    "export_delete_keyboard": ("Main.utils.keyboard", "export_delete_keyboard"),
    "export_picker_keyboard": ("Main.utils.keyboard", "export_picker_keyboard"),
    "keypad_keyboard": ("Main.utils.keyboard", "keypad_keyboard"),
    "main_menu_keyboard": ("Main.utils.keyboard", "main_menu_keyboard"),
    "stats_overview_keyboard": ("Main.utils.keyboard", "stats_overview_keyboard"),
    "LMDBStorage": ("Main.utils.lmdb_storage", "LMDBStorage"),
    "PeerTracker": ("Main.utils.peer_tracker", "PeerTracker"),
    "BroadcastResult": ("Main.utils.peer_tracker", "BroadcastResult"),
    "broadcast_to_client_audience": ("Main.utils.peer_tracker", "broadcast_to_client_audience"),
    "build_addacc_api_rich_message": ("Main.utils.rich_ui", "build_addacc_api_rich_message"),
    "build_addacc_method_rich_message": ("Main.utils.rich_ui", "build_addacc_method_rich_message"),
    "build_addacc_session_rich_message": ("Main.utils.rich_ui", "build_addacc_session_rich_message"),
    "build_addbot_rich_message": ("Main.utils.rich_ui", "build_addbot_rich_message"),
    "build_help_rich_message": ("Main.utils.rich_ui", "build_help_rich_message"),
    "build_keypad_rich_message": ("Main.utils.rich_ui", "build_keypad_rich_message"),
    "format_uptime": ("Main.utils.rich_ui", "format_uptime"),
}

__all__ = list(_EXPORTS.keys())


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr_name = target
    mod = importlib.import_module(mod_name)
    return getattr(mod, attr_name)
