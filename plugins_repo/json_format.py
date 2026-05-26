"""Pretty-print JSON: copies formatted result to clipboard."""
from __future__ import annotations

import json
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _looks_like_json(text: str) -> bool:
    """Cheap shape check - does the text *start* with a JSON container or
    string? We deliberately don't json.loads() here (too slow to run on
    every popup show)."""
    s = text.lstrip()
    if not s:
        return False
    first = s[0]
    if first not in '{["':
        return False
    # A lone '{' or '[' is too eager - require at least a paired closer
    # somewhere in the selection.
    last = s.rstrip()[-1:]
    return (first, last) in (("{", "}"), ("[", "]"), ('"', '"'))


def _format(text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "JSON error", str(exc)[:300]],
            check=False,
        )
        return
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    import actions
    actions.replace_selection(pretty)
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",  "-i", "accessories-text-editor",
         "JSON formatted", f"Replaced selection ({len(pretty)} chars)"],
        check=False,
    )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="json-format",
        icon="linuxpop-json-symbolic",
        tooltip="JSON pretty-print",
        handler=_format,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=55,
        predicate=_looks_like_json,
    ))
