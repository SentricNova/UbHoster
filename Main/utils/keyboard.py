"""InlineKeyboardMarkup builders with Kurigram ButtonStyle colors."""

from __future__ import annotations

from typing import Any

from pyrogram import enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "➕ Add Bot",
                    callback_data="menu:addbot",
                    style=enums.ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    "➕ Add Account",
                    callback_data="menu:addacc",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    "📊 My Clients & Stats",
                    callback_data="menu:stats",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    "📣 Broadcast",
                    callback_data="menu:broadcast",
                    style=enums.ButtonStyle.SUCCESS,
                ),
            ],
            [
                InlineKeyboardButton(
                    "🎁 Affiliate Program",
                    callback_data="menu:affiliate",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    "🔑 Export Session",
                    callback_data="menu:export",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton("🏓 Ping", callback_data="menu:ping"),
                InlineKeyboardButton("❓ Help", callback_data="menu:help"),
            ],
        ]
    )


def keypad_keyboard(mode: str = "phone") -> InlineKeyboardMarkup:
    """Numeric keypad for entering phone number or OTP without typing in chat."""
    digit_btn = lambda d: InlineKeyboardButton(d, callback_data=f"kp:d:{d}")

    if mode == "phone":
        bottom_left = InlineKeyboardButton(
            "➕ +",
            callback_data="kp:d:+",
            style=enums.ButtonStyle.PRIMARY,
        )
        enter_label = "📨 Send OTP"
    else:
        bottom_left = InlineKeyboardButton(
            "🔁 Resend",
            callback_data="kp:resend",
            style=enums.ButtonStyle.PRIMARY,
        )
        enter_label = "✅ Verify OTP"

    return InlineKeyboardMarkup(
        [
            [digit_btn("1"), digit_btn("2"), digit_btn("3")],
            [digit_btn("4"), digit_btn("5"), digit_btn("6")],
            [digit_btn("7"), digit_btn("8"), digit_btn("9")],
            [
                bottom_left,
                digit_btn("0"),
                InlineKeyboardButton(
                    "⌫",
                    callback_data="kp:bs",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    "🗑 Clear",
                    callback_data="kp:clear",
                    style=enums.ButtonStyle.DANGER,
                ),
                InlineKeyboardButton(
                    enter_label,
                    callback_data="kp:enter",
                    style=enums.ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="kp:cancel",
                    style=enums.ButtonStyle.DANGER,
                ),
            ],
        ]
    )


def addbot_wizard_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🤖 Open @BotFather",
                    url="https://t.me/BotFather",
                    style=enums.ButtonStyle.PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="addbot:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ],
        ]
    )


def addacc_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📱 Phone Number (Keypad)",
                    callback_data="addacc:mode:phone",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔑 Session String",
                    callback_data="addacc:mode:session",
                    style=enums.ButtonStyle.SUCCESS,
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="kp:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ],
        ]
    )


def addacc_api_creds_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⏭ Skip (Use Bot's Default API)",
                    callback_data="addacc:skip_api",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="kp:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ],
        ]
    )


def addacc_session_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="kp:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        ]
    )


def cancel_2fa_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Cancel Login",
                    callback_data="kp:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        ]
    )


def stats_overview_keyboard(
    clients: list[dict[str, Any]],
    running_ids: set[str],
    *,
    is_main_owner: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if clients:
        rows.append(
            [
                InlineKeyboardButton(
                    f"📊 Show Bot Stats ({len(clients)})",
                    callback_data="stats:list:0",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ]
        )

    if is_main_owner:
        rows.append(
            [
                InlineKeyboardButton(
                    "🤖 Stats of Bots",
                    callback_data="stats:bots:0",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    "👤 Stats of Ubs",
                    callback_data="stats:ubs:0",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                "🔄 Refresh",
                callback_data="stats:refresh",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "➕ Add Bot",
                callback_data="menu:addbot",
                style=enums.ButtonStyle.SUCCESS,
            ),
            InlineKeyboardButton(
                "➕ Add Acc",
                callback_data="menu:addacc",
                style=enums.ButtonStyle.PRIMARY,
            ),
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def stats_client_list_keyboard(
    clients: list[dict[str, Any]],
    running_ids: set[str],
    page: int = 0,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    total = len(clients)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = clients[page * per_page : (page + 1) * per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for doc in chunk:
        cid = doc["client_id"]
        is_up = cid in running_ids
        icon = "🟢" if is_up else "🔴"
        kind = "Bot" if doc.get("client_type") == "bot" else "Acc"
        label = f"{icon} [{kind}] {doc.get('name', 'Client')}"
        if doc.get("username"):
            label += f" (@{doc['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"stats:view:{cid}",
                    style=enums.ButtonStyle.SUCCESS if is_up else enums.ButtonStyle.DEFAULT,
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"stats:list:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"stats:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back to Stats", callback_data="stats:refresh"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def client_detail_keyboard(client_id: str, is_running: bool) -> InlineKeyboardMarkup:
    toggle_btn = (
        InlineKeyboardButton(
            "⏹ Stop",
            callback_data=f"cli:stop:{client_id}",
            style=enums.ButtonStyle.DANGER,
        )
        if is_running
        else InlineKeyboardButton(
            "▶️ Start",
            callback_data=f"cli:start:{client_id}",
            style=enums.ButtonStyle.SUCCESS,
        )
    )
    return InlineKeyboardMarkup(
        [
            [
                toggle_btn,
                InlineKeyboardButton(
                    "🔄 Restart",
                    callback_data=f"cli:restart:{client_id}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    "👥 Manage Users / Groups / Channels",
                    callback_data=f"peers:menu:{client_id}",
                    style=enums.ButtonStyle.PRIMARY,
                )
            ],
            [
                InlineKeyboardButton(
                    "📣 Broadcast",
                    callback_data=f"bc:pick:{client_id}",
                    style=enums.ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    "🔑 Export Session",
                    callback_data=f"exp:get:{client_id}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    "⚙️ Settings",
                    callback_data=f"cset:menu:{client_id}",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    "🗑 Remove",
                    callback_data=f"cli:del_ask:{client_id}",
                    style=enums.ButtonStyle.DANGER,
                ),
            ],
            [
                InlineKeyboardButton("⬅️ Back to Stats", callback_data="stats:refresh"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu:home"),
            ],
        ]
    )


def client_delete_confirm_keyboard(client_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm Delete",
                    callback_data=f"cli:del_yes:{client_id}",
                    style=enums.ButtonStyle.DANGER,
                ),
                InlineKeyboardButton(
                    "↩️ Keep Client",
                    callback_data=f"stats:view:{client_id}",
                    style=enums.ButtonStyle.SUCCESS,
                ),
            ]
        ]
    )


def peers_menu_keyboard(
    client_id: str,
    counts: dict[str, int],
    *,
    can_sync_dialogs: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                f"👤 Users ({counts.get('user', 0)})",
                callback_data=f"peers:list:{client_id}:user:0",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                f"👥 Groups ({counts.get('group', 0)})",
                callback_data=f"peers:list:{client_id}:group:0",
                style=enums.ButtonStyle.PRIMARY,
            ),
        ],
        [
            InlineKeyboardButton(
                f"📢 Channels ({counts.get('channel', 0)})",
                callback_data=f"peers:list:{client_id}:channel:0",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "📣 Broadcast All",
                callback_data=f"bc:ask:{client_id}:all",
                style=enums.ButtonStyle.SUCCESS,
            ),
        ],
    ]
    if can_sync_dialogs:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔄 Sync Existing Chats",
                    callback_data=f"peers:sync:{client_id}",
                    style=enums.ButtonStyle.SUCCESS,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "🗑 Clear All Peers",
                callback_data=f"peers:clear_ask:{client_id}",
                style=enums.ButtonStyle.DANGER,
            ),
            InlineKeyboardButton(
                "⬅️ Back to Client",
                callback_data=f"stats:view:{client_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def peers_list_keyboard(
    client_id: str,
    peer_type: str,
    peers: list[dict[str, Any]],
    page: int,
    has_next: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for p in peers:
        chat_id = p["chat_id"]
        label = p.get("title") or p.get("username") or str(chat_id)
        if p.get("username"):
            label = f"{label} (@{p['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    f"❌ Remove: {label[:42]}",
                    callback_data=f"peers:del:{client_id}:{peer_type}:{page}:{chat_id}",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"peers:list:{client_id}:{peer_type}:{page - 1}",
            )
        )
    if has_next:
        nav.append(
            InlineKeyboardButton(
                "Next ➡️",
                callback_data=f"peers:list:{client_id}:{peer_type}:{page + 1}",
            )
        )
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(
                f"📣 Broadcast {peer_type.title()}s",
                callback_data=f"bc:ask:{client_id}:{peer_type}",
                style=enums.ButtonStyle.SUCCESS,
            ),
            InlineKeyboardButton(
                "⬅️ Back",
                callback_data=f"peers:menu:{client_id}",
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def broadcast_client_picker_keyboard(clients: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for doc in clients[:15]:
        cid = doc["client_id"]
        kind = "Bot" if doc.get("client_type") == "bot" else "Acc"
        label = f"[{kind}] {doc.get('name', cid)}"
        if doc.get("username"):
            label += f" (@{doc['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"bc:pick:{cid}",
                    style=enums.ButtonStyle.PRIMARY,
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def stats_client_list_keyboard(
    clients: list[dict[str, Any]],
    running_ids: set[str],
    page: int = 0,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    total = len(clients)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = clients[page * per_page : (page + 1) * per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for doc in chunk:
        cid = doc["client_id"]
        is_up = cid in running_ids
        icon = "🟢" if is_up else "🔴"
        kind = "Bot" if doc.get("client_type") == "bot" else "Acc"
        label = f"{icon} [{kind}] {doc.get('name', 'Client')}"
        if doc.get("username"):
            label += f" (@{doc['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"stats:view:{cid}",
                    style=enums.ButtonStyle.SUCCESS if is_up else enums.ButtonStyle.DEFAULT,
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"stats:list:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"stats:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back to Stats", callback_data="stats:refresh"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def broadcast_target_keyboard(client_id: str, counts: dict[str, int]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"👤 Users ({counts.get('user', 0)})",
                    callback_data=f"bc:ask:{client_id}:user",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    f"👥 Groups ({counts.get('group', 0)})",
                    callback_data=f"bc:ask:{client_id}:group",
                    style=enums.ButtonStyle.PRIMARY,
                ),
            ],
            [
                InlineKeyboardButton(
                    f"📢 Channels ({counts.get('channel', 0)})",
                    callback_data=f"bc:ask:{client_id}:channel",
                    style=enums.ButtonStyle.PRIMARY,
                ),
                InlineKeyboardButton(
                    f"🌐 All ({counts.get('total', 0)})",
                    callback_data=f"bc:ask:{client_id}:all",
                    style=enums.ButtonStyle.SUCCESS,
                ),
            ],
            [
                InlineKeyboardButton("⬅️ Client Details", callback_data=f"stats:view:{client_id}"),
                InlineKeyboardButton("🏠 Menu", callback_data="menu:home"),
            ],
        ]
    )


def broadcast_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "❌ Cancel Broadcast",
                    callback_data="bc:cancel",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        ]
    )


def export_picker_keyboard(clients: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for doc in clients[:15]:
        cid = doc["client_id"]
        kind = "Bot" if doc.get("client_type") == "bot" else "Acc"
        label = f"🔑 [{kind}] {doc.get('name', cid)}"
        if doc.get("username"):
            label += f" (@{doc['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"exp:get:{cid}",
                    style=enums.ButtonStyle.PRIMARY,
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def stats_client_list_keyboard(
    clients: list[dict[str, Any]],
    running_ids: set[str],
    page: int = 0,
    per_page: int = 6,
) -> InlineKeyboardMarkup:
    total = len(clients)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = clients[page * per_page : (page + 1) * per_page]

    rows: list[list[InlineKeyboardButton]] = []
    for doc in chunk:
        cid = doc["client_id"]
        is_up = cid in running_ids
        icon = "🟢" if is_up else "🔴"
        kind = "Bot" if doc.get("client_type") == "bot" else "Acc"
        label = f"{icon} [{kind}] {doc.get('name', 'Client')}"
        if doc.get("username"):
            label += f" (@{doc['username']})"
        rows.append(
            [
                InlineKeyboardButton(
                    label[:60],
                    callback_data=f"stats:view:{cid}",
                    style=enums.ButtonStyle.SUCCESS if is_up else enums.ButtonStyle.DEFAULT,
                )
            ]
        )

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"stats:list:{page - 1}"))
    if page + 1 < total_pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"stats:list:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back to Stats", callback_data="stats:refresh"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def export_delete_keyboard(client_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🗑 Delete This Message",
                    callback_data="exp:delmsg",
                    style=enums.ButtonStyle.DANGER,
                ),
                InlineKeyboardButton(
                    "⬅️ Client Stats",
                    callback_data=f"stats:view:{client_id}",
                ),
            ]
        ]
    )


def client_settings_keyboard(client_id: str, client_type: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "👋 Welcome Text",
                callback_data=f"cset:welcome:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "📋 Logger Group",
                callback_data=f"cset:logger:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
        ],
        [
            InlineKeyboardButton(
                "👥 Sudo Users",
                callback_data=f"cset:sudo:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "🧩 Feature Toggles",
                callback_data=f"cset:feats:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
        ],
    ]
    if client_type == "userbot":
        rows.append(
            [
                InlineKeyboardButton(
                    "🛡 DM Guard",
                    callback_data=f"cset:dmguard:{client_id}",
                    style=enums.ButtonStyle.PRIMARY,
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton("⬅️ Back to Client", callback_data=f"stats:view:{client_id}"),
            InlineKeyboardButton("🏠 Menu", callback_data="menu:home"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def feature_toggles_keyboard(
    client_id: str,
    features: dict[str, bool],
    all_features: dict[str, str],
    client_type: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    pair: list[InlineKeyboardButton] = []
    for key, label in all_features.items():
        if client_type == "bot" and key in ("steal", "dm_guard"):
            continue
        enabled = bool(features.get(key, True))
        icon = "🟢" if enabled else "🔴"
        style = enums.ButtonStyle.SUCCESS if enabled else enums.ButtonStyle.DANGER
        pair.append(
            InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=f"cset:ftog:{client_id}:{key}",
                style=style,
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)
    rows.append([InlineKeyboardButton("⬅️ Settings", callback_data=f"cset:menu:{client_id}")])
    return InlineKeyboardMarkup(rows)


def sudo_manage_keyboard(client_id: str, sudo_users: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "➕ Add Sudo",
                callback_data=f"cset:sudoadd:{client_id}",
                style=enums.ButtonStyle.SUCCESS,
            )
        ]
    ]
    for uid in sudo_users:
        rows.append(
            [
                InlineKeyboardButton(
                    f"❌ Remove {uid}",
                    callback_data=f"cset:sudodel:{client_id}:{uid}",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        )
    rows.append([InlineKeyboardButton("⬅️ Settings", callback_data=f"cset:menu:{client_id}")])
    return InlineKeyboardMarkup(rows)


def dm_guard_keyboard(client_id: str, dm_guard: dict[str, Any]) -> InlineKeyboardMarkup:
    action = str(dm_guard.get("action") or "off")
    actions = [("off", "Off"), ("warn", "Warn"), ("mute", "Mute"), ("archive", "Archive"), ("block", "Block")]
    action_row: list[InlineKeyboardButton] = []
    for code, title in actions:
        is_sel = action == code
        action_row.append(
            InlineKeyboardButton(
                f"{'✅ ' if is_sel else ''}{title}",
                callback_data=f"cset:dmgact:{client_id}:{code}",
                style=enums.ButtonStyle.SUCCESS if is_sel else enums.ButtonStyle.DEFAULT,
            )
        )
    rows: list[list[InlineKeyboardButton]] = [
        action_row[:3],
        action_row[3:],
        [
            InlineKeyboardButton(
                f"🔢 Warn Limit ({dm_guard.get('warn_limit', 3)})",
                callback_data=f"cset:dmglim:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "✏️ Warn Text",
                callback_data=f"cset:dmgtxt:{client_id}",
                style=enums.ButtonStyle.PRIMARY,
            ),
        ],
        [InlineKeyboardButton("⬅️ Settings", callback_data=f"cset:menu:{client_id}")],
    ]
    return InlineKeyboardMarkup(rows)


def owner_broadcast_keyboard(state: dict[str, Any]) -> InlineKeyboardMarkup:
    mode = state.get("mode", "copy")
    sender = state.get("sender", "main")
    speed = state.get("speed", "medium")
    delete_after = state.get("delete_after", 0)
    target = state.get("target", "all")

    def _btn(label: str, cb: str, active: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            f"{'✅ ' if active else ''}{label}",
            callback_data=cb,
            style=enums.ButtonStyle.SUCCESS if active else enums.ButtonStyle.DEFAULT,
        )

    return InlineKeyboardMarkup(
        [
            [
                _btn("Copy", "obc:opt:mode:copy", mode == "copy"),
                _btn("Forward", "obc:opt:mode:forward", mode == "forward"),
            ],
            [
                _btn("MainBot", "obc:opt:sender:main", sender == "main"),
                _btn("AllBots", "obc:opt:sender:bots", sender == "bots"),
                _btn("AllAccounts", "obc:opt:sender:ubs", sender == "ubs"),
                _btn("Everyone", "obc:opt:sender:all", sender == "all"),
            ],
            [
                _btn("Slow", "obc:opt:speed:slow", speed == "slow"),
                _btn("Medium", "obc:opt:speed:medium", speed == "medium"),
                _btn("Fast", "obc:opt:speed:fast", speed == "fast"),
            ],
            [
                _btn("Del: Off", "obc:opt:del:0", delete_after == 0),
                _btn("Del: 1m", "obc:opt:del:60", delete_after == 60),
                _btn("Del: 1h", "obc:opt:del:3600", delete_after == 3600),
            ],
            [
                _btn("Users", "obc:opt:target:user", target == "user"),
                _btn("Groups", "obc:opt:target:group", target == "group"),
                _btn("Channels", "obc:opt:target:channel", target == "channel"),
                _btn("All", "obc:opt:target:all", target == "all"),
            ],
            [
                InlineKeyboardButton(
                    "🚀 Start Broadcast",
                    callback_data="obc:start",
                    style=enums.ButtonStyle.SUCCESS,
                ),
                InlineKeyboardButton(
                    "🗑 Delete All Sent",
                    callback_data="obc:delall",
                    style=enums.ButtonStyle.DANGER,
                ),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="obc:cancel", style=enums.ButtonStyle.DANGER)],
        ]
    )


def owner_broadcast_runtime_keyboard(paused: bool, has_sent: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "▶️ Resume" if paused else "⏸ Pause",
                callback_data="obc:resume" if paused else "obc:pause",
                style=enums.ButtonStyle.PRIMARY,
            ),
            InlineKeyboardButton(
                "🛑 Stop",
                callback_data="obc:stop",
                style=enums.ButtonStyle.DANGER,
            ),
        ]
    ]
    if has_sent:
        rows.append(
            [
                InlineKeyboardButton(
                    "🗑 Delete All Sent Messages",
                    callback_data="obc:delall",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def forcejoin_keyboard(scope: str, chats: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    def _btn(label: str, val: str) -> InlineKeyboardButton:
        active = scope == val
        return InlineKeyboardButton(
            f"{'✅ ' if active else ''}{label}",
            callback_data=f"fj:scope:{val}",
            style=enums.ButtonStyle.SUCCESS if active else enums.ButtonStyle.DEFAULT,
        )

    rows: list[list[InlineKeyboardButton]] = [
        [
            _btn("Off", "off"),
            _btn("MainBot", "main"),
            _btn("Everyone's Bots", "all_bots"),
        ],
        [
            InlineKeyboardButton(
                "➕ Add Chat",
                callback_data="fj:add",
                style=enums.ButtonStyle.SUCCESS,
            ),
            InlineKeyboardButton(
                "🧹 Clear All",
                callback_data="fj:clear",
                style=enums.ButtonStyle.DANGER,
            ),
        ],
    ]
    for c in chats:
        cid = c.get("chat_id")
        title = str(c.get("title") or cid)
        rows.append(
            [
                InlineKeyboardButton(
                    f"❌ {title[:40]}",
                    callback_data=f"fj:del:{cid}",
                    style=enums.ButtonStyle.DANGER,
                )
            ]
        )
    return InlineKeyboardMarkup(rows)
