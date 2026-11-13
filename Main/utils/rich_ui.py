"""RichMessage & Rich Table UI builders using Kurigram InputRichBlockTable."""

from __future__ import annotations

from pyrogram import enums, types


def format_uptime(seconds: int | float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _make_table_cell(
    label: str,
    callback_data: str,
    style: enums.ButtonStyle,
) -> types.RichBlockTableCell:
    return types.RichBlockTableCell(
        text=types.RichTextButton(
            types.RichMessageButton(
                text=label,
                style=style,
                callback_data=callback_data,
            )
        ),
        align="center",
        valign="middle",
    )


def build_keypad_rich_message(
    *,
    mode: str,
    value: str,
    error_text: str | None = None,
    phone_hint: str = "",
) -> types.InputRichMessage:
    """Build a RichMessage with a bordered table of green/red buttons for number/OTP entry.

    On valid input state: entered digits are green (ButtonStyle.SUCCESS) and
    placeholders are blue (ButtonStyle.PRIMARY) inside the table:
        | + | 9 | 1 | 9 | 8 | 7 | 6 | 5 |

    On wrong phone number or wrong OTP (`error_text` is set): ALL buttons inside
    the table become red (ButtonStyle.DANGER).
    """
    is_error = bool(error_text)
    max_cols = 8

    if mode == "phone":
        title = "Step 1/2 — Enter Phone Number (Secure Keypad)"
        steps = [
            "Tap the inline keypad buttons below to enter your phone number with country code (e.g. +14155552671).",
            "Do NOT type or send your phone number as a chat message — Telegram can ban bots that collect numbers via text.",
            "Press 'Send OTP' once your full number appears in the green table below.",
        ]
        chars = list(value)
        if not chars:
            slots = ["+", "•", "•", "•", "•", "•"]
            filled_flags = [False] * len(slots)
        elif len(chars) <= max_cols:
            slots = chars
            filled_flags = [True] * len(chars)
        else:
            # Chunk into rows of 8 columns, padding last row so table dimensions stay uniform
            slots = chars
            filled_flags = [True] * len(chars)
            rem = len(slots) % max_cols
            if rem:
                pad = max_cols - rem
                slots.extend(["·"] * pad)
                filled_flags.extend([False] * pad)
    else:
        title = "Step 2/2 — Enter Telegram OTP (Secure Keypad)"
        masked_phone = phone_hint or "your account"
        steps = [
            f"An official login code was sent to {masked_phone}.",
            "Use ONLY the inline keypad buttons below to enter the 5-digit OTP.",
            "Do NOT paste or type the OTP in chat. Press 'Verify OTP' when done.",
        ]
        chars = list(value)
        total_slots = max(5, len(chars))
        slots = [chars[i] if i < len(chars) else "•" for i in range(total_slots)]
        filled_flags = [i < len(chars) for i in range(total_slots)]

    rows: list[list[types.RichBlockTableCell]] = []
    for row_start in range(0, len(slots), max_cols):
        row_cells: list[types.RichBlockTableCell] = []
        chunk_slots = slots[row_start : row_start + max_cols]
        chunk_filled = filled_flags[row_start : row_start + max_cols]
        for offset, (ch, is_filled) in enumerate(zip(chunk_slots, chunk_filled)):
            idx = row_start + offset
            if is_error:
                btn_style = enums.ButtonStyle.DANGER
            elif is_filled:
                btn_style = enums.ButtonStyle.SUCCESS
            else:
                btn_style = enums.ButtonStyle.PRIMARY
            row_cells.append(_make_table_cell(ch, f"kp:cell:{idx}", btn_style))
        rows.append(row_cells)

    table_caption = (
        f"❌ {error_text}"
        if error_text
        else (f"Digits entered: {len(value)}" if value else "Tap keypad digits below")
    )

    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(text=types.RichTextBold(title), size=3),
        types.InputRichBlockList(
            items=[
                types.InputRichBlockListItem(
                    blocks=[types.InputRichBlockParagraph(text=step)],
                    value=idx,
                    type="1",
                )
                for idx, step in enumerate(steps, start=1)
            ],
        ),
        types.InputRichBlockDivider(),
        types.InputRichBlockTable(
            cells=rows,
            is_bordered=True,
            is_striped=False,
            is_compact=True,
            caption=types.RichTextBold(table_caption) if is_error else table_caption,
        ),
    ]

    if is_error:
        blocks.append(
            types.InputRichBlockBlockQuotation(
                blocks=[
                    types.InputRichBlockParagraph(
                        text=types.RichTextBold(f"Error: {error_text}")
                    )
                ],
                credit="Tap Clear or Backspace to try again",
            )
        )

    return types.InputRichMessage(blocks=blocks)


def build_addacc_method_rich_message() -> types.InputRichMessage:
    return types.InputRichMessage(
        blocks=[
            types.InputRichBlockSectionHeading(
                text=types.RichTextBold("Add Userbot Account — Choose Login Method"),
                size=3,
            ),
            types.InputRichBlockParagraph(
                text=(
                    "Select how you want to connect your Telegram account. "
                    "Both methods encrypt and store your session in MongoDB + LMDB cache."
                )
            ),
            types.InputRichBlockTable(
                cells=[
                    [
                        types.RichBlockTableCell(text=types.RichTextBold("Method"), is_header=True),
                        types.RichBlockTableCell(text=types.RichTextBold("How it works"), is_header=True),
                    ],
                    [
                        types.RichBlockTableCell(text="📱 Phone Number"),
                        types.RichBlockTableCell(
                            text="Secure green/red keypad for number & OTP, then generates a session"
                        ),
                    ],
                    [
                        types.RichBlockTableCell(text="🔑 Session String"),
                        types.RichBlockTableCell(
                            text="Paste an existing Kurigram/Pyrogram session string directly"
                        ),
                    ],
                ],
                is_bordered=True,
                is_striped=True,
                is_compact=True,
            ),
        ]
    )


def build_addacc_api_rich_message(
    *, login_method: str, error_text: str | None = None
) -> types.InputRichMessage:
    method_label = "Phone Number" if login_method == "phone" else "Session String"
    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold(f"API ID & API Hash ({method_label})"),
            size=3,
        ),
        types.InputRichBlockParagraph(
            text=(
                "Optional: send your own API_ID and API_HASH (from my.telegram.org) "
                "separated by a space or newline, e.g.:"
            )
        ),
        types.InputRichBlockPreformatted(
            text="12345678 0123456789abcdef0123456789abcdef",
            language="text",
        ),
        types.InputRichBlockBlockQuotation(
            blocks=[
                types.InputRichBlockParagraph(
                    text="Or tap Skip below to use our bot's built-in API_ID & API_HASH."
                )
            ],
            credit="Your message will be deleted immediately for security",
        ),
    ]
    if error_text:
        blocks.append(
            types.InputRichBlockBlockQuotation(
                blocks=[
                    types.InputRichBlockParagraph(
                        text=types.RichTextBold(f"Error: {error_text}")
                    )
                ],
                credit="Try again or tap Skip",
            )
        )
    return types.InputRichMessage(blocks=blocks)


def build_addacc_session_rich_message(error_text: str | None = None) -> types.InputRichMessage:
    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold("Paste Your Session String"),
            size=3,
        ),
        types.InputRichBlockList(
            items=[
                types.InputRichBlockListItem(
                    blocks=[
                        types.InputRichBlockParagraph(
                            text="Send your Kurigram or Pyrogram session string as your next message."
                        )
                    ],
                    value=1,
                    type="1",
                ),
                types.InputRichBlockListItem(
                    blocks=[
                        types.InputRichBlockParagraph(
                            text="Your message will be deleted immediately, encrypted, and saved to MongoDB + LMDB cache."
                        )
                    ],
                    value=2,
                    type="1",
                ),
            ]
        ),
    ]
    if error_text:
        blocks.append(
            types.InputRichBlockBlockQuotation(
                blocks=[
                    types.InputRichBlockParagraph(
                        text=types.RichTextBold(f"Error: {error_text}")
                    )
                ],
                credit="Send a valid session string or Cancel",
            )
        )
    return types.InputRichMessage(blocks=blocks)


def build_addbot_rich_message(error_text: str | None = None) -> types.InputRichMessage:
    """Build step-by-step instructions for connecting a Telegram Bot token."""
    steps = [
        "Open @BotFather on Telegram and send /newbot (or /mybots -> API Token).",
        "Copy your Bot API Token (format: 123456789:AAHxxxxxxxxxxxxxxxx).",
        "Send the token here as your next message — it will be deleted immediately after verification for safety.",
    ]
    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold("Add a Telegram Bot (Step-by-Step)"),
            size=3,
        ),
        types.InputRichBlockList(
            items=[
                types.InputRichBlockListItem(
                    blocks=[types.InputRichBlockParagraph(text=step)],
                    value=idx,
                    type="1",
                )
                for idx, step in enumerate(steps, start=1)
            ],
        ),
        types.InputRichBlockBlockQuotation(
            blocks=[
                types.InputRichBlockParagraph(
                    text="Hosted bots run on raw MTProto handlers with /start and /ping plugins from Main/ClientPlugin/for_bots/."
                )
            ],
            credit="Low-RAM Raw Mode",
        ),
    ]
    if error_text:
        blocks.append(
            types.InputRichBlockBlockQuotation(
                blocks=[
                    types.InputRichBlockParagraph(
                        text=types.RichTextBold(f"Error: {error_text}")
                    )
                ],
                credit="Please send a valid Bot Token or Cancel",
            )
        )
    return types.InputRichMessage(blocks=blocks)


def build_help_rich_message(is_owner: bool = False) -> types.InputRichMessage:
    """Build RichMessage help overview for main bot commands (filtered for non-owners)."""
    rows = [
        [
            types.RichBlockTableCell(text=types.RichTextBold("Command"), is_header=True, align="left"),
            types.RichBlockTableCell(text=types.RichTextBold("Description"), is_header=True, align="left"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/start")),
            types.RichBlockTableCell(text="Open main hosting dashboard & feature showcase"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/addbot")),
            types.RichBlockTableCell(text="Connect & host a Telegram Bot via Bot Token"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/addacc")),
            types.RichBlockTableCell(text="Connect & host a Userbot via secure keypad"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/stats | stats")),
            types.RichBlockTableCell(text="View & manage your hosted bots and accounts"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/broadcast")),
            types.RichBlockTableCell(text="Broadcast messages to your bot/account audience"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/export")),
            types.RichBlockTableCell(text="Export your connected account/bot session string"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/invite")),
            types.RichBlockTableCell(text="View your affiliate referral link & unlock rewards"),
        ],
        [
            types.RichBlockTableCell(text=types.RichTextCode("/ping")),
            types.RichBlockTableCell(text="Check raw MTProto, MongoDB & LMDB latency"),
        ],
    ]

    blocks: list[types.InputRichBlock] = [
        types.InputRichBlockSectionHeading(
            text=types.RichTextBold("AntiGravity Hosting Commands"),
            size=3,
        ),
        types.InputRichBlockTable(
            cells=rows,
            is_bordered=True,
            is_striped=True,
            is_compact=True,
        ),
    ]

    if is_owner:
        owner_rows = [
            [
                types.RichBlockTableCell(text=types.RichTextBold("Owner Command"), is_header=True, align="left"),
                types.RichBlockTableCell(text=types.RichTextBold("Action"), is_header=True, align="left"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/restart")),
                types.RichBlockTableCell(text="Restart the hosting bot engine safely"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/cleancache")),
                types.RichBlockTableCell(text="Prune temporary LMDB caches & resync"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/maintenance")),
                types.RichBlockTableCell(text="Toggle maintenance mode with custom reason"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/forcejoin")),
                types.RichBlockTableCell(text="Manage mandatory join channels"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/tracemem")),
                types.RichBlockTableCell(text="Analyze per-file RAM memory consumption"),
            ],
            [
                types.RichBlockTableCell(text=types.RichTextCode("/invite")),
                types.RichBlockTableCell(text="Tune affiliate program settings & leaderboard"),
            ],
        ]
        blocks.extend(
            [
                types.InputRichBlockSectionHeading(
                    text=types.RichTextBold("👑 Master Owner Control"),
                    size=3,
                ),
                types.InputRichBlockTable(
                    cells=owner_rows,
                    is_bordered=True,
                    is_striped=True,
                    is_compact=True,
                ),
            ]
        )

    blocks.append(
        types.InputRichBlockFooter(
            text="Hosted Bots support /start & /ping | Hosted Userbots support .ping"
        )
    )
    return types.InputRichMessage(blocks=blocks)
