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
    # Key injection goes through the platform backend: xdotool on X11,
    # wtype on Wayland/KDE. The backend handles a missing tool itself.
    from platform_backend import get_backend
    get_backend().send_key(combo)


def _cut(_text: str) -> None:
    _send_keys("ctrl+x")


def _paste(_text: str) -> None:
    _send_keys("ctrl+v")


# WM_CLASS substrings of apps where pressing plain Return inserts a
# newline instead of submitting. For those, Paste & Enter sends
# Ctrl+Return (or Ctrl+Shift+Return for Discord's "send" binding).
_NEWLINE_ENTER_CLASSES = (
    "slack",         # Slack desktop and web client (chrome-app)
    "discord",       # Discord desktop
    "ms-teams",      # Microsoft Teams
    "teams-for-linux",
    "element",       # Element (Matrix client) - shift+enter newline
    "thunderbird",   # Compose window
)


def _submit_keystroke_for_focus() -> str:
    """Probe the focused window's WM_CLASS and decide whether plain
    Return submits, or whether we need Ctrl+Return. Default is plain
    Return - it's the right answer for terminals, search bars,
    address bars, Claude/ChatGPT/Gemini web, single-line chat boxes."""
    from platform_backend import get_backend
    blob = " ".join(get_backend().active_window_haystacks())
    for needle in _NEWLINE_ENTER_CLASSES:
        if needle in blob:
            return "ctrl+Return"
    return "Return"


def _paste_and_enter(_text: str) -> None:
    """Paste clipboard, settle, then submit. Picks Ctrl+Return for apps
    where Return inserts a newline (Slack, Discord, Teams, Element,
    Thunderbird compose) and plain Return everywhere else (terminals,
    search bars, single-line chat boxes, Claude/ChatGPT/Gemini web).
    The settle pause matters - Electron apps debounce input events;
    without it Enter beats the paste's committed-text state and the
    field submits empty."""
    import time as _t
    submit_key = _submit_keystroke_for_focus()
    _send_keys("ctrl+v")
    _t.sleep(0.08)
    _send_keys(submit_key)


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
