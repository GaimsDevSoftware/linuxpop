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
            try:
                x, y = self._backend.pointer_position()
            except Exception as exc:  # noqa: BLE001
                print(f"[wayland] pointer query failed: {exc}")
                x, y = 0, 0
            try:
                self._on_selection(text, x, y)
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
            )
            parts = out.stdout.strip().split()
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            print(f"[wayland] cursor query failed: {exc}")
        return 0, 0

    # ---- keystroke injection --------------------------------------------
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
        if not shutil.which("wtype"):
            print("[wayland] wtype missing - cannot send key")
            return
        args = self._wtype_args(combo)
        if args is None:
            return
        subprocess.run(["wtype"] + args, check=False)

    def type_text(self, text: str) -> None:
        if not shutil.which("wtype"):
            print("[wayland] wtype missing - cannot type text")
            return
        # `--` is not understood by wtype; guard against a leading dash by
        # typing via stdin is unsupported, so pass directly (paste-replace
        # text rarely starts with '-').
        subprocess.run(["wtype", text], check=False)

    # ---- active window ---------------------------------------------------
    def active_window_haystacks(self) -> list[str]:
        # Only called when the user actually has blocklist patterns (caller
        # short-circuits on empty), so the KWin round-trip never costs the
        # default no-blocklist path anything.
        try:
            out = subprocess.run(
                [sys.executable, "-m", "platform_backend._kwin_active"],
                capture_output=True, text=True, timeout=3.0,
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
        # There is no native-Wayland way to watch global pointer events, but
        # XRecord still works over XWayland (the same path the snippet-trigger
        # watcher uses here), so the modifier+double-click gesture works inside
        # XWayland windows. For native Wayland windows it simply never fires -
        # the popup hotkey covers those. Best-effort and non-fatal.
        if not os.environ.get("DISPLAY"):
            return None
        try:
            from mouse_watcher import DoubleClickWatcher
            return DoubleClickWatcher(on_double_click)
        except Exception as exc:  # noqa: BLE001
            print(f"[wayland] double-click watcher unavailable: {exc}")
            return None

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
