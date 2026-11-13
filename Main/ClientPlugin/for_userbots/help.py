""".help plugin for hosted userbots (RichText if Premium, clean text otherwise)."""

from __future__ import annotations

from pyrogram import types

from Main.ClientPlugin.loader import RawContext, userbot_command
from Main.utils.client_actions import check_client_access

_COMMANDS_ROWS = [
    ("Core", ".ping | .help | .stop | .leave"),
    ("Spam & Raid", ".spam | .fspam | .tspam | .raid | .fraid | .unraid"),
    ("Tagging", ".tagall | .tagadmins | .tagusers | .tagactive"),
    ("Moderation", ".ban | .unban | .mute | .unmute | .kick | .banall | .unbanall | .kickall"),
    ("Monitoring", ".spy | .unspy | .spy list | .spy on/off"),
    ("Tools", ".forward | .broadcast | .scrape | .send | .steal | .export | .info"),
    ("DM & Safety", ".dmclean | .confirm | .cancel | .dmblock | .dmmute | .dmunmute"),
]


@userbot_command("help")
async def hosted_userbot_help(ctx: RawContext) -> None:
    allowed, _, err_msg = await check_client_access(ctx, feature="help", owner_only=True)
    if not allowed:
        await ctx.edit_raw(err_msg)
        return

    is_premium = bool(getattr(ctx.client.me, "is_premium", False))
    if is_premium:
        try:
            table_cells = [
                [
                    types.RichBlockTableCell(text=types.RichTextBold("Category"), is_header=True),
                    types.RichBlockTableCell(text=types.RichTextBold("Commands"), is_header=True),
                ]
            ]
            for cat, cmds in _COMMANDS_ROWS:
                table_cells.append(
                    [
                        types.RichBlockTableCell(text=types.RichTextBold(cat)),
                        types.RichBlockTableCell(text=types.RichTextCode(cmds)),
                    ]
                )
            rich_msg = types.InputRichMessage(
                blocks=[
                    types.InputRichBlockSectionHeading(
                        text=types.RichTextBold("Userbot Command Reference"), size=3
                    ),
                    types.InputRichBlockTable(
                        cells=table_cells,
                        is_bordered=True,
                        is_striped=True,
                        is_compact=True,
                    ),
                ]
            )
            await ctx.client.edit_message_text(
                chat_id=ctx.chat_id,
                message_id=ctx.msg_id,
                rich_message=rich_msg,
            )
            return
        except Exception:
            pass

    cards = [
        "╔═══════════════════════════╗\n"
        "   ✨ <b>USERBOT COMMAND GUIDE</b> ✨\n"
        "╚═══════════════════════════╝\n"
    ]
    for cat, cmds in _COMMANDS_ROWS:
        cards.append(f"• <b>{cat}</b>\n<blockquote><code>{cmds}</code></blockquote>")
    cards.append("💡 <i>Execute commands with <code>.command</code> prefix. Send <code>.stop</code> to abort any active task.</i>")

    try:
        from pyrogram import enums
        await ctx.client.edit_message_text(
            chat_id=ctx.chat_id,
            message_id=ctx.msg_id,
            text="\n\n".join(cards),
            parse_mode=enums.ParseMode.HTML,
        )
    except Exception:
        lines = ["📖 **Userbot Commands**\n"]
        for cat, cmds in _COMMANDS_ROWS:
            lines.append(f"• **{cat}:** `{cmds}`")
        await ctx.edit_raw("\n".join(lines))
