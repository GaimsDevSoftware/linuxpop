"""UPPERCASE / lowercase / Title Case / slugify text transformations."""
from __future__ import annotations

import re
import subprocess
import unicodedata

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    # Replace the user's selection with the result AND keep
    # it on the clipboard. Fallback (read-only context): the
    # clipboard still has it so the user can paste manually.
    import actions
    actions.replace_selection(text)
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",  "-i", "accessories-text-editor", label, text[:200]],
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


# Predicates: only show a case button when clicking it would actually
# change the selection. Cheap string operations; safe to call on every
# popup show.

def _has_letters(text: str) -> bool:
    return any(c.isalpha() for c in text)


def _not_already_upper(text: str) -> bool:
    return _has_letters(text) and text != text.upper()


def _not_already_lower(text: str) -> bool:
    return _has_letters(text) and text != text.lower()


def _not_already_title(text: str) -> bool:
    return _has_letters(text) and text != text.title()


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="upper-case",
        icon="linuxpop-case-upper-symbolic",
        tooltip="UPPERCASE",
        handler=_upper,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=80,
        predicate=_not_already_upper,
    ))
    register_plugin(Plugin(
        name="lower-case",
        icon="linuxpop-case-lower-symbolic",
        tooltip="lowercase",
        handler=_lower,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=81,
        predicate=_not_already_lower,
    ))
    register_plugin(Plugin(
        name="title-case",
        icon="linuxpop-case-title-symbolic",
        tooltip="Title Case",
        handler=_title,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=82,
        predicate=_not_already_title,
    ))
    register_plugin(Plugin(
        name="slugify",
        icon="linuxpop-slugify-symbolic",
        tooltip="Slugify (URL-friendly)",
        handler=_slugify,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=83,
        predicate=_has_letters,  # nothing to slug if it's all digits/symbols
    ))
