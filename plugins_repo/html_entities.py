"""HTML entity encode / decode. <p>café & "co"</p> ↔ &lt;p&gt;café &amp; &quot;co&quot;&lt;/p&gt;"""
from __future__ import annotations

import html
import re
import subprocess

from classifier import ContentType
from plugin_base import Plugin

# Named or numeric HTML entities - &amp; &#39; &#x2F; etc.
_HTML_ENTITY = re.compile(r"&(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#[0-9]+|#x[0-9a-fA-F]+);")


def _has_html_entities(text: str) -> bool:
    return bool(_HTML_ENTITY.search(text))


# Chars whose HTML-escaped form differs from themselves. If none of
# these are in the selection, html.escape() returns the input unchanged
# and there's nothing for the user to click.
_NEEDS_ESCAPING = set("<>&\"'")


def _has_escapable_chars(text: str) -> bool:
    return any(c in _NEEDS_ESCAPING for c in text)


def _copy(text: str, label: str) -> None:
    # Replace the user's selection with the result AND keep
    # it on the clipboard. Fallback (read-only context): the
    # clipboard still has it so the user can paste manually.
    import actions
    actions.replace_selection(text)
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",  "-i", "text-html-symbolic", label, text[:200]],
        check=False,
    )


def _encode(text: str) -> None:
    _copy(html.escape(text, quote=True), "HTML entities encoded")


def _decode(text: str) -> None:
    _copy(html.unescape(text), "HTML entities decoded")


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    register_plugin(Plugin(name="html-entity-encode", icon="linuxpop-html-encode-symbolic",
        tooltip="HTML entity encode", handler=_encode, content_types=types, priority=75,
        predicate=_has_escapable_chars))
    register_plugin(Plugin(name="html-entity-decode", icon="linuxpop-html-decode-symbolic",
        tooltip="HTML entity decode", handler=_decode, content_types=types, priority=76,
        predicate=_has_html_entities))
