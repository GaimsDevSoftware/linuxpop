"""UPPERCASE / lowercase / Title Case / slugify text transformations."""
from __future__ import annotations

import re
import subprocess
import unicodedata

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"),
        check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "accessories-text-editor", label, text[:200]],
        check=False,
    )


def _slugify(text: str) -> None:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    _copy(slug, "Slugified")


def _upper(text: str) -> None:
    _copy(text.upper(), "UPPERCASE")


def _lower(text: str) -> None:
    _copy(text.lower(), "lowercase")


def _title(text: str) -> None:
    _copy(text.title(), "Title Case")


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="upper-case",
        icon="linuxpop-case-upper-symbolic",
        tooltip="UPPERCASE",
        handler=_upper,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=80,
    ))
    register_plugin(Plugin(
        name="lower-case",
        icon="linuxpop-case-lower-symbolic",
        tooltip="lowercase",
        handler=_lower,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=81,
    ))
    register_plugin(Plugin(
        name="title-case",
        icon="linuxpop-case-title-symbolic",
        tooltip="Title Case",
        handler=_title,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=82,
    ))
    register_plugin(Plugin(
        name="slugify",
        icon="linuxpop-slugify-symbolic",
        tooltip="Slugify (URL-friendly)",
        handler=_slugify,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=83,
    ))
