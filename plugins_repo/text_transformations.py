"""A bundle of small text-transform actions: reverse, sort lines, dedupe,
trim per line, strip HTML, remove diacritics, ASCII-only, swap case.
Each transforms the selection and copies the result to the clipboard.
"""
from __future__ import annotations

import random as _random
import re
import subprocess
import unicodedata

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    """Replace the user's selection with `text` AND copy to clipboard.
    Falls back to clipboard-only if the focused widget is read-only -
    user still gets the transformed text, they just have to paste it
    themselves. Imported from actions so all transformers share the
    same paste-over-selection behaviour."""
    import actions
    actions.replace_selection(text)
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",  "-i", "accessories-text-editor", label, text[:200]],
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


# Recognises real tag-shaped HTML (open or close), not stray '<' chars
# in prose. Requires at least one letter after the opening '<'.
_TAG_RE = re.compile(r"<\/?[a-zA-Z][^<>]*>")


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


# ---- predicates (cheap, run on every popup show) -----------------------

def _has_multiple_lines(text: str) -> bool:
    return "\n" in text.strip("\n")


def _lines_can_dedupe(text: str) -> bool:
    """At least 2 non-empty lines AND at least one duplicate."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) >= 2 and len(set(lines)) < len(lines)


def _lines_have_edge_whitespace(text: str) -> bool:
    """At least one line has leading or trailing whitespace worth trimming."""
    for ln in text.splitlines():
        if ln != ln.strip():
            return True
    return False


def _has_html_tags(text: str) -> bool:
    return bool(_TAG_RE.search(text))


def _has_diacritics(text: str) -> bool:
    """Any combining mark (NFKD decomposition unique vs original)."""
    return unicodedata.normalize("NFKD", text) != text


def _has_non_ascii(text: str) -> bool:
    return any(ord(c) > 127 for c in text)


def _has_letters(text: str) -> bool:
    """Skip case-changing buttons on selections without alphabetic chars
    (numbers, symbols, emoji-only selections)."""
    return any(c.isalpha() for c in text)


def _has_mixed_case(text: str) -> bool:
    """Swap-case is only meaningful when there's actually case to swap.
    Otherwise it does the same thing as upper / lower."""
    return any(c.isupper() for c in text) and any(c.islower() for c in text)


def _worth_reversing(text: str) -> bool:
    """Reversing 1-2 chars is rarely useful; the action is mostly a
    novelty on full words/sentences."""
    return len(text.strip()) >= 3


def _worth_mocking(text: str) -> bool:
    """Mocking SpongeBob is for full quotes - single words look silly."""
    return _has_letters(text) and len(text.strip()) >= 4


def _mock_case(text: str) -> None:
    """Random upper/lower per char - the SpongeBob mocking format.
    Seeded with the selection so the result is stable across re-clicks
    on the same text (less surprising than fresh randomness each time)."""
    rng = _random.Random(text)
    out = "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in text)
    _copy(out, "mOcKiNg cAsE")


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    # Reverse: any non-empty selection - no predicate needed.
    register_plugin(Plugin(name="reverse-text", icon="object-flip-horizontal-symbolic",
        tooltip="Reverse text", handler=_reverse, content_types=types, priority=150,
        predicate=_worth_reversing))
    # Sort: only show when there's more than one line to sort.
    register_plugin(Plugin(name="sort-lines", icon="view-sort-ascending-symbolic",
        tooltip="Sort lines", handler=_sort_lines, content_types=types, priority=151,
        predicate=_has_multiple_lines))
    # Dedupe: only show when there's actually a duplicate to remove.
    register_plugin(Plugin(name="dedupe-lines", icon="edit-clear-all-symbolic",
        tooltip="Deduplicate lines", handler=_dedupe_lines, content_types=types, priority=152,
        predicate=_lines_can_dedupe))
    # Trim: only show if at least one line has leading/trailing whitespace.
    register_plugin(Plugin(name="trim-lines", icon="format-justify-left-symbolic",
        tooltip="Trim each line", handler=_trim_lines, content_types=types, priority=153,
        predicate=_lines_have_edge_whitespace))
    # Strip-HTML: only show if there's an actual <tag> in the selection.
    register_plugin(Plugin(name="strip-html", icon="text-x-generic-symbolic",
        tooltip="Strip HTML tags", handler=_strip_html, content_types=types, priority=154,
        predicate=_has_html_tags))
    # Remove diacritics: only show when there are accented characters.
    register_plugin(Plugin(name="remove-diacritics", icon="format-text-strikethrough-symbolic",
        tooltip="Remove diacritics", handler=_remove_diacritics, content_types=types, priority=155,
        predicate=_has_diacritics))
    # ASCII-only: only show when there are non-ASCII characters to drop.
    register_plugin(Plugin(name="ascii-only", icon="format-text-richtext-symbolic",
        tooltip="ASCII only", handler=_ascii_only, content_types=types, priority=156,
        predicate=_has_non_ascii))
    # Swap / mock case: only meaningful if the selection has letters.
    register_plugin(Plugin(name="swap-case", icon="format-text-bold-symbolic",
        tooltip="Swap case", handler=_swap_case, content_types=types, priority=157,
        predicate=_has_mixed_case))
    register_plugin(Plugin(name="mock-case", icon="face-laugh-symbolic",
        tooltip="mOcKiNg cAsE", handler=_mock_case, content_types=types, priority=158,
        predicate=_worth_mocking))
