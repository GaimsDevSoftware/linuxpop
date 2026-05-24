"""Editing actions — Cut, Paste, Backspace, Select All.

PopClip's bread-and-butter buttons. They all operate on the currently
focused editable text input by simulating the standard keyboard
shortcut via xdotool:

  Cut         → Ctrl+X        (copy selection + remove from source)
  Paste       → Ctrl+V        (replace selection with clipboard content)
  Backspace   → BackSpace     (delete selection)
  Select All  → Ctrl+A        (expand selection to the whole field)

LinuxPop's popup has set_accept_focus(False), so the source app keeps
keyboard focus the whole time the popup is up — xdotool's key event
lands there, not on the popup window. Will only have a visible effect
in an editable field; clicking 'Cut' on a read-only web page does
nothing, same as PopClip on macOS.
"""
from __future__ import annotations

import shutil
import subprocess

from plugin_base import Plugin


def _send_keys(combo: str) -> None:
    if not shutil.which("xdotool"):
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",
             "-u", "critical", "-i", "dialog-error",
             "LinuxPop",
             "xdotool is not installed — required for the editing actions. "
             "Install with: sudo apt install xdotool"],
            check=False,
        )
        return
    try:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", combo],
            check=False, timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        # xdotool stuck — extremely rare but a hung worker is worse
        # than a missed action; just bail silently.
        pass


def _cut(_text: str) -> None:
    _send_keys("ctrl+x")


def _paste(_text: str) -> None:
    _send_keys("ctrl+v")


def _backspace(_text: str) -> None:
    _send_keys("BackSpace")


def _select_all(_text: str) -> None:
    _send_keys("ctrl+a")


def register(register_plugin) -> None:
    # Priority: just behind the universal "copy" (which is 10) so the
    # editing cluster sits together at the start of the popup row.
    register_plugin(Plugin(
        name="cut",
        icon="edit-cut-symbolic",
        tooltip="Cut",
        handler=_cut,
        content_types=(),  # universal — show for every selection
        priority=11,
        requires_editable=True,
    ))
    register_plugin(Plugin(
        name="paste",
        icon="edit-paste-symbolic",
        tooltip="Paste (replace selection with clipboard)",
        handler=_paste,
        content_types=(),
        priority=12,
        requires_editable=True,
    ))
    register_plugin(Plugin(
        name="backspace",
        icon="edit-delete-symbolic",
        tooltip="Delete selection",
        handler=_backspace,
        content_types=(),
        priority=13,
        requires_editable=True,
    ))
    # Select All is left as 'always show' — Ctrl+A works on read-only
    # widgets too (browsers, PDF viewers expand the selection to the
    # whole document), so it stays useful even without an editable focus.
    register_plugin(Plugin(
        name="select-all",
        icon="edit-select-all-symbolic",
        tooltip="Select all",
        handler=_select_all,
        content_types=(),
        priority=14,
    ))
