"""XWayland + GNOME (Mutter) platform backend.

GNOME's Mutter implements neither wlr-layer-shell (so a Wayland client cannot
place a surface at absolute coordinates) nor any external cursor-position API
(there is no equivalent of KWin's workspace.cursorPos). Both are exactly what
the KDE backend relies on for popup placement. So on GNOME we take a hybrid
route:

  - positioning + pointer  -> inherited from X11Backend, with the app running
    under XWayland (main.py sets GDK_BACKEND=x11 when this backend is chosen).
    Gtk.Window.move() and Xlib XQueryPointer both work for an XWayland toplevel
    and report global coordinates - the two hard problems, solved for free.
  - selection watch / clipboard / key injection -> Wayland-native tools
    (wl-clipboard, ydotool), reused verbatim from the KDE backend, because the
    X11 tools (xclip / XFixes / xdotool) only see XWayland apps, not native
    Wayland ones.
  - global hotkey -> evdev (/dev/input), read at kernel level so it fires
    regardless of which app (native Wayland or XWayland) has focus; an X11
    grab via XWayland would miss keys while a native Wayland window is focused.
  - active window -> AT-SPI (GNOME's native a11y), merged with the XWayland
    WM_CLASS so legacy X11 apps are still matched.

Net result: positioning/pointer come straight from the proven X11 path; only
the selection / clipboard / injection / hotkey I/O is swapped for Wayland-
native equivalents that already exist and are tested on KDE.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
from typing import Callable, Optional

from .x11 import X11Backend


class _PollingSelectionWatcher:
    """Primary-selection watcher for compositors WITHOUT wlr-data-control.

    GNOME/Mutter does not implement the data-control protocol, so
    `wl-paste --primary --watch` exits immediately ("Watch mode requires a
    compositor that supports the data-control protocol") and the event-driven
    WaylandSelectionWatcher never fires. One-shot `wl-paste --primary` reads DO
    work on Mutter, so we poll: read the primary selection on a short interval
    and fire on change. Sees both native Wayland and XWayland apps (anything
    that owns the primary selection). Mirrors WaylandSelectionWatcher's
    interface so the backend can swap it in transparently.
    """

    def __init__(
        self,
        backend,
        on_selection: Callable[[str, int, int], None],
        debounce_ms: int = 150,
        poll_ms: int = 250,
    ) -> None:
        self._backend = backend
        self._on_selection = on_selection
        self._debounce_s = max(0.0, debounce_ms / 1000.0)
        self._poll_s = max(0.05, poll_ms / 1000.0)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._last_text: Optional[str] = None

    def set_debounce_ms(self, ms: int) -> None:
        self._debounce_s = max(0.0, ms / 1000.0)

    def start(self) -> None:
        if not shutil.which("wl-paste"):
            print("[xwayland_gnome] wl-paste missing - selection watch disabled")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-gnome-selpoll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=1.0)

    def _read_primary(self) -> str:
        try:
            return self._backend.read_selection("primary") or ""
        except Exception:  # noqa: BLE001
            return ""

    def _run(self) -> None:
        # Baseline: whatever is already selected when LinuxPop starts must not
        # pop up. Establish it before the loop so the first real change fires.
        self._last_text = self._read_primary()
        while not self._stop.wait(self._poll_s):
            text = self._read_primary()
            if text == self._last_text:
                continue
            # Changed. Let a drag settle, then re-read so we anchor on the
            # final selection instead of an intermediate one mid-drag.
            if self._debounce_s and self._stop.wait(self._debounce_s):
                break
            text = self._read_primary()
            self._last_text = text
            if not text or not text.strip():
                continue
            try:
                x, y = self._backend.pointer_position()
            except Exception:  # noqa: BLE001
                x, y = 0, 0
            try:
                self._on_selection(text, x, y)
            except Exception as exc:  # noqa: BLE001
                print(f"[xwayland_gnome] selection callback error: {exc}")


class XWaylandGnomeBackend(X11Backend):
    name = "xwayland_gnome"
    # Running under XWayland: the popup is an X11 toplevel, so the Xlib-based
    # pointer/Esc polling and Gtk.Window.move() inherited from X11Backend apply.
    popup_uses_xlib = True
    # XQueryPointer returns physical root pixels (as on native X11), so the
    # popup divides by the monitor scale - inherit X11Backend's False.
    pointer_is_logical = False

    def __init__(self) -> None:
        super().__init__()
        # Lazily-created KDE backend instance reused purely as a Wayland I/O
        # helper for clipboard + key injection (its wl-clipboard / ydotool code
        # is compositor-agnostic). Never used for pointer or positioning.
        self._wl = None

    def _wl_io(self):
        if self._wl is None:
            from .wayland_kde import WaylandKdeBackend
            self._wl = WaylandKdeBackend()
        return self._wl

    # ---- session ---------------------------------------------------------
    def check_session(self) -> None:
        if not os.environ.get("WAYLAND_DISPLAY"):
            print("[gnome] WAYLAND_DISPLAY not set - wrong backend.",
                  file=sys.stderr)
            sys.exit(2)
        if (os.environ.get("GDK_BACKEND") or "").lower() != "x11":
            print("[gnome] WARNING: GDK_BACKEND is not 'x11'. Popup positioning "
                  "needs the app to run under XWayland; main.py normally sets "
                  "this automatically before GTK starts.", file=sys.stderr)
        if not os.environ.get("DISPLAY"):
            print("[gnome] WARNING: DISPLAY unset - XWayland may be "
                  "unavailable; pointer queries and popup placement will fail.",
                  file=sys.stderr)
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
        if "GNOME" not in desktop:
            print(f"[gnome] note: XDG_CURRENT_DESKTOP={desktop!r}; the "
                  "xwayland_gnome backend targets GNOME but should also work on "
                  "other non-KDE Wayland compositors via XWayland.",
                  file=sys.stderr)
        if not shutil.which("wl-paste"):
            print("[gnome] WARNING: wl-clipboard (wl-paste/wl-copy) missing - "
                  "selection auto-popup and clipboard actions need it. "
                  "Install: sudo dnf install wl-clipboard", file=sys.stderr)
        if not (shutil.which("ydotool") or shutil.which("wtype")):
            print("[gnome] WARNING: no Wayland key injector (ydotool/wtype) - "
                  "Cut/Paste/Backspace actions will not work. "
                  "Install ydotool and enable the ydotoold daemon.",
                  file=sys.stderr)

    # ---- selection / clipboard (Wayland-native) --------------------------
    def read_selection(self, source: str) -> str:
        return self._wl_io().read_selection(source)

    def set_clipboard(self, text: str) -> None:
        self._wl_io().set_clipboard(text)

    # ---- keystroke injection (ydotool/wtype via the KDE helper) ----------
    def send_key(self, combo: str) -> None:
        self._wl_io().send_key(combo)

    def type_text(self, text: str) -> None:
        self._wl_io().type_text(text)

    def can_paste(self) -> bool:
        return self._wl_io().can_paste()

    def paste(self) -> None:
        self._wl_io().paste()

    # ---- active window (AT-SPI + XWayland WM_CLASS) ----------------------
    def active_window_haystacks(self) -> list[str]:
        hays: list[str] = []
        try:
            from editable_detect import active_window_atspi_haystacks
            hays.extend(active_window_atspi_haystacks())
        except Exception:
            pass
        # XWayland WM_CLASS/title still helps for legacy X11 apps.
        try:
            hays.extend(super().active_window_haystacks())
        except Exception:
            pass
        seen: set[str] = set()
        out: list[str] = []
        for h in hays:
            if h and h not in seen:
                seen.add(h)
                out.append(h)
        return out

    # ---- component factories --------------------------------------------
    def make_selection_watcher(self, on_selection, debounce_ms):
        # Mutter has no wlr-data-control, so `wl-paste --watch` is dead here -
        # use the polling watcher (one-shot reads work) instead of the KDE
        # event-driven one. Sees native Wayland AND XWayland selections; calls
        # back into this backend for read_selection() and pointer_position().
        return _PollingSelectionWatcher(self, on_selection,
                                        debounce_ms=debounce_ms)

    def make_hotkey(self, hotkey_str, on_trigger, use_polling=False):
        from .evdev_hotkey import EvdevHotkey
        return EvdevHotkey(hotkey_str, on_trigger, use_polling=use_polling)

    def make_double_click_watcher(self, on_double_click):
        # Kernel-level evdev double-click watcher: sees native Wayland AND
        # XWayland windows. Uses this backend's pointer_position() (XWayland).
        from .wayland_kde import WaylandDoubleClickWatcher
        return WaylandDoubleClickWatcher(self, on_double_click)

    # ---- popup positioning ----------------------------------------------
    # init_popup_window() and move_popup_window() are inherited from
    # X11Backend: under XWayland, Gtk.Window.move() positions the toplevel at
    # global coordinates, so no layer-shell setup is needed (and none exists on
    # Mutter anyway).
