"""Speak the selection aloud via spd-say (speech-dispatcher) or espeak."""
from __future__ import annotations

import shutil
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _speak(text: str) -> None:
    text = text.strip()
    if not text:
        return
    if shutil.which("spd-say"):
        # spd-say is non-blocking by default and uses the user's preferred voice
        subprocess.Popen(["spd-say", "--", text], start_new_session=True)
        return
    if shutil.which("espeak"):
        subprocess.Popen(["espeak", "--", text], start_new_session=True)
        return
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "Speak - missing dependency",
         "Install spd-say (speech-dispatcher) or espeak"],
        check=False,
    )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="speak-aloud",
        icon="audio-speakers-symbolic",
        tooltip="Speak aloud",
        handler=_speak,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=210,
    ))
