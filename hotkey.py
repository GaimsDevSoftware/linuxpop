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
        use_polling: bool = False,
    ) -> None:
        self._hotkey_str = hotkey_str
        self._on_trigger = on_trigger
        # `use_polling` switches from XGrabKey (event-driven) to
        # XQueryKeymap polling (~50 ms). Polling bypasses grab races —
        # e.g. Cinnamon briefly grabbing the keyboard on Super-down for
        # its overview gesture, which silently eats the first press of
        # any Super-based hotkey. Costs ~3-5 % CPU per hotkey and
        # ~25 ms median trigger latency. Off by default.
        self._use_polling = use_polling
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

    def stop(self, wait: bool = True, timeout: float = 1.5) -> None:
        """Stop the hotkey thread. When `wait` (default), block until the
        thread exits and the X11 grab is released — required by live-rebind
        in main.py so the old thread's ungrab doesn't race the new thread's
        grab on the same key and silently steal it."""
        self._stop.set()
        if wait and self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

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

        # Polling mode bypasses XGrabKey entirely — just samples the
        # keymap every 50 ms and edge-detects 0→1 transitions. Used as
        # the escape hatch for hotkeys that fight with WM-level grabs
        # (Cinnamon Super-overview is the canonical example).
        if self._use_polling:
            print(f"[hotkey] '{self._hotkey_str}' polling mode "
                  f"(keycode={keycode}, mods=0x{mods:x})")
            self._poll_loop()
            try:
                self._dpy.close()
            except Exception:
                pass
            return

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
                    ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-u", "critical", "-i", "dialog-warning",
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

        # Event loop. We poll select() with a 1 s timeout so the daemon
        # can stop within 1 s of self._stop being set, while staying mostly
        # asleep when no hotkey traffic arrives. (Previously: 0.2 s, which
        # woke this thread 5 times/sec per registered hotkey — multiply by
        # the popup + clipboard hotkeys and the daemon was getting 10
        # context switches/sec for no reason.)
        while not self._stop.is_set():
            if self._dpy.pending_events() == 0:
                import select
                try:
                    select.select([self._dpy.fileno()], [], [], 1.0)
                except OSError:
                    break
                continue
            event = self._dpy.next_event()
            # MappingNotify (type 34): the X server is telling us the
            # keyboard mapping changed. Our grab is on a specific keycode,
            # but the user's keysym (e.g. 'greater') may have moved to a
            # different keycode — in which case our grab silently stops
            # catching their presses until we re-grab on the new keycode.
            # On Norwegian setups with IBus / kbdd that swap layouts per
            # app, these arrive in floods and were the root cause of
            # "first press of the hotkey is silently dropped".
            if event.type == X.MappingNotify:
                try:
                    event.refresh_keyboard_mapping()
                except Exception:
                    pass
                new_keycode = self._dpy.keysym_to_keycode(keysym)
                if new_keycode and new_keycode != keycode:
                    print(f"[hotkey] '{self._hotkey_str}' keycode shifted "
                          f"{keycode} -> {new_keycode} after MappingNotify; "
                          f"re-grabbing")
                    # Ungrab the old combos, grab the new one
                    for old_kc, old_mask in list(self._grabbed_combos):
                        try:
                            self._root.ungrab_key(old_kc, old_mask)
                        except Exception:
                            pass
                    self._grabbed_combos.clear()
                    for extra in _lock_variants(self._dpy):
                        mask = mods | extra
                        catcher = error.CatchError(error.BadAccess)
                        try:
                            self._root.grab_key(
                                new_keycode, mask, 1,
                                X.GrabModeAsync, X.GrabModeAsync,
                                onerror=catcher,
                            )
                            self._dpy.sync()
                            if not catcher.get_error():
                                self._grabbed_combos.append((new_keycode, mask))
                        except Exception:
                            pass
                    keycode = new_keycode
                continue
            if event.type == X.KeyPress and event.detail == keycode:
                # Filter out the lock-only variants by masking expected mods
                effective = event.state & (
                    X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod3Mask | X.Mod4Mask
                )
                if effective == mods:
                    print(f"[hotkey] '{self._hotkey_str}' fired -- "
                          f"scheduling trigger on GTK main thread")
                    GLib.idle_add(self._safe_trigger)
                else:
                    print(f"[hotkey] '{self._hotkey_str}' press ignored: "
                          f"expected mods=0x{mods:x} got effective=0x{effective:x} "
                          f"(full state=0x{event.state:x})")

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

    def _poll_loop(self) -> None:
        """Sample the keyboard 20×/sec and fire on rising-edge of the
        hotkey combo. Bypasses XGrabKey entirely — used when another
        client (Cinnamon's Super-overview detector etc.) keeps eating
        the first press of our combo.

        Mod-state read from query_pointer().mask (X's only way to get
        current modifier state at runtime). Key state read from
        query_keymap() — a 32-byte bitmap with one bit per keycode.
        """
        import time as _time
        keycode = self._keycode
        mods = self._mods
        mod_mask = (X.ShiftMask | X.ControlMask
                    | X.Mod1Mask | X.Mod3Mask | X.Mod4Mask)
        byte_idx = keycode // 8
        bit_idx = keycode % 8
        bit_val = 1 << bit_idx
        prev_pressed = False
        # Seed prev_pressed from current state so a held key at startup
        # isn't treated as a rising edge.
        try:
            keymap = self._dpy.query_keymap()
            prev_pressed = bool(keymap[byte_idx] & bit_val)
        except Exception:
            pass
        while not self._stop.is_set():
            try:
                keymap = self._dpy.query_keymap()
                pressed = bool(keymap[byte_idx] & bit_val)
                if pressed and not prev_pressed:
                    # Rising edge — verify modifier state right now.
                    data = self._root.query_pointer()
                    effective = data.mask & mod_mask
                    if effective == mods:
                        print(f"[hotkey] '{self._hotkey_str}' fired "
                              "(polling) -- scheduling trigger")
                        GLib.idle_add(self._safe_trigger)
                prev_pressed = pressed
            except Exception as exc:  # noqa: BLE001
                print(f"[hotkey] poll error: {exc}")
            _time.sleep(0.05)

    def _safe_trigger(self) -> bool:
        try:
            self._on_trigger()
        except Exception as exc:  # noqa: BLE001
            print(f"[hotkey] trigger handler failed: {exc}")
        return False
