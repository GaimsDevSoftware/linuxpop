"""Markdown wrapping - bold, italic, strikethrough, highlight, quote, code, link.

Unlike the rich-text Formatting plugin (which presses Ctrl+B and lets the
editor style the text), these wrap the selection in literal markdown syntax
and paste it back over the selection. Handy in any plain-text markdown editor,
note app, chat box, commit message or issue field.

They share the "markdown" category, so with popup_group_categories on they
collapse behind one "Markdown" chip. Each carries the red ".md" earmark icon
so it never gets confused with the rich-text formatting buttons.
"""
from __future__ import annotations

import actions
from plugin_base import Plugin


def _wrap(prefix: str, suffix: str):
    def handler(text: str) -> None:
        actions.replace_selection(f"{prefix}{text}{suffix}")
    return handler


def _quote(text: str) -> None:
    # Blockquote every line of the selection.
    lines = text.split("\n")
    actions.replace_selection("\n".join(f"> {ln}" for ln in lines))


def _link(text: str) -> None:
    # Inline link with an empty target for the user to fill in.
    actions.replace_selection(f"[{text}]()")


_ACTIONS = [
    ("md-bold",          "linuxpop-md-bold-symbolic",          "Bold (**)",        _wrap("**", "**"), 50),
    ("md-italic",        "linuxpop-md-italic-symbolic",        "Italic (*)",       _wrap("*", "*"),   51),
    ("md-strikethrough", "linuxpop-md-strikethrough-symbolic", "Strikethrough (~~)", _wrap("~~", "~~"), 52),
    ("md-highlight",     "linuxpop-md-highlight-symbolic",     "Highlight (==)",   _wrap("==", "=="), 53),
    ("md-quote",         "linuxpop-md-quote-symbolic",         "Quote (>)",        _quote,            54),
    ("md-code",          "linuxpop-md-code-symbolic",          "Inline code (`)",  _wrap("`", "`"),   55),
    ("md-link",          "linuxpop-md-link-symbolic",          "Link ([]())",      _link,             56),
]


def register(register_plugin) -> None:
    for name, icon, tooltip, handler, priority in _ACTIONS:
        register_plugin(Plugin(
            name=name,
            icon=icon,
            tooltip=tooltip,
            handler=handler,
            content_types=(),
            priority=priority,
            requires_editable=True,
            category="markdown",
        ))
