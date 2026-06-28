"""Global hotkey via evdev (/dev/input) for Wayland sessions where an X11
grab cannot see a window that a *native* Wayland app has focused - notably
GNOME/Mutter, where XGrabKey through XWayland only fires while an XWayland
window is focused.

Reading key events straight from the kernel input devices means the hotkey
fires regardless of which toolkit/app has focus. Modifier state is tracked
through libxkbcommon (reusing the KDE backend's _XkbModifierState), so a
Ctrl/Alt swap or any other xkb option is honoured rather than hard-coding
which physical keycode is Ctrl. Needs the user in the 'input' group (same
requirement as ydotool); logs and stays inert otherwise.

Interface mirrors hotkey.Hotkey: start() / stop(wait, timeout), so main.py
treats it identically to the X11 hotkey.
"""
from __future__ import annotations

import os
import select as _select
import struct
import threading
import time
from typing import Callable, Optional

from gi.repository import GLib

# evdev keycodes (linux/input-event-codes.h) for the keys a hotkey may use as
# its main (non-modifier) key. Keyed by lowercased name / alias. These are
# physical-position codes; a hotkey therefore tracks the physical key, which is
# the conventional behaviour for global shortcuts.
_KEYCODES = {
    **{c: 2 + i for i, c in enumerate("1234567890")},  # 1..0 -> 2..11
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
    "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
    "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
    "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
    "minus": 12, "equal": 13, "bracketleft": 26, "bracketright": 27,
    "semicolon": 39, "apostrophe": 40, "grave": 41, "backslash": 43,
    "comma": 51, "period": 52, "dot": 52, "slash": 53,
    "space": 57, "spacebar": 57,
    "return": 28, "enter": 28, "tab": 15, "escape": 1, "esc": 1,
    "backspace": 14, "delete": 111, "insert": 110,
    "home": 102, "end": 107, "pageup": 104, "prior": 104,
    "pagedown": 109, "next": 109,
    "up": 103, "down": 108, "left": 105, "right": 106,
    "f1": 59, "f2": 60, "f3": 61, "f4": 62, "f5": 63, "f6": 64,
    "f7": 65, "f8": 66, "f9": 67, "f10": 68, "f11": 87, "f12": 88,
}

# Hotkey-string modifier tokens -> canonical modifier name.
_MOD_ALIASES = {
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt", "mod1": "alt",
    "super": "super", "win": "super", "meta": "super", "mod4": "super",
}

# evdev keycodes for each modifier (left/right), used for the raw fallback
# when libxkbcommon is unavailable.
_MOD_CODES = {
    "ctrl":  {29, 97},    # KEY_LEFTCTRL / KEY_RIGHTCTRL
    "shift": {42, 54},    # KEY_LEFTSHIFT / KEY_RIGHTSHIFT
    "alt":   {56, 100},   # KEY_LEFTALT / KEY_RIGHTALT
    "super": {125, 126},  # KEY_LEFTMETA / KEY_RIGHTMETA
}
_ALL_MOD_CODES = {c for s in _MOD_CODES.values() for c in s}
_ALL_MODS = ("ctrl", "shift", "alt", "super")

# Suppress duplicate fires that can arrive within the same millisecond when a
# keyboard is exposed through more than one event node.
_DEDUP_MS = 100


def _parse(hotkey: str) -> tuple[frozenset, int, str]:
    """Return (required-modifier-name set, main evdev keycode, key name).

    Raises ValueError for an empty string, an unknown modifier, or a main
    key not in _KEYCODES."""
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty hotkey: {hotkey!r}")
    key_token = parts[-1]
    mods = set()
    for token in parts[:-1]:
        name = _MOD_ALIASES.get(token)
        if name is None:
            raise ValueError(f"unknown modifier {token!r} in {hotkey!r}")
        mods.add(name)
    code = _KEYCODES.get(key_token)
    if code is None:
        raise ValueError(f"unknown key {key_token!r} in {hotkey!r}")
    return frozenset(mods), code, key_token


class EvdevHotkey:
    def __init__(
        self,
        hotkey_str: str,
        on_trigger: Callable[[], None],
        use_polling: bool = False,
    ) -> None:
        self._hotkey_str = hotkey_str
        self._on_trigger = on_trigger
        # use_polling is irrelevant for evdev (already event-driven and
        # grab-free); accepted only for interface parity with hotkey.Hotkey.
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._held: set[int] = set()   # raw modifier keycodes down (fallback)
        self._xkb = None               # layout-aware modifier state
        self._last_fire_ms = 0

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-hotkey-evdev")
        self._thread.start()

    def stop(self, wait: bool = True, timeout: float = 1.5) -> None:
        self._stop.set()
        t = self._thread
        if wait and t is not None and t.is_alive():
            t.join(timeout=timeout)

    def _open_devices(self) -> list:
        import glob
        # by-path *-event-kbd names the physical keyboards and excludes
        # ydotool's virtual uinput node (no bus path), so injected keys never
        # trigger the hotkey.
        paths = set()
        for p in glob.glob("/dev/input/by-path/*-event-kbd"):
            try:
                paths.add(os.path.realpath(p))
            except OSError:
                pass
        if not paths:
            paths = set(glob.glob("/dev/input/event*"))
        fds = []
        for path in sorted(paths):
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass
        return fds

    def _run(self) -> None:
        try:
            req_mods, main_code, _key_name = _parse(self._hotkey_str)
        except ValueError as exc:
            print(f"[hotkey-evdev] {exc}")
            return
        fds = self._open_devices()
        if not fds:
            print("[hotkey-evdev] no readable input devices - add yourself to "
                  "the 'input' group? Global hotkey disabled.")
            return
        # Layout-aware modifier state (honours Ctrl/Alt swap etc.). Reused
        # from the KDE backend; falls back to raw keycodes if libxkbcommon is
        # missing.
        from .wayland_kde import _XkbModifierState
        self._xkb = _XkbModifierState()
        print(f"[hotkey-evdev] listening for {self._hotkey_str!r} "
              f"(code={main_code}, mods={sorted(req_mods)}) on "
              f"{len(fds)} device(s)")
        EV_KEY = 0x01
        fmt = "llHHi"
        size = struct.calcsize(fmt)
        try:
            while not self._stop.is_set():
                r, _, _ = _select.select(fds, [], [], 0.5)
                for fd in r:
                    try:
                        data = os.read(fd, size * 64)
                    except (BlockingIOError, OSError):
                        continue
                    for off in range(0, len(data) - size + 1, size):
                        _s, _us, et, code, val = struct.unpack_from(
                            fmt, data, off)
                        if et != EV_KEY:
                            continue
                        # Feed xkb (press=1/release=0; ignore autorepeat=2)
                        # and keep the raw-code fallback set in sync.
                        if val in (0, 1):
                            self._xkb.update(code, val == 1)
                        if code in _ALL_MOD_CODES:
                            if val == 1:
                                self._held.add(code)
                            elif val == 0:
                                self._held.discard(code)
                            continue
                        # Main key press only (val==1; ignore release/repeat).
                        if code == main_code and val == 1:
                            if self._mods_match(req_mods) and self._fresh():
                                print(f"[hotkey-evdev] {self._hotkey_str!r} "
                                      "fired -- scheduling trigger")
                                GLib.idle_add(self._safe_trigger)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _mods_match(self, req_mods) -> bool:
        """Exact match: every required modifier active, no others. Uses the
        layout-aware xkb state, falling back to raw keycodes per-modifier when
        xkb is unavailable."""
        for name in _ALL_MODS:
            want = name in req_mods
            active = self._xkb.is_active(name) if self._xkb else None
            if active is None:
                active = bool(self._held & _MOD_CODES[name])
            if active != want:
                return False
        return True

    def _fresh(self) -> bool:
        """Debounce duplicate events from multiple device nodes."""
        now = int(time.monotonic() * 1000)
        if now - self._last_fire_ms < _DEDUP_MS:
            return False
        self._last_fire_ms = now
        return True

    def _safe_trigger(self) -> bool:
        try:
            self._on_trigger()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey-evdev] trigger handler failed: {exc}")
        return False
