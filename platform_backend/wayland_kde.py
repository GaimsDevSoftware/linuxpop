"""KDE Plasma 6 / Wayland platform backend.

Proven on Fedora 44 KDE Plasma (KWin 6.6.5) during the Fase 0 spike:
  - clipboard / primary    -> wl-clipboard (wl-copy / wl-paste)
  - selection change watch  -> `wl-paste --primary --watch`
  - global pointer position -> KWin `workspace.cursorPos` over DBus
                               (see _kwin_cursor.py; subprocess per query)
  - keystroke injection     -> wtype
  - popup positioning        -> gtk-layer-shell (anchor top-left + margins)
  - global hotkey            -> KGlobalAccel over DBus (best-effort, see below)

The global hotkey path is the one piece that cannot be verified without a
physical key press; its Qt-keycode/flags encoding is a researched best-guess.
It is fully guarded and non-fatal: if registration fails the app runs normally
and auto-popup-on-selection (the headline feature) is unaffected.
"""
from __future__ import annotations

import os
import select as _select
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from gi.repository import GLib

from .base import PlatformBackend

_LAYER = None


def _kwin_subprocess_env() -> dict:
    """Environment for the `python3 -m platform_backend._kwin_*` helper
    subprocesses. Puts the package root on PYTHONPATH so the child can
    import platform_backend even when its cwd isn't the app directory -
    notably inside a Flatpak sandbox, where it otherwise dies with
    ModuleNotFoundError and the cursor/active-window query silently fails
    (popup then falls back to the top-left corner)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    return env

# Linux input-event codes (linux/input-event-codes.h) keyed by the
# xdotool-style key names the rest of the app emits. Used to build
# ydotool `key` CODE:STATE tokens. Letters/digits are lower-cased on
# lookup; the named keys cover every chord the app actually sends
# (Return, ctrl+Return, BackSpace, ctrl+a/x/v) plus common extras.
_YDOTOOL_KEYCODES = {
    **{d: 2 + i for i, d in enumerate("1234567890")},  # KEY_1=2 .. KEY_0=11
    "a": 30, "b": 48, "c": 46, "d": 32, "e": 18, "f": 33, "g": 34,
    "h": 35, "i": 23, "j": 36, "k": 37, "l": 38, "m": 50, "n": 49,
    "o": 24, "p": 25, "q": 16, "r": 19, "s": 31, "t": 20, "u": 22,
    "v": 47, "w": 17, "x": 45, "y": 21, "z": 44,
    "return": 28, "enter": 28, "kp_enter": 96,
    "backspace": 14, "tab": 15, "escape": 1, "esc": 1,
    "space": 57, "delete": 111, "minus": 12, "equal": 13,
    "home": 102, "end": 107, "prior": 104, "next": 109,
    "left": 105, "right": 106, "up": 103, "down": 108,
}


def _layer_shell():
    """Import GtkLayerShell lazily so importing this module doesn't hard-fail
    when the typelib is missing (we want a clear error only when we actually
    try to show the popup)."""
    global _LAYER
    if _LAYER is None:
        import gi
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
        _LAYER = GtkLayerShell
    return _LAYER


# ---------------------------------------------------------------------------
# Selection watcher: `wl-paste --primary --watch` notifies us; we then read
# the primary selection and the cursor position, mirroring the X11 watcher's
# event->read design.
# ---------------------------------------------------------------------------
class WaylandSelectionWatcher:
    def __init__(
        self,
        backend: "WaylandKdeBackend",
        on_selection: Callable[[str, int, int], None],
        debounce_ms: int = 150,
    ) -> None:
        self._backend = backend
        self._on_selection = on_selection
        self._debounce_s = max(0.0, debounce_ms / 1000.0)
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_text = ""

    def set_debounce_ms(self, ms: int) -> None:
        self._debounce_s = max(0.0, ms / 1000.0)

    def start(self) -> None:
        if not shutil.which("wl-paste"):
            print("[wayland] wl-paste missing - selection watch disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-wl-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run(self) -> None:
        # `--watch echo` pings (a bare newline) on every primary-selection
        # change. A single drag emits a BURST of these as the selection grows,
        # so we treat the pipe as an event stream and DEBOUNCE: only after the
        # pings go quiet for debounce_s do we read the selection + cursor and
        # fire once. Without this the popup re-reads the (still moving) cursor
        # on every ping and visibly chases the mouse for a second or two.
        try:
            self._proc = subprocess.Popen(
                ["wl-paste", "--primary", "--watch", "echo"],
                stdout=subprocess.PIPE, bufsize=0,
            )
        except OSError as exc:
            print(f"[wayland] could not start wl-paste --watch: {exc}")
            return
        assert self._proc.stdout is not None
        fd = self._proc.stdout.fileno()
        first = True
        while not self._stop.is_set():
            # Block until at least one ping arrives (0.5 s wake-ups so stop()
            # is honoured promptly).
            readable, _, _ = _select.select([fd], [], [], 0.5)
            if not readable:
                continue
            try:
                if not os.read(fd, 4096):
                    break  # wl-paste exited
            except OSError:
                break
            # Capture the cursor at the FIRST event of the burst, then re-
            # capture on every subsequent selection change. The last capture
            # therefore lands on the final change -- the drag release, right at
            # the end of the selected text. We deliberately do NOT query the
            # pointer again after the settle delay: by then the user has let go
            # and started moving the mouse toward the popup, and that drift was
            # exactly why the popup appeared away from the selection instead of
            # anchored over it. (On KWin Wayland there is no API for the text
            # selection's rectangle, so the release point is the best anchor we
            # have.)
            cap_x = cap_y = None
            try:
                cap_x, cap_y = self._backend.pointer_position()
            except Exception:  # noqa: BLE001
                pass
            # Drain the rest of the burst: keep consuming until the stream is
            # quiet for one debounce window.
            settle = max(0.2, self._debounce_s)
            while True:
                more, _, _ = _select.select([fd], [], [], settle)
                if not more:
                    break
                try:
                    if not os.read(fd, 4096):
                        break
                except OSError:
                    break
                try:
                    cap_x, cap_y = self._backend.pointer_position()
                except Exception:  # noqa: BLE001
                    pass
            if self._stop.is_set():
                break
            # The very first settled value is whatever was already selected
            # when LinuxPop started - don't pop up for it.
            if first:
                first = False
                continue
            text = self._backend.read_selection("primary")
            if not text or not text.strip():
                continue
            self._last_text = text
            if cap_x is None:
                try:
                    cap_x, cap_y = self._backend.pointer_position()
                except Exception as exc:  # noqa: BLE001
                    print(f"[wayland] pointer query failed: {exc}")
                    cap_x, cap_y = 0, 0
            try:
                self._on_selection(text, cap_x, cap_y)
            except Exception as exc:  # noqa: BLE001
                print(f"[wayland] selection callback error: {exc}")


# ---------------------------------------------------------------------------
# Global hotkey via KGlobalAccel (KDE's global-shortcut daemon, integrated
# into KWin on Wayland). Best-effort + non-fatal.
# ---------------------------------------------------------------------------
_QT_MODS = {
    "shift": 0x02000000,
    "ctrl": 0x04000000,
    "control": 0x04000000,
    "alt": 0x08000000,
    "super": 0x10000000,
    "win": 0x10000000,
    "meta": 0x10000000,
}
_QT_NAMED_KEYS = {
    "escape": 0x01000000, "tab": 0x01000001, "return": 0x01000004,
    "enter": 0x01000005, "space": 0x20, "backspace": 0x01000003,
    "delete": 0x01000007, "home": 0x01000010, "end": 0x01000011,
    "left": 0x01000012, "up": 0x01000013, "right": 0x01000014,
    "down": 0x01000015, "pageup": 0x01000016, "pagedown": 0x01000017,
    "insert": 0x01000006,
}


def _qt_keycode(hotkey_str: str) -> Optional[int]:
    """Encode 'super+shift+y' as a Qt QKeySequence int (modifiers OR'd with
    the Qt::Key value), which is what KGlobalAccel.setShortcut expects."""
    parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    for tok in parts[:-1]:
        if tok not in _QT_MODS:
            return None
        mods |= _QT_MODS[tok]
    key = parts[-1]
    if key in _QT_NAMED_KEYS:
        keycode = _QT_NAMED_KEYS[key]
    elif len(key) == 1:
        keycode = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit():
        keycode = 0x01000030 + (int(key[1:]) - 1)  # Qt::Key_F1 = 0x01000030
    else:
        return None
    return mods | keycode


class WaylandKdeHotkey:
    """Registers a global shortcut with KGlobalAccel over DBus.

    NOTE: the Qt-keycode encoding and setShortcut flags are a researched
    best-guess that needs a real key press to confirm. All failures are
    swallowed and logged; the app keeps running either way.
    """

    _counter = 0

    def __init__(self, hotkey_str: str, on_trigger: Callable[[], None]) -> None:
        self._hotkey_str = hotkey_str
        self._on_trigger = on_trigger
        WaylandKdeHotkey._counter += 1
        self._action = f"linuxpop-action-{WaylandKdeHotkey._counter}"
        self._component = "linuxpop"
        self._registered = False

    def start(self) -> None:
        try:
            self._register()
        except Exception as exc:  # noqa: BLE001
            print(f"[wayland] KGlobalAccel registration failed for "
                  f"{self._hotkey_str!r}: {exc} (hotkey inactive; auto-popup "
                  f"on selection still works)")

    def _register(self) -> None:
        import dbus
        keycode = _qt_keycode(self._hotkey_str)
        if keycode is None:
            print(f"[wayland] cannot encode hotkey {self._hotkey_str!r}")
            return
        bus = dbus.SessionBus()
        kga = dbus.Interface(
            bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"),
            "org.kde.KGlobalAccel",
        )
        action_id = dbus.Array(
            [self._component, self._action, "LinuxPop", self._hotkey_str],
            signature="s",
        )
        kga.doRegister(action_id)
        # Register with setShortcut(actionId: as, keys: ai, flags: u). The keys
        # argument is a PLAIN array of Qt key ints (each modifiers|key); the
        # call returns the keycodes actually assigned. Verified against the live
        # KF6 interface: it returns the requested keycode, i.e. the grab IS
        # activated - so this single call is sufficient on Plasma 6.
        #
        # Do NOT use setShortcutKeys here. Its keys argument is QList<QKeySequence>
        # (wire type `a(ai)`), which KGlobalAccel/KWin demarshals with a custom
        # QKeySequence operator that ABORTS the whole process on the slightest
        # framing mismatch. Hand-marshalling that nested type from dbus-python
        # made kwin_wayland SIGABRT inside libdbus ("type invalid 0 not a basic
        # type") on every LinuxPop startup, taking the entire Plasma session down
        # with it. Plain `ai` is type-trivial and cannot trigger that abort.
        # flags = SetPresent(2) | NoAutoloading(4) = 6. SetPresent is ESSENTIAL
        # on Plasma 6 Wayland: without it the key is recorded but the component
        # stays INACTIVE (isActive()==False), so KWin never installs the physical
        # key grab and presses do nothing (only DBus invokeShortcut fires). With
        # SetPresent the component goes active and KWin grabs the key. Verified
        # live: flag 4 -> isActive False, key not grabbed; flag 6 -> isActive
        # True, key grabbed. NoAutoloading keeps the keys we pass (not stored config).
        flags = dbus.UInt32(6)
        assigned = kga.setShortcut(
            action_id, dbus.Array([dbus.Int32(keycode)], signature="i"), flags)
        if not list(assigned):
            print(f"[wayland] KGlobalAccel did not assign {self._hotkey_str!r} "
                  f"- it is likely already bound to another action (e.g. a KDE "
                  f"system shortcut); the hotkey will be inactive")
        comp_path = kga.getComponent(self._component)
        # Subscribe broadly (interface + signal only). Pinning bus_name/path
        # can silently miss the signal if KWin emits it from a different sender
        # name or object path than getComponent reports; the handler filters by
        # our action id anyway.
        bus.add_signal_receiver(
            self._on_pressed,
            signal_name="globalShortcutPressed",
            dbus_interface="org.kde.kglobalaccel.Component",
        )
        self._registered = True
        print(f"[wayland] registered global shortcut {self._hotkey_str!r} "
              f"via KGlobalAccel (action={self._action}) - press to verify")

    def _on_pressed(self, *args) -> None:
        # The Component signal is globalShortcutPressed(componentUnique,
        # shortcutUnique, timestamp) - so our action id is args[1], NOT args[0]
        # (that's the component). Match it anywhere in the args to be robust,
        # and log every delivery so we can confirm KWin is routing the press.
        names = {str(a) for a in args}
        print(f"[wayland] globalShortcutPressed {sorted(names)}", flush=True)
        if self._action not in names:
            return
        GLib.idle_add(self._safe_trigger)

    def _safe_trigger(self) -> bool:
        try:
            self._on_trigger()
        except Exception as exc:  # noqa: BLE001
            print(f"[wayland] hotkey trigger failed: {exc}")
        return False

    def stop(self, wait: bool = True, timeout: float = 1.5) -> None:
        if not self._registered:
            return
        try:
            import dbus
            bus = dbus.SessionBus()
            kga = dbus.Interface(
                bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"),
                "org.kde.KGlobalAccel",
            )
            action_id = dbus.Array(
                [self._component, self._action, "LinuxPop", self._hotkey_str],
                signature="s",
            )
            kga.unRegister(action_id)
        except Exception:
            pass
        self._registered = False


def _detect_kbd_rmlvo() -> tuple:
    """(model, layout, variant, options) for the active keyboard so raw
    /dev/input keycodes can be interpreted through the user's REAL xkb
    config. Crucially this includes the xkb *options* (e.g. a Ctrl/Alt swap
    via ctrl:swap_lalt_lctl) - localectl omits those, so KDE keeps them in
    kxkbrc. evdev events are pre-xkb, so without the keymap a remap is
    invisible to us."""
    model, layout, variant, options = "pc105", "us", "", ""
    try:
        out = subprocess.run(["localectl", "status"], capture_output=True,
                             text=True, timeout=2.0).stdout
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("X11 Layout:"):
                layout = s.split(":", 1)[1].strip() or layout
            elif s.startswith("X11 Variant:"):
                variant = s.split(":", 1)[1].strip()
            elif s.startswith("X11 Model:"):
                model = s.split(":", 1)[1].strip() or model
            elif s.startswith("X11 Options:"):
                options = s.split(":", 1)[1].strip()
    except (OSError, subprocess.SubprocessError):
        pass
    # KDE keeps the live keyboard options (Ctrl/Alt swap, caps remaps, …) in
    # kxkbrc and is authoritative on Wayland - prefer it over localectl.
    try:
        path = os.path.expanduser("~/.config/kxkbrc")
        in_layout = False
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("["):
                    in_layout = (line == "[Layout]")
                elif in_layout and line.startswith("Options="):
                    options = line.split("=", 1)[1].strip()
    except OSError:
        pass
    return (model, layout.split(",")[0], variant.split(",")[0], options)


class _XkbModifierState:
    """libxkbcommon ctypes wrapper that tracks LOGICAL modifier state from
    raw evdev keycodes, honouring the user's layout + options. This is what
    lets a Ctrl/Alt swap (kxkbrc Options=ctrl:swap_lalt_lctl) be respected:
    we ask xkb 'is Control effectively active?' instead of hard-coding which
    physical keycode is Ctrl."""

    _MOD_NAME = {
        "ctrl": b"Control", "shift": b"Shift", "alt": b"Mod1", "super": b"Mod4",
    }
    _XKB_STATE_MODS_EFFECTIVE = 1 << 3

    def __init__(self) -> None:
        import ctypes as C
        self._ok = False
        try:
            xkb = C.CDLL("libxkbcommon.so.0")
        except OSError:
            return
        self._xkb = xkb

        class _RN(C.Structure):
            _fields_ = [("rules", C.c_char_p), ("model", C.c_char_p),
                        ("layout", C.c_char_p), ("variant", C.c_char_p),
                        ("options", C.c_char_p)]

        xkb.xkb_context_new.restype = C.c_void_p
        xkb.xkb_context_new.argtypes = [C.c_int]
        xkb.xkb_keymap_new_from_names.restype = C.c_void_p
        xkb.xkb_keymap_new_from_names.argtypes = [
            C.c_void_p, C.POINTER(_RN), C.c_int]
        xkb.xkb_state_new.restype = C.c_void_p
        xkb.xkb_state_new.argtypes = [C.c_void_p]
        xkb.xkb_state_update_key.restype = C.c_int
        xkb.xkb_state_update_key.argtypes = [C.c_void_p, C.c_uint32, C.c_int]
        xkb.xkb_state_mod_name_is_active.restype = C.c_int
        xkb.xkb_state_mod_name_is_active.argtypes = [
            C.c_void_p, C.c_char_p, C.c_int]
        ctx = xkb.xkb_context_new(0)
        if not ctx:
            return
        model, layout, variant, options = _detect_kbd_rmlvo()

        def _mk(opts):
            rn = _RN(b"evdev", (model or "pc105").encode(),
                     (layout or "us").encode(),
                     variant.encode() if variant else None,
                     opts.encode() if opts else None)
            return xkb.xkb_keymap_new_from_names(ctx, C.byref(rn), 0)

        km = _mk(options) or _mk("")
        if not km:
            return
        self._state = xkb.xkb_state_new(km)
        self._ok = bool(self._state)
        print(f"[dblclick] xkb state ok={self._ok} layout={layout} "
              f"variant={variant or '-'} options={options or '-'}")

    def update(self, evdev_code: int, down: bool) -> None:
        if self._ok:
            self._xkb.xkb_state_update_key(
                self._state, evdev_code + 8, 1 if down else 0)

    def is_active(self, mod: str):
        """True/False if the named modifier is effectively held, or None when
        xkb is unavailable (caller falls back to raw keycodes)."""
        if not self._ok:
            return None
        name = self._MOD_NAME.get(mod)
        if not name:
            return None
        return self._xkb.xkb_state_mod_name_is_active(
            self._state, name, self._XKB_STATE_MODS_EFFECTIVE) == 1


class WaylandDoubleClickWatcher:
    """evdev-based modifier+double-click watcher for native Wayland.

    The X11 path (XRecord) only sees XWayland windows on KWin, so the
    double-click-modifier feature was dead for native Wayland apps. This
    reads mouse buttons AND modifier keys straight from /dev/input - the
    same mechanism the snippet-trigger and outside-click watchers already
    use, so it works for every toolkit. Needs the user in the 'input'
    group; silently inert otherwise.

    Mirrors mouse_watcher.DoubleClickWatcher's semantics (300 ms window,
    8 px tolerance, gated on the configured modifier). evdev button events
    carry no coordinates, so the cursor position comes from the KWin
    pointer query - fetched only while the gating modifier is held, so the
    DBus round-trip happens for our gesture, not on every click.
    """

    _MOD_CODES = {
        "ctrl":  {29, 97},    # KEY_LEFTCTRL / KEY_RIGHTCTRL
        "shift": {42, 54},    # KEY_LEFTSHIFT / KEY_RIGHTSHIFT
        "alt":   {56, 100},   # KEY_LEFTALT / KEY_RIGHTALT
        "super": {125, 126},  # KEY_LEFTMETA / KEY_RIGHTMETA
    }
    _ALL_MODS = {29, 97, 42, 54, 56, 100, 125, 126}
    _BTN_LEFT = 0x110
    _DOUBLE_CLICK_MS = 300
    _POSITION_TOLERANCE_PX = 8

    def __init__(self, backend, on_double_click) -> None:
        self._backend = backend
        self._cb = on_double_click
        self._stop = threading.Event()
        self._thread = None
        self._held = set()      # raw modifier keycodes down (xkb fallback)
        self._xkb = None        # layout-aware modifier state (built in _run)
        self._last_ms = 0
        self._last_xy = (0, 0)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-dblclick-wl")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Join briefly so the /dev/input fds are closed (in _run's finally)
        # before the caller drops this watcher and possibly starts a fresh
        # one on the next toggle. The read loop wakes at most every ~0.3 s,
        # so this returns quickly; bounded timeout keeps a wedged thread
        # from blocking the settings toggle indefinitely.
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=0.6)
        self._thread = None

    def _required_mod_name(self) -> str:
        """The configured modifier name, read fresh each click so changing
        the setting takes effect without a restart."""
        try:
            from settings import get_settings
            name = (get_settings().get("double_click_modifier") or "ctrl").lower()
        except Exception:
            name = "ctrl"
        return name if name in self._MOD_CODES else "ctrl"

    def _required_codes(self) -> set:
        """Raw-keycode fallback set, used only when xkb is unavailable."""
        return self._MOD_CODES.get(self._required_mod_name(), self._MOD_CODES["ctrl"])

    def _open_devices(self) -> list:
        import glob
        # by-path names are the physical devices and exclude ydotool's
        # virtual uinput node (no bus path) - so injected keys never
        # pollute the modifier state.
        paths = set()
        for pat in ("*-event-kbd", "*-event-mouse"):
            for p in glob.glob(f"/dev/input/by-path/{pat}"):
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
        import struct
        fds = self._open_devices()
        if not fds:
            print("[dblclick] no readable input devices - add yourself to the "
                  "'input' group? Wayland double-click off")
            return
        # Layout-aware modifier state so a Ctrl/Alt swap is respected. If
        # libxkbcommon is missing we fall back to raw keycodes in _on_left_press.
        self._xkb = _XkbModifierState()
        print(f"[dblclick] evdev watcher on {len(fds)} device(s) (Wayland)")
        EV_KEY = 0x01
        fmt = "llHHi"
        size = struct.calcsize(fmt)
        try:
            while not self._stop.is_set():
                r, _, _ = _select.select(fds, [], [], 0.3)
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
                        if code == self._BTN_LEFT:
                            if val == 1:
                                self._on_left_press()
                            continue
                        # Keyboard key: feed xkb (press=1/release=0; ignore
                        # autorepeat=2) and keep the raw-code fallback set.
                        if val in (0, 1):
                            self._xkb.update(code, val == 1)
                        if code in self._ALL_MODS:
                            if val == 1:
                                self._held.add(code)
                            elif val == 0:
                                self._held.discard(code)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _on_left_press(self) -> None:
        # Only act on our gesture: the configured modifier must be held.
        # This also keeps the KWin pointer query off the plain-click path.
        name = self._required_mod_name()
        active = self._xkb.is_active(name) if self._xkb else None
        if active is None:
            # xkb unavailable - fall back to raw physical keycodes (ignores
            # any Ctrl/Alt swap, but better than nothing).
            active = bool(self._held & self._required_codes())
        if not active:
            return
        x, y = self._backend.pointer_position()
        now_ms = int(time.monotonic() * 1000)
        elapsed = now_ms - self._last_ms
        dx = abs(x - self._last_xy[0])
        dy = abs(y - self._last_xy[1])
        if (elapsed < self._DOUBLE_CLICK_MS
                and dx < self._POSITION_TOLERANCE_PX
                and dy < self._POSITION_TOLERANCE_PX):
            self._last_ms = 0
            self._last_xy = (0, 0)
            # Tiny settle so the app sees the click first, then we show
            # the menu (same as the X11 watcher).
            GLib.timeout_add(20, self._fire, x, y)
            return
        self._last_ms = now_ms
        self._last_xy = (x, y)

    def _fire(self, x: int, y: int) -> bool:
        try:
            self._cb(x, y)
        except Exception as exc:  # noqa: BLE001
            print(f"[dblclick] callback failed: {exc}")
        return False  # one-shot


class WaylandKdeBackend(PlatformBackend):
    name = "wayland_kde"
    popup_uses_xlib = False
    # KWin's workspace.cursorPos is in logical/compositor pixels, the same
    # space as GTK's monitor geometry - so the popup must NOT divide by scale.
    pointer_is_logical = True

    def __init__(self) -> None:
        # KGlobalAccel signal delivery needs dbus-python bound to the GLib
        # main loop. Set it as the default before any SessionBus is created
        # so the hotkey's add_signal_receiver works.
        try:
            from dbus.mainloop.glib import DBusGMainLoop
            DBusGMainLoop(set_as_default=True)
        except Exception:
            pass

    # ---- session ---------------------------------------------------------
    def check_session(self) -> None:
        if not os.environ.get("WAYLAND_DISPLAY"):
            print("[wayland] WAYLAND_DISPLAY not set - wrong backend.",
                  file=sys.stderr)
            sys.exit(2)
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
        if "KDE" not in desktop:
            print(f"[wayland] WARNING: XDG_CURRENT_DESKTOP={desktop!r} is not "
                  "KDE. Cursor positioning relies on KWin; popup placement may "
                  "not work on this compositor.", file=sys.stderr)

    # ---- selection / clipboard ------------------------------------------
    def read_selection(self, source: str) -> str:
        args = ["wl-paste", "--no-newline"]
        if source.lower() == "primary":
            args.append("--primary")
        try:
            out = subprocess.run(args, capture_output=True, timeout=2.0)
            if out.returncode != 0:
                return ""  # empty selection -> wl-paste exits non-zero
            return out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def set_clipboard(self, text: str) -> None:
        if not shutil.which("wl-copy"):
            print("[wayland] wl-copy missing - cannot set clipboard")
            return
        try:
            subprocess.run(["wl-copy"], input=text.encode("utf-8"),
                           check=False, timeout=2.0)
        except subprocess.SubprocessError as exc:
            print(f"[wayland] wl-copy failed: {exc}")

    # ---- pointer ---------------------------------------------------------
    def pointer_position(self) -> tuple[int, int]:
        try:
            out = subprocess.run(
                [sys.executable, "-m", "platform_backend._kwin_cursor"],
                capture_output=True, text=True, timeout=3.0,
                env=_kwin_subprocess_env(),
            )
            parts = out.stdout.strip().split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
            # Empty/short stdout means the KWin script round-trip produced
            # nothing (timeout, DBus-name clash, KWin busy). This is the
            # silent path that lands the popup at the top-left corner.
            print(f"[wayland] cursor query gave no coords "
                  f"(rc={out.returncode}, stdout={out.stdout!r}, "
                  f"stderr={out.stderr.strip()!r}) -> falling back to (0,0)")
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            print(f"[wayland] cursor query failed: {exc}")
        return 0, 0

    def can_paste(self) -> bool:
        # ydotool (KWin) / wtype (wlroots) are the only Wayland injectors and
        # neither is bundled in the Flatpak, so paste-back no-ops there. Let
        # callers detect this and fall back to "copied to clipboard".
        return bool(shutil.which("ydotool") or shutil.which("wtype"))

    # ---- keystroke injection --------------------------------------------
    #
    # wtype is DEAD on KWin: it drives zwp_virtual_keyboard_v1, which KWin
    # does not implement, so it silently no-ops on Plasma. The validated
    # path on KWin is ydotool, which writes to the kernel uinput device and
    # bypasses Wayland's synthetic-input ban entirely (needs the ydotoold
    # user daemon + /dev/uinput + user in `input` group). The future
    # sandbox-clean route is libei via the RemoteDesktop portal (`eitype`),
    # left as a fallback hook for when that CLI is present.
    #
    # Order: ydotool (works on KWin) -> wtype (works on wlroots/Sway, dead on
    # KWin but harmless to try) so this same backend still injects on a
    # non-KDE Wayland compositor.

    def _ydotool_env(self) -> dict:
        """Env for ydotool with YDOTOOL_SOCKET pointed at the running
        daemon. ydotoold here listens on $XDG_RUNTIME_DIR/.ydotool_socket;
        set it explicitly so we don't depend on ydotool's compiled default."""
        env = os.environ.copy()
        if not env.get("YDOTOOL_SOCKET"):
            runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
            sock = os.path.join(runtime, ".ydotool_socket")
            if os.path.exists(sock):
                env["YDOTOOL_SOCKET"] = sock
        return env

    def _ydotool_key_events(self, combo: str) -> Optional[list[str]]:
        """Translate an xdotool-style chord ('ctrl+v', 'Return') into the
        CODE:STATE token list ydotool `key` expects. Modifiers are pressed
        in order then released in reverse, wrapping the final key."""
        parts = [p.strip() for p in combo.split("+") if p.strip()]
        if not parts:
            return None
        mod_codes = {"ctrl": 29, "control": 29, "shift": 42, "alt": 56,
                     "super": 125, "win": 125, "meta": 125}
        mods = []
        for m in parts[:-1]:
            code = mod_codes.get(m.lower())
            if code is None:
                print(f"[wayland] ydotool: unknown modifier {m!r} in {combo!r}")
                return None
            mods.append(code)
        key = parts[-1]
        key_code = _YDOTOOL_KEYCODES.get(key) or _YDOTOOL_KEYCODES.get(key.lower())
        if key_code is None:
            print(f"[wayland] ydotool: unknown key {key!r} in {combo!r}")
            return None
        events = [f"{c}:1" for c in mods]
        events += [f"{key_code}:1", f"{key_code}:0"]
        events += [f"{c}:0" for c in reversed(mods)]
        return events

    def _wtype_args(self, combo: str) -> Optional[list[str]]:
        parts = [p.strip() for p in combo.split("+") if p.strip()]
        if not parts:
            return None
        mod_map = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt",
                   "shift": "shift", "super": "logo", "win": "logo",
                   "meta": "logo"}
        mods = [mod_map[m.lower()] for m in parts[:-1] if m.lower() in mod_map]
        key = parts[-1]
        args = []
        for m in mods:
            args += ["-M", m]
        args += ["-k", key]
        for m in reversed(mods):
            args += ["-m", m]
        return args

    def send_key(self, combo: str) -> None:
        if shutil.which("ydotool"):
            events = self._ydotool_key_events(combo)
            if events is not None:
                try:
                    subprocess.run(["ydotool", "key", *events],
                                   env=self._ydotool_env(), check=False)
                    return
                except OSError as exc:
                    print(f"[wayland] ydotool key failed: {exc}")
        if shutil.which("wtype"):
            args = self._wtype_args(combo)
            if args is not None:
                subprocess.run(["wtype"] + args, check=False)
                return
        print(f"[wayland] no working key-injection tool for {combo!r} "
              "(install ydotool + ydotoold on KWin)")

    def type_text(self, text: str) -> None:
        if shutil.which("ydotool"):
            try:
                # `--` terminates ydotool's own option parsing so text that
                # starts with '-' is typed literally rather than parsed.
                subprocess.run(["ydotool", "type", "--", text],
                               env=self._ydotool_env(), check=False)
                return
            except OSError as exc:
                print(f"[wayland] ydotool type failed: {exc}")
        if shutil.which("wtype"):
            subprocess.run(["wtype", text], check=False)
            return
        print("[wayland] no working text-injection tool (install ydotool)")

    # ---- active window ---------------------------------------------------
    def active_window_haystacks(self) -> list[str]:
        # Only called when the user actually has blocklist patterns (caller
        # short-circuits on empty), so the KWin round-trip never costs the
        # default no-blocklist path anything.
        try:
            out = subprocess.run(
                [sys.executable, "-m", "platform_backend._kwin_active"],
                capture_output=True, text=True, timeout=3.0,
                env=_kwin_subprocess_env(),
            )
            cls, _, caption = out.stdout.strip().partition("\t")
            return [s.lower() for s in (cls, caption) if s]
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[wayland] active-window query failed: {exc}")
            return []

    # ---- opening URLs ----------------------------------------------------
    def open_url(self, url: str) -> None:
        try:
            subprocess.Popen(["xdg-open", url], start_new_session=True)
        except FileNotFoundError:
            print("[wayland] xdg-open not available")
            return
        # Wayland focus-stealing prevention leaves the browser in the
        # background when launched from our (unfocused) popup. KWin scripts are
        # privileged and can force-raise it - do that shortly after.
        threading.Thread(target=self._raise_browser, daemon=True).start()

    def _browser_keyword(self) -> str:
        kw = getattr(self, "_browser_kw", None)
        if kw is not None:
            return kw
        kw = "firefox"
        try:
            out = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=2.0,
            )
            ident = out.stdout.strip().lower()
            if ident.endswith(".desktop"):
                ident = ident[:-len(".desktop")]
            if ident:
                # org.mozilla.firefox -> firefox; google-chrome -> google-chrome
                kw = ident.split(".")[-1] if "." in ident else ident
        except (OSError, subprocess.SubprocessError):
            pass
        self._browser_kw = kw
        return kw

    def _raise_browser(self) -> None:
        time.sleep(0.5)  # let the tab register before we raise the window
        kw = self._browser_keyword().replace('"', "").replace("\\", "")
        js = (
            'var s="%s";'
            'var L=(workspace.windowList?workspace.windowList():workspace.clientList());'
            'for(var i=0;i<L.length;i++){var w=L[i];try{'
            'if(String(w.resourceClass).toLowerCase().indexOf(s)>=0){'
            'w.minimized=false;workspace.activeWindow=w;break;}}catch(e){}}'
        ) % kw
        import tempfile
        path = None
        try:
            import dbus
            bus = dbus.SessionBus()
            fd, path = tempfile.mkstemp(suffix=".js", prefix="linuxpop-raise-")
            os.write(fd, js.encode("utf-8"))
            os.close(fd)
            scripting = dbus.Interface(
                bus.get_object("org.kde.KWin", "/Scripting"),
                "org.kde.kwin.Scripting",
            )
            sid = int(scripting.loadScript(path))
            dbus.Interface(
                bus.get_object("org.kde.KWin", f"/Scripting/Script{sid}"),
                "org.kde.kwin.Script",
            ).run()
            time.sleep(0.3)
            try:
                scripting.unloadScript(path)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            print(f"[wayland] raise-browser failed: {exc}")
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass

    # ---- component factories --------------------------------------------
    def make_selection_watcher(self, on_selection, debounce_ms):
        return WaylandSelectionWatcher(self, on_selection, debounce_ms=debounce_ms)

    def make_hotkey(self, hotkey_str, on_trigger, use_polling=False):
        return WaylandKdeHotkey(hotkey_str, on_trigger)

    def make_double_click_watcher(self, on_double_click):
        # XRecord (the old path) only sees XWayland windows on KWin, so the
        # gesture was dead for native Wayland apps. Read mouse buttons +
        # modifier keys from /dev/input instead - kernel-level, so it covers
        # XWayland AND native Wayland windows. Needs the user in 'input';
        # the watcher logs and stays inert otherwise.
        return WaylandDoubleClickWatcher(self, on_double_click)

    # ---- popup positioning ----------------------------------------------
    def init_popup_window(self, win) -> None:
        L = _layer_shell()
        L.init_for_window(win)
        L.set_layer(win, L.Layer.OVERLAY)
        # KeyboardMode.NONE: the popup must NOT take keyboard focus. With
        # ON_DEMAND the layer surface grabs focus on map then immediately gets
        # a focus-out, and PopupWindow._on_focus_out hides it before it's even
        # visible. NONE keeps it on-screen; dismissal is via the leave/initial
        # grace timers (Esc-to-dismiss is a documented Wayland gap for now).
        try:
            L.set_keyboard_mode(win, L.KeyboardMode.NONE)
        except Exception:
            pass
        L.set_anchor(win, L.Edge.LEFT, True)
        L.set_anchor(win, L.Edge.TOP, True)

    def move_popup_window(self, win, x: int, y: int) -> None:
        L = _layer_shell()
        L.set_anchor(win, L.Edge.LEFT, True)
        L.set_anchor(win, L.Edge.TOP, True)
        L.set_margin(win, L.Edge.LEFT, int(x))
        L.set_margin(win, L.Edge.TOP, int(y))
