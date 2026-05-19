"""HTML entity encode / decode. <p>café & "co"</p> ↔ &lt;p&gt;café &amp; &quot;co&quot;&lt;/p&gt;"""
from __future__ import annotations

import html
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"), check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "text-html-symbolic", label, text[:200]],
        check=False,
    )


def _encode(text: str) -> None:
    _copy(html.escape(text, quote=True), "HTML entities encoded")


def _decode(text: str) -> None:
    _copy(html.unescape(text), "HTML entities decoded")


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    register_plugin(Plugin(name="html-entity-encode", icon="text-html-symbolic",
        tooltip="HTML entity encode", handler=_encode, content_types=types, priority=75))
    register_plugin(Plugin(name="html-entity-decode", icon="text-html-symbolic",
        tooltip="HTML entity decode", handler=_decode, content_types=types, priority=76))
