"""A bundle of small text-transform actions: reverse, sort lines, dedupe,
trim per line, strip HTML, remove diacritics, ASCII-only, swap case.
Each transforms the selection and copies the result to the clipboard.
"""
from __future__ import annotations

import re
import subprocess
import unicodedata

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"), check=False,
        timeout=2.0,
    )
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "accessories-text-editor", label, text[:200]],
        check=False,
    )


def _reverse(text: str) -> None:
    _copy(text[::-1], "Reversed")


def _sort_lines(text: str) -> None:
    lines = text.splitlines()
    _copy("\n".join(sorted(lines)), "Sorted lines")


def _dedupe_lines(text: str) -> None:
    seen, out = set(), []
    for line in text.splitlines():
        if line not in seen:
            seen.add(line)
            out.append(line)
    _copy("\n".join(out), f"Deduplicated ({len(out)} unique lines)")


def _trim_lines(text: str) -> None:
    _copy("\n".join(line.strip() for line in text.splitlines()), "Trimmed lines")


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> None:
    cleaned = _TAG_RE.sub("", text)
    # Collapse the common entities; lazy decode without importing html module
    cleaned = (cleaned.replace("&amp;", "&").replace("&lt;", "<")
                       .replace("&gt;", ">").replace("&quot;", '"')
                       .replace("&#39;", "'").replace("&nbsp;", " "))
    _copy(cleaned, "HTML stripped")


def _remove_diacritics(text: str) -> None:
    normalized = unicodedata.normalize("NFKD", text)
    out = "".join(c for c in normalized if not unicodedata.combining(c))
    _copy(out, "Diacritics removed")


def _ascii_only(text: str) -> None:
    out = text.encode("ascii", "ignore").decode("ascii")
    _copy(out, "ASCII only")


def _swap_case(text: str) -> None:
    _copy(text.swapcase(), "Case swapped")


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    register_plugin(Plugin(name="reverse-text", icon="object-flip-horizontal-symbolic",
        tooltip="Reverse text", handler=_reverse, content_types=types, priority=150))
    register_plugin(Plugin(name="sort-lines", icon="view-sort-ascending-symbolic",
        tooltip="Sort lines", handler=_sort_lines, content_types=types, priority=151))
    register_plugin(Plugin(name="dedupe-lines", icon="edit-clear-all-symbolic",
        tooltip="Deduplicate lines", handler=_dedupe_lines, content_types=types, priority=152))
    register_plugin(Plugin(name="trim-lines", icon="format-justify-left-symbolic",
        tooltip="Trim each line", handler=_trim_lines, content_types=types, priority=153))
    register_plugin(Plugin(name="strip-html", icon="text-x-generic-symbolic",
        tooltip="Strip HTML tags", handler=_strip_html, content_types=types, priority=154))
    register_plugin(Plugin(name="remove-diacritics", icon="format-text-strikethrough-symbolic",
        tooltip="Remove diacritics", handler=_remove_diacritics, content_types=types, priority=155))
    register_plugin(Plugin(name="ascii-only", icon="format-text-richtext-symbolic",
        tooltip="ASCII only", handler=_ascii_only, content_types=types, priority=156))
    register_plugin(Plugin(name="swap-case", icon="format-text-bold-symbolic",
        tooltip="Swap case", handler=_swap_case, content_types=types, priority=157))
