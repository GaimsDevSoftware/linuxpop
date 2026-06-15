"""LinuxPop platform backend selection.

`get_backend()` returns a cached PlatformBackend chosen by detect():
  - KDE Plasma Wayland  -> WaylandKdeBackend
  - everything else      -> X11Backend (the original behaviour)

The package is named `platform_backend`, not `platform`, to avoid shadowing
Python's stdlib `platform` module (imported by gi and others).
"""
from __future__ import annotations

import os

from .base import PlatformBackend

_backend: PlatformBackend | None = None


def detect() -> str:
    """Return 'wayland_kde' or 'x11'.

    Wayland KDE is chosen only when there is a Wayland display AND no usable
    X11 display - under XWayland (both set) we prefer the mature X11 path.
    Override with LINUXPOP_BACKEND=x11|wayland_kde.
    """
    forced = (os.environ.get("LINUXPOP_BACKEND") or "").strip().lower()
    if forced in ("x11", "wayland_kde"):
        return forced
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_x11 = bool(os.environ.get("DISPLAY"))
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    if has_wayland and not has_x11:
        return "wayland_kde"
    if session == "wayland" and has_wayland:
        # Pure Wayland session that also exposes XWayland: still prefer the
        # native Wayland backend (selection/hotkey via X won't see native
        # Wayland apps).
        return "wayland_kde"
    return "x11"


def get_backend() -> PlatformBackend:
    global _backend
    if _backend is None:
        choice = detect()
        if choice == "wayland_kde":
            from .wayland_kde import WaylandKdeBackend
            _backend = WaylandKdeBackend()
        else:
            from .x11 import X11Backend
            _backend = X11Backend()
    return _backend
