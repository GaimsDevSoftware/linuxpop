"""URL-encode and decode selected text."""
from __future__ import annotations

import subprocess
import sys
import urllib.parse


from classifier import ContentType  # noqa: E402
from plugin_base import Plugin  # noqa: E402


def _copy_and_notify(label: str, text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"),
        check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "applications-internet", label, text[:200]],
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
    ))
    register_plugin(Plugin(
        name="url-decode",
        icon="linuxpop-url-decode-symbolic",
        tooltip="URL-decode",
        handler=_decode,
        content_types=(ContentType.PLAIN_TEXT, ContentType.URL),
        priority=71,
    ))
