"""URL-encode and decode selected text."""
from __future__ import annotations

import re
import subprocess
import sys
import urllib.parse


from classifier import ContentType  # noqa: E402
from plugin_base import Plugin  # noqa: E402

_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
_PLUS_AS_SPACE = re.compile(r"\S\+\S")


def _looks_percent_encoded(text: str) -> bool:
    """Return True if the selection contains percent-escape sequences
    (\\x%XX), which is the only thing url-decode can actually change."""
    return bool(_PERCENT_ESCAPE.search(text))


_URL_UNRESERVED = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)

# Characters that strongly suggest the selection is URL-shaped (or
# is meant to be slotted into a URL): query separators, anchors,
# percent signs, plus already-escaped sequences.
_URL_SIGNAL_CHARS = set("?&=#%")


def _has_chars_needing_url_encode(text: str) -> bool:
    """Show URL-encode only when the selection is URL-shaped or actually
    contains characters that produce different output after encoding.

    The classifier already routes recognised URLs through ContentType.URL,
    which the plugin matches via content_types. For PLAIN_TEXT we apply
    a stricter heuristic: at least one URL-signal char (?&=#%) OR
    non-ASCII (where encoding matters for cross-system safety). 'Hello
    world' has only a space - encoding it gives 'Hello%20world', which
    is technically correct but rarely the user's intent for prose.
    """
    if any(c in _URL_SIGNAL_CHARS for c in text):
        return True
    if any(ord(c) > 127 for c in text):
        return True
    return False


def _copy_and_notify(label: str, text: str) -> None:
    import actions
    actions.replace_selection(text)
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",  "-i", "applications-internet", label, text[:200]],
        check=False,
    )


def _encode(text: str) -> None:
    _copy_and_notify("URL-encoded", urllib.parse.quote(text))


def _decode(text: str) -> None:
    _copy_and_notify("URL-decoded", urllib.parse.unquote(text))


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="url-encode",
        icon="linuxpop-url-encode-symbolic",
        tooltip="URL-encode",
        handler=_encode,
        content_types=(ContentType.PLAIN_TEXT, ContentType.URL),
        priority=70,
        predicate=_has_chars_needing_url_encode,
    ))
    register_plugin(Plugin(
        name="url-decode",
        icon="linuxpop-url-decode-symbolic",
        tooltip="URL-decode",
        handler=_decode,
        content_types=(ContentType.PLAIN_TEXT, ContentType.URL),
        priority=71,
        predicate=_looks_percent_encoded,
    ))
