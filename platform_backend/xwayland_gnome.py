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

from .x11 import X11Backend


class XWaylandGnomeBackend(X11Backend):
    name = "xwayland_gnome"
    # Running under XWayland: the popup is an X11 toplevel, so the Xlib-based
    # pointer/Esc polling and Gtk.Window.move() inherited from X11Backend apply.
    popup_uses_xlib = True
    # The pointer now comes from the GNOME Shell extension's global.get_pointer(),
    # which is in LOGICAL (stage) coordinates - the same space as the monitor
    # geometry and Gtk.Window.move() - so the popup must NOT divide by the scale.
    pointer_is_logical = True

    def __init__(self) -> None:
        super().__init__()
        # Lazily-created KDE backend instance reused purely as a Wayland I/O
        # helper for clipboard + key injection (its wl-clipboard / ydotool code
        # is compositor-agnostic). Never used for pointer or positioning.
        self._wl = None
        self._shell_pointer = None   # cached D-Bus proxy to the Shell extension
        self._scale_cache = None     # monitor scale, for the no-extension path

    # ---- pointer (via the GNOME Shell extension) -------------------------
    def pointer_position(self) -> "tuple[int, int]":
        # GNOME Wayland gives an X11/XWayland app no way to read the global
        # cursor over native-Wayland windows: XQueryPointer freezes there and
        # GNOME never shipped the wlr virtual-pointer / layer-shell protocols.
        # Our bundled GNOME Shell extension publishes global.get_pointer() over
        # D-Bus (logical coords) - query it.
        try:
            import dbus
            if self._shell_pointer is None:
                obj = dbus.SessionBus().get_object(
                    "org.gnome.Shell",
                    "/io/github/GaimsDevSoftware/LinuxPop/Pointer")
                self._shell_pointer = dbus.Interface(
                    obj, "io.github.GaimsDevSoftware.LinuxPop.Pointer")
            x, y = self._shell_pointer.GetPointer()
            return int(x), int(y)
        except Exception:  # noqa: BLE001
            # Extension not loaded yet (it needs a Shell reload / re-login) or
            # disabled. Fall back to XQueryPointer converted to logical; it
            # freezes over native-Wayland windows, but it won't crash.
            self._shell_pointer = None
            px, py = super().pointer_position()
            s = self._logical_scale()
            return int(px / s), int(py / s)

    def _logical_scale(self) -> int:
        if self._scale_cache is None:
            try:
                import gi
                gi.require_version("Gdk", "3.0")
                from gi.repository import Gdk
                disp = Gdk.Display.get_default()
                mon = disp.get_primary_monitor() or disp.get_monitor(0)
                self._scale_cache = mon.get_scale_factor() or 1
            except Exception:  # noqa: BLE001
                self._scale_cache = 1
        return self._scale_cache

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
    def _activate_app(self) -> None:
        # Clicking the popup hands keyboard focus to it on GNOME, so an injected
        # chord would land on the popup. Ask the Shell extension to re-focus the
        # app (the MRU normal window) first, then give the compositor a moment
        # to apply it before we inject. No-op (and harmless) if the app never
        # actually lost focus, or if the extension isn't loaded.
        import time
        try:
            import dbus
            if self._shell_pointer is None:
                obj = dbus.SessionBus().get_object(
                    "org.gnome.Shell",
                    "/io/github/GaimsDevSoftware/LinuxPop/Pointer")
                self._shell_pointer = dbus.Interface(
                    obj, "io.github.GaimsDevSoftware.LinuxPop.Pointer")
            self._shell_pointer.ActivateApp()
        except Exception:  # noqa: BLE001
            # ActivateApp needs the updated extension (loaded after a re-login).
            # Don't drop the proxy - it's still valid for GetPointer.
            pass
        # Always pause briefly: it gives the compositor time to settle focus
        # after the popup click, which fixes the injection even when the focus
        # shift is only transient (no extension re-focus needed).
        time.sleep(0.12)

    def send_key(self, combo: str) -> None:
        self._activate_app()
        self._wl_io().send_key(combo)

    def type_text(self, text: str) -> None:
        self._activate_app()
        self._wl_io().type_text(text)

    def can_paste(self) -> bool:
        return self._wl_io().can_paste()

    def paste(self) -> None:
        self._activate_app()
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
        # Mutter has no wlr-data-control (so `wl-paste --watch` is dead), and
        # POLLING wl-paste turned out to hammer the source app into re-serving
        # the primary selection several times a second - which made the
        # selection highlight and the text caret blink and disrupted typing.
        # Use the X11 XFixes watcher instead: it is event-driven (reads the
        # selection only ONCE, when it actually changes), and Mutter bridges
        # the Wayland primary selection to the X11 PRIMARY, so XFixes sees BOTH
        # native-Wayland and XWayland app selections.
        from watcher import SelectionWatcher
        # pointer_fn: XQueryPointer freezes over native-Wayland windows here, so
        # the watcher must read the cursor through our GNOME-Shell extension
        # (pointer_position) instead - otherwise the popup anchors at a stale
        # spot regardless of where the selection actually was.
        return SelectionWatcher(on_selection, debounce_ms=debounce_ms,
                                pointer_fn=self.pointer_position)

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
