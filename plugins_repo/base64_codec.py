"""Base64 encode/decode: writes result to clipboard and shows a notification."""
from __future__ import annotations

import base64
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _notify(title: str, body: str) -> None:
    try:
        subprocess.run(
            ["notify-send", "-i", "accessories-character-map", title, body[:300]],
            check=False,
        )
    except FileNotFoundError:
        print(f"[{title}] {body}")


def _to_clipboard(text: str) -> None:
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass


def _encode(text: str) -> None:
    enc = base64.b64encode(text.encode("utf-8")).decode("ascii")
    _to_clipboard(enc)
    _notify("Base64 encoded", f"Copied ({len(enc)} chars): {enc[:80]}")


def _decode(text: str) -> None:
    try:
        dec = base64.b64decode(text.strip(), validate=True).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        _notify("Base64 error", f"Could not decode: {exc}")
        return
    _to_clipboard(dec)
    _notify("Base64 decoded", f"Copied: {dec[:120]}")


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="base64-encode",
        icon="linuxpop-base64-encode-symbolic",
        tooltip="Base64 encode",
        handler=_encode,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=60,
    ))
    register_plugin(Plugin(
        name="base64-decode",
        icon="linuxpop-base64-decode-symbolic",
        tooltip="Base64 decode",
        handler=_decode,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=61,
    ))
