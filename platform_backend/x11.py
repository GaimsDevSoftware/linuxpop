"""X11 platform backend - the original LinuxPop behaviour.

Wraps xclip (selection/clipboard), Xlib (pointer), xdotool (keystrokes /
active window) and the existing X11 watcher / hotkey / double-click modules.
Behaviour here must stay byte-for-byte what LinuxPop did before the
platform_backend refactor, so X11 users see no change.
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Callable, Optional

from .base import PlatformBackend

# Reused Xlib connection for pointer queries (opening a fresh Display() per
# call costs ~5-15 ms of XOpenDisplay roundtrip, which the hotkey path hits on
# every press). Module-scope; never closed - lives for the process lifetime.
_pointer_dpy = None


class X11Backend(PlatformBackend):
    name = "x11"
    popup_uses_xlib = True

    # ---- session ---------------------------------------------------------
    def check_session(self) -> None:
        """Pure X11 fully supported; XWayland warns but starts; pure Wayland
        is handled by the wayland_kde backend, so reaching here without a
        DISPLAY is fatal."""
        has_x11 = bool(os.environ.get("DISPLAY"))
        has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
        if not has_x11:
            print("DISPLAY is not set - the X11 backend needs an X11 display.",
                  file=sys.stderr)
            sys.exit(2)
        if has_wayland and has_x11:
            warning = (
                "Running under XWayland: PRIMARY-selection auto-popup will only "
                "fire for legacy X11 apps. The global hotkey still works "
                "everywhere the X11 grab is honoured. For full functionality, "
                "use an Xorg session - or a KDE Plasma Wayland session, which "
                "LinuxPop now supports natively."
            )
            print(f"[linuxpop] WARNING: {warning}", file=sys.stderr)
            try:
                subprocess.run(
                    ["notify-send", "--hint=byte:transient:1", "-t", "6000",
                     "-u", "normal", "-i", "linuxpop",
                     "LinuxPop: limited under XWayland", warning],
                    check=False,
                )
            except FileNotFoundError:
                pass

    # ---- selection / clipboard ------------------------------------------
    def read_selection(self, source: str) -> str:
        sel = "primary" if source.lower() == "primary" else "clipboard"
        try:
            out = subprocess.run(
                ["xclip", "-selection", sel, "-o"],
                capture_output=True, timeout=0.5,
            )
            return out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def set_clipboard(self, text: str) -> None:
        import shutil
        if not shutil.which("xclip"):
            print("[x11] xclip not installed, cannot set clipboard")
            return
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"), check=False, timeout=2.0,
            )
        except subprocess.SubprocessError as exc:
            print(f"[x11] xclip failed: {exc}")

    # ---- pointer ---------------------------------------------------------
    def pointer_position(self) -> tuple[int, int]:
        global _pointer_dpy
        from Xlib import display
        if _pointer_dpy is None:
            _pointer_dpy = display.Display()
        try:
            data = _pointer_dpy.screen().root.query_pointer()
            return data.root_x, data.root_y
        except Exception:
            try:
                _pointer_dpy.close()
            except Exception:
                pass
            _pointer_dpy = None
            raise

    # ---- keystroke injection --------------------------------------------
    def send_key(self, combo: str) -> None:
        import shutil
        if not shutil.which("xdotool"):
            print("[x11] xdotool missing - cannot send key")
            return
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", combo], check=False,
        )

    def can_paste(self) -> bool:
        import shutil
        return bool(shutil.which("xdotool"))

    def type_text(self, text: str) -> None:
        import shutil
        if not shutil.which("xdotool"):
            print("[x11] xdotool missing - cannot type text")
            return
        subprocess.run(
            ["xdotool", "type", "--clearmodifiers", "--", text], check=False,
        )

    # ---- opening URLs ----------------------------------------------------
    def open_url(self, url: str) -> None:
        import shutil
        if not shutil.which("xdg-open"):
            print("[x11] xdg-open not available")
            return
        # X11 window managers raise the browser on their own.
        subprocess.Popen(["xdg-open", url], start_new_session=True)

    # ---- active window ---------------------------------------------------
    def active_window_haystacks(self) -> list[str]:
        """Window title (xdotool) + WM_CLASS (xprop). xprop is used for the
        class because xdotool getwindowclassname is missing in several distro
        builds."""
        haystacks: list[str] = []
        try:
            wid = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=0.3,
            ).stdout.strip()
            if not wid:
                return []
            try:
                out = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=0.3,
                ).stdout.strip()
                if out:
                    haystacks.append(out.lower())
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                out = subprocess.run(
                    ["xprop", "-id", wid, "WM_CLASS"],
                    capture_output=True, text=True, timeout=0.3,
                ).stdout.strip()
                if out:
                    haystacks.append(out.lower())
            except (OSError, subprocess.SubprocessError):
                pass
        except (OSError, subprocess.SubprocessError):
            return []
        return haystacks

    # ---- component factories --------------------------------------------
    def make_selection_watcher(self, on_selection, debounce_ms):
        from watcher import SelectionWatcher
        return SelectionWatcher(on_selection, debounce_ms=debounce_ms)

    def make_hotkey(self, hotkey_str, on_trigger, use_polling=False):
        from hotkey import Hotkey
        return Hotkey(hotkey_str, on_trigger, use_polling=use_polling)

    def make_double_click_watcher(self, on_double_click):
        from mouse_watcher import DoubleClickWatcher
        return DoubleClickWatcher(on_double_click)

    # ---- popup positioning ----------------------------------------------
    def init_popup_window(self, win) -> None:
        # X11 positions with Gtk.Window.move(); nothing to set up.
        return None

    def move_popup_window(self, win, x: int, y: int) -> None:
        win.move(int(x), int(y))
