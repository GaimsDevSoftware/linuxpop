"""LinuxPop platform backend selection.

`get_backend()` returns a cached PlatformBackend chosen by detect():
  - KDE Plasma Wayland   -> WaylandKdeBackend (native: gtk-layer-shell + KWin)
  - GNOME Wayland        -> XWaylandGnomeBackend (XWayland positioning +
                            Wayland-native I/O; Mutter has no layer-shell)
  - everything else       -> X11Backend (the original behaviour)

The package is named `platform_backend`, not `platform`, to avoid shadowing
Python's stdlib `platform` module (imported by gi and others).
"""
from __future__ import annotations

import os

from .base import PlatformBackend

_backend: PlatformBackend | None = None


def detect() -> str:
    """Return 'wayland_kde', 'xwayland_gnome', or 'x11'.

    A Wayland session routes by desktop environment:
      - KDE   -> wayland_kde      (native gtk-layer-shell + KWin cursor query)
      - GNOME -> xwayland_gnome   (Mutter lacks layer-shell; we run the popup
                                   under XWayland and use Wayland-native I/O)
      - other Wayland -> wayland_kde (best-effort; wlroots/Sway support
                                   layer-shell, so the KDE path mostly works)
    A non-Wayland session is always the mature X11 path.

    Override with LINUXPOP_BACKEND=x11|wayland_kde|xwayland_gnome.
    """
    forced = (os.environ.get("LINUXPOP_BACKEND") or "").strip().lower()
    if forced in ("x11", "wayland_kde", "xwayland_gnome"):
        return forced
    has_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))
    has_x11 = bool(os.environ.get("DISPLAY"))
    session = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
    is_wayland_session = has_wayland and (session == "wayland" or not has_x11)
    if is_wayland_session:
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
        if "GNOME" in desktop:
            # Mutter: no layer-shell and no external cursor API, so the KDE
            # path can't place the popup. Run under XWayland instead.
            return "xwayland_gnome"
        # KDE (native) and any other Wayland compositor fall through to the
        # KDE backend: it's native on KDE and a reasonable best-effort on
        # wlroots-based compositors (which do implement layer-shell).
        return "wayland_kde"
    return "x11"


def get_backend() -> PlatformBackend:
    global _backend
    if _backend is None:
        choice = detect()
        if choice == "wayland_kde":
            from .wayland_kde import WaylandKdeBackend
            _backend = WaylandKdeBackend()
        elif choice == "xwayland_gnome":
            from .xwayland_gnome import XWaylandGnomeBackend
            _backend = XWaylandGnomeBackend()
        else:
            from .x11 import X11Backend
            _backend = X11Backend()
    return _backend
