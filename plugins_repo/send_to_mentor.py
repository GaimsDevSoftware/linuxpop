"""Send the selected text to Mentor (the local Odysseus fork).

Mentor's web UI reads an `?ask=<text>` query parameter on load: it drops the
text into the composer and submits it to the active chat (the same hook
Mentor's own KRunner plugin uses). So we just open

    http://127.0.0.1:<port>/?ask=<urlencoded>

through the platform backend, which raises the browser to the foreground on
Wayland/KDE (focus-stealing prevention otherwise leaves it behind). A quick
/api/health probe first tells the user plainly when Mentor isn't running,
instead of dumping them on a dead-port error page.
"""
from __future__ import annotations

import os
import subprocess
import urllib.parse
import urllib.request

from classifier import ContentType
from plugin_base import Plugin


def _port() -> str:
    return (os.environ.get("MENTOR_PORT")
            or os.environ.get("APP_PORT")
            or "7000")


def _base() -> str:
    return f"http://127.0.0.1:{_port()}"


def _mentor_running() -> bool:
    try:
        with urllib.request.urlopen(f"{_base()}/api/health", timeout=1.5) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def _notify(title: str, body: str, icon: str = "linuxpop-mentor") -> None:
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000", "-i", icon,
         title, body],
        check=False,
    )


def _send(text: str) -> None:
    if not text or not text.strip():
        return
    if not _mentor_running():
        _notify("Mentor isn't running",
                f"Start Mentor (expected on {_base()}) and try again.",
                icon="dialog-warning")
        return
    url = f"{_base()}/?ask={urllib.parse.quote(text, safe='')}"
    try:
        __import__("platform_backend").get_backend().open_url(url)
    except Exception:
        try:
            subprocess.Popen(["xdg-open", url], close_fds=True)
        except FileNotFoundError:
            _notify("Could not open Mentor", "xdg-open is missing",
                    icon="dialog-error")
            return
    _notify("Sent to Mentor", "Opened the active chat with your selection.")


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="send-to-mentor",
        icon="linuxpop-mentor",
        tooltip="Send to Mentor",
        handler=_send,
        content_types=(ContentType.PLAIN_TEXT, ContentType.URL,
                       ContentType.EMAIL, ContentType.PATH),
        priority=98,
    ))
