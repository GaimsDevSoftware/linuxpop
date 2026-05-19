"""Pretty-print JSON: copies formatted result to clipboard."""
from __future__ import annotations

import json
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _format(text: str) -> None:
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        subprocess.run(
            ["notify-send", "-i", "dialog-error", "JSON error", str(exc)[:300]],
            check=False,
        )
        return
    pretty = json.dumps(obj, indent=2, ensure_ascii=False)
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=pretty.encode("utf-8"),
        check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "accessories-text-editor",
         "JSON formatted", f"Copied ({len(pretty)} chars)"],
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
    ))
