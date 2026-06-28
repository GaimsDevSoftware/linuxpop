"""Run a selected shell command in a terminal.

The classifier tags shell-command-looking selections as
``ContentType.COMMAND`` (e.g. ``wpctl set-volume @DEFAULT_AUDIO_SINK@ 50%``),
but nothing acted on them - this plugin adds the missing "Run in terminal"
button for exactly that content type.

The heavy lifting lives in ``actions.run_in_terminal``: it finds an available
terminal emulator, optionally shows a confirmation dialog
(``terminal_confirm_run``, default on), echoes the command, runs it, and keeps
the terminal open afterwards (``terminal_keep_open``, default on). Launch is
done with a real argv list (never ``shell=True`` at construction), and the
confirmation dialog is the guard against running a malicious-looking selection
by accident.
"""
from __future__ import annotations

import actions
from classifier import ContentType
from plugin_base import Plugin


def _run(text: str) -> None:
    actions.run_in_terminal(text)


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="run-in-terminal",
        icon="utilities-terminal-symbolic",
        tooltip="Run in terminal",
        handler=_run,
        content_types=(ContentType.COMMAND,),
        # Lower than the transform/utility plugins so it sits toward the
        # end of the bar - it's a deliberate, occasional action, not a
        # first-reach one.
        priority=30,
    ))
