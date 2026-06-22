"""Bold / Italic / Underline / Strikethrough - rich-text formatting.

Standard Ctrl-key shortcuts that work in almost every modern rich-text
editor (Google Docs, Word, LibreOffice, Notion, Obsidian, GitHub
Markdown editors with the formatting toolbar, most webmail composers).

In Markdown editors that don't bind Ctrl+B etc., these will do nothing
- same trade-off as PopClip on macOS. The user is expected to know
when they're in a rich-text vs plain-text context.
"""
from __future__ import annotations

from plugin_base import Plugin


def _send(combo: str) -> None:
    # Route through the active backend so key injection uses the right tool
    # for the session: xdotool on X11, ydotool on Wayland/KDE. Calling
    # xdotool directly does nothing on native Wayland.
    try:
        from platform_backend import get_backend
        get_backend().send_key(combo)
    except Exception as exc:  # noqa: BLE001
        print(f"[formatting] send_key failed: {exc}")


def register(register_plugin) -> None:
    # Use the bundled house-style glyphs (icons/linuxpop-format-*) rather than
    # freedesktop format-text-* theme names: the theme icons render in
    # whatever style the active desktop ships (and can be missing entirely
    # under some KDE themes or in the Flatpak sandbox), which left these
    # sitting visually foreign next to the rest of the popup row. These are
    # the plain letterforms; the markdown variants (linuxpop-md-*) carry a
    # red ".md" earmark so the two mechanisms never look the same.
    register_plugin(Plugin(
        name="format-bold",
        icon="linuxpop-format-bold-symbolic",
        tooltip="Bold",
        handler=lambda _t: _send("ctrl+b"),
        content_types=(),
        priority=40,
        requires_editable=True,
        category="format",
    ))
    register_plugin(Plugin(
        name="format-italic",
        icon="linuxpop-format-italic-symbolic",
        tooltip="Italic",
        handler=lambda _t: _send("ctrl+i"),
        content_types=(),
        priority=41,
        requires_editable=True,
        category="format",
    ))
    register_plugin(Plugin(
        name="format-underline",
        icon="linuxpop-format-underline-symbolic",
        tooltip="Underline",
        handler=lambda _t: _send("ctrl+u"),
        content_types=(),
        priority=42,
        requires_editable=True,
        category="format",
    ))
