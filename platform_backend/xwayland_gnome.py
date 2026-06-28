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
from typing import Optional

from .x11 import X11Backend


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
        # wl-paste --primary --watch sees native Wayland apps' selection (the
        # X11 XFixes watcher would only see XWayland apps). The watcher calls
        # back into this backend for read_selection() and pointer_position().
        from .wayland_kde import WaylandSelectionWatcher
        return WaylandSelectionWatcher(self, on_selection,
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
