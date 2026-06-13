"""Bold / Italic / Underline / Strikethrough - rich-text formatting.

Standard Ctrl-key shortcuts that work in almost every modern rich-text
editor (Google Docs, Word, LibreOffice, Notion, Obsidian, GitHub
Markdown editors with the formatting toolbar, most webmail composers).

In Markdown editors that don't bind Ctrl+B etc., these will do nothing
- same trade-off as PopClip on macOS. The user is expected to know
when they're in a rich-text vs plain-text context.
"""
from __future__ import annotations

import shutil
import subprocess

from plugin_base import Plugin


def _send(combo: str) -> None:
    if not shutil.which("xdotool"):
        return
    try:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", combo],
            check=False, timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        pass


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="format-bold",
        icon="format-text-bold-symbolic",
        tooltip="Bold",
        handler=lambda _t: _send("ctrl+b"),
        content_types=(),
        priority=40,
        requires_editable=True,
    ))
    register_plugin(Plugin(
        name="format-italic",
        icon="format-text-italic-symbolic",
        tooltip="Italic",
        handler=lambda _t: _send("ctrl+i"),
        content_types=(),
        priority=41,
        requires_editable=True,
    ))
    register_plugin(Plugin(
        name="format-underline",
        icon="format-text-underline-symbolic",
        tooltip="Underline",
        handler=lambda _t: _send("ctrl+u"),
        content_types=(),
        priority=42,
        requires_editable=True,
    ))
