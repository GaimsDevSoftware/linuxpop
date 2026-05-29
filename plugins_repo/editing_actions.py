"""Editing actions - Cut, Paste, Backspace, Select All.

PopClip's bread-and-butter buttons. They all operate on the currently
focused editable text input by simulating the standard keyboard
shortcut via xdotool:

  Cut         → Ctrl+X        (copy selection + remove from source)
  Paste       → Ctrl+V        (replace selection with clipboard content)
  Backspace   → BackSpace     (delete selection)
  Select All  → Ctrl+A        (expand selection to the whole field)

LinuxPop's popup has set_accept_focus(False), so the source app keeps
keyboard focus the whole time the popup is up - xdotool's key event
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
             "xdotool is not installed - required for the editing actions. "
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
        # xdotool stuck - extremely rare but a hung worker is worse
        # than a missed action; just bail silently.
        pass


def _cut(_text: str) -> None:
    _send_keys("ctrl+x")


def _paste(_text: str) -> None:
    _send_keys("ctrl+v")


def _paste_and_enter(_text: str) -> None:
    """Paste clipboard, settle, then press Return. One-button submit for
    chat boxes, search bars, and terminal prompts where the user already
    has the next thing they want to run on the clipboard. The settle
    pause matters - some Electron apps (Discord, Slack, Claude desktop)
    debounce input events; without it the Enter beats the paste's
    committed-text state and the field submits empty."""
    import time as _t
    _send_keys("ctrl+v")
    _t.sleep(0.08)
    _send_keys("Return")


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
        content_types=(),  # universal - show for every selection
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
    # Paste & Enter: paste the clipboard then submit. The icon is the
    # standard send / right-arrow glyph so the difference from plain
    # Paste reads at a glance.
    register_plugin(Plugin(
        name="paste-and-enter",
        icon="mail-send-symbolic",
        tooltip="Paste & Enter (paste clipboard, then press Return)",
        handler=_paste_and_enter,
        content_types=(),
        priority=15,
        requires_editable=True,
    ))
    # Same handler (xdotool BackSpace key) as the no-selection edit
     # menu's "Backspace" button - label them the same so the user
    # doesn't read them as two separate actions. With a selection
    # active, BackSpace deletes the selection; without one it deletes
    # the character before the cursor - same key, different effect.
    register_plugin(Plugin(
        name="backspace",
        # edit-clear-symbolic is the LTR Backspace glyph (arrow pointing
        # left with an X inside). The -rtl variant points right and is
        # for right-to-left languages - wrong direction for us.
        icon="edit-clear-symbolic",
        tooltip="Backspace (delete the selected text)",
        handler=_backspace,
        content_types=(),
        priority=13,
        requires_editable=True,
    ))
    # Select All is left as 'always show' - Ctrl+A works on read-only
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
