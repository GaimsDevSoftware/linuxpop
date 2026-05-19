"""ROT13 — classic letter rotation cipher. Reversible (apply twice = original)."""
from __future__ import annotations

import codecs
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _rot13(text: str) -> None:
    out = codecs.encode(text, "rot_13")
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=out.encode("utf-8"), check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "view-refresh-symbolic", "ROT13", out[:200]],
        check=False,
    )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="rot13",
        icon="view-refresh-symbolic",
        tooltip="ROT13 cipher",
        handler=_rot13,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=200,
    ))
