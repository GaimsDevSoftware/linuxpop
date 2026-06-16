"""Show word, character and line counts of the selected text via notification."""
from __future__ import annotations

import shutil
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _count(text: str) -> None:
    words = len(text.split())
    chars = len(text)
    chars_no_ws = len(text.replace(" ", "").replace("\n", "").replace("\t", ""))
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    body = (
        f"{words} words, {chars} chars "
        f"({chars_no_ws} without whitespace), {lines} lines"
    )
    # Always log the result; this is the only feedback when a sandboxed
    # environment cannot reach the host notification daemon.
    print(f"[wordcount] {body}")
    # Also put the summary on the clipboard so the user can paste it if
    # the desktop notification does not show up.
    try:
        from platform_backend import get_backend
        get_backend().set_clipboard(body)
    except Exception as exc:
        print(f"[wordcount] clipboard fallback failed: {exc}")
    if shutil.which("notify-send"):
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "accessories-text-editor", "LinuxPop wordcount", body],
            check=False,
        )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="wordcount",
        icon="linuxpop-wordcount-symbolic",
        tooltip="Word & char count",
        handler=_count,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=50,
    ))
