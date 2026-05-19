"""Global X11 hotkey registration.

Parses a hotkey string like "ctrl+alt+space" and grabs it on the X11
root window. When pressed anywhere, fires a callback on the GTK main
thread (via GLib.idle_add). Threaded so the GTK main loop is untouched.
"""
from __future__ import annotations

import threading
from typing import Callable, Iterable, Optional

from Xlib import X, XK, display, error

from gi.repository import GLib

# Standard modifier names mapped to X11 modifier masks
_MOD_NAMES = {
    "shift": X.ShiftMask,
    "ctrl": X.ControlMask,
    "control": X.ControlMask,
    "alt": X.Mod1Mask,
    "mod1": X.Mod1Mask,
    "super": X.Mod4Mask,
    "win": X.Mod4Mask,
    "meta": X.Mod4Mask,
    "mod4": X.Mod4Mask,
    "hyper": X.Mod3Mask,
    "mod3": X.Mod3Mask,
}

# Friendly key aliases → XK_* strings (XStringToKeysym format)
_KEY_ALIASES = {
    "space": "space",
    "spacebar": "space",
    "tab": "Tab",
    "return": "Return",
    "enter": "Return",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "BackSpace",
    "delete": "Delete",
    "insert": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "pagedown": "Page_Down",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}


def _parse(hotkey: str) -> tuple[int, str]:
    """Return (modifier_mask, keysym_name) for a hotkey string."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty hotkey: {hotkey!r}")
    mods = 0
    key_token = parts[-1]
    for token in parts[:-1]:
        if token not in _MOD_NAMES:
            raise ValueError(f"unknown modifier {token!r} in {hotkey!r}")
        mods |= _MOD_NAMES[token]
    # Map alias; otherwise return the token itself — the resolver will try
    # several capitalizations against XK.string_to_keysym.
    if key_token in _KEY_ALIASES:
        key_name = _KEY_ALIASES[key_token]
    elif len(key_token) > 1 and key_token.startswith("f") and key_token[1:].isdigit():
        key_name = key_token.upper()  # F1..F12
    else:
        key_name = key_token
    return mods, key_name


def _resolve_keysym(name: str) -> int:
    """Try multiple capitalizations against XK.string_to_keysym."""
    # X11 keysym names are case-sensitive and inconsistent: 'Return', 'space',
    # 'BackSpace', 'greater', 'Up'. Try the most likely variants in order.
    candidates = [
        name,                              # as-given (e.g. 'greater', 'F5')
        name.lower(),                      # 'greater'
        name[:1].upper() + name[1:],       # 'Greater' (works for Return etc.)
        name.upper(),                      # 'GREATER' (rare)
    ]
    seen: set[str] = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        ks = XK.string_to_keysym(cand)
        if ks:
            return ks
    return 0


def _lock_variants(dpy: display.Display) -> Iterable[int]:
    """Generate all mod-mask variants that should be ignored by the grab."""
    # We always combine with each combo of LockMask (caps) and Mod2Mask (numlock)
    ignored = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]
    return ignored


class Hotkey:
    def __init__(
        self,
        hotkey_str: str,
        on_trigger: Callable[[], None],
    ) -> None:
        self._hotkey_str = hotkey_str
        self._on_trigger = on_trigger
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._mods: int = 0
        self._keycode: int = 0
        self._grabbed_combos: list[tuple[int, int]] = []  # (keycode, modmask)
        self._dpy: Optional[display.Display] = None
        self._root = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="linuxpop-hotkey")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            self._dpy = display.Display()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] cannot open display: {exc}")
            return

        try:
            mods, keysym_name = _parse(self._hotkey_str)
        except ValueError as exc:
            print(f"[hotkey] {exc}")
            return

        keysym = _resolve_keysym(keysym_name)
        if keysym == 0:
            print(f"[hotkey] unknown key name: {keysym_name!r}")
            return

        keycode = self._dpy.keysym_to_keycode(keysym)
        if keycode == 0:
            print(f"[hotkey] no keycode for {keysym_name!r}")
            return

        self._mods = mods
        self._keycode = keycode
        self._root = self._dpy.screen().root

        # Grab the key with every lock-modifier variant, checking each for
        # an async BadAccess (= another client already owns this combo).
        # CatchError is per-request: instantiate fresh each iteration.
        grab_failed = 0
        for extra in _lock_variants(self._dpy):
            mask = mods | extra
            catcher = error.CatchError(error.BadAccess)
            try:
                self._root.grab_key(
                    keycode,
                    mask,
                    1,  # owner_events
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    onerror=catcher,
                )
                self._dpy.sync()
                if catcher.get_error():
                    grab_failed += 1
                else:
                    self._grabbed_combos.append((keycode, mask))
            except Exception as exc:  # noqa: BLE001
                print(f"[hotkey] grab failed for mask 0x{mask:x}: {exc}")
                grab_failed += 1

        # If EVERY variant failed, the user's hotkey is unusable — notify them
        # instead of silently doing nothing.
        if not self._grabbed_combos:
            print(f"[hotkey] '{self._hotkey_str}' could not be grabbed — already in use")
            try:
                import subprocess
                subprocess.run(
                    ["notify-send", "-u", "critical", "-i", "dialog-warning",
                     "LinuxPop hotkey conflict",
                     f"'{self._hotkey_str}' is already bound by another app. "
                     "Pick a different combination in Settings."],
                    check=False,
                )
            except Exception:
                pass
            return
        if grab_failed:
            print(f"[hotkey] {grab_failed} lock-variant grab(s) refused (non-fatal)")

        print(f"[hotkey] listening for {self._hotkey_str} (keycode={keycode}, mods=0x{mods:x})")

        # Event loop
        while not self._stop.is_set():
            # next_event blocks; use pending_events poll with small sleep so we can stop
            if self._dpy.pending_events() == 0:
                # Block with a short timeout via select on the fd
                import select
                try:
                    select.select([self._dpy.fileno()], [], [], 0.2)
                except OSError:
                    break
                continue
            event = self._dpy.next_event()
            if event.type == X.KeyPress and event.detail == keycode:
                # Filter out the lock-only variants by masking expected mods
                effective = event.state & (
                    X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod3Mask | X.Mod4Mask
                )
                if effective == mods:
                    GLib.idle_add(self._safe_trigger)

        # Cleanup
        for keycode, mask in self._grabbed_combos:
            try:
                self._root.ungrab_key(keycode, mask)
            except Exception:
                pass
        try:
            self._dpy.close()
        except Exception:
            pass

    def _safe_trigger(self) -> bool:
        try:
            self._on_trigger()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] trigger handler failed: {exc}")
        return False
