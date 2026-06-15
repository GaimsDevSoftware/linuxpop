"""Print the KWin global pointer position as "x y" and exit (KDE Wayland only).

Wayland exposes no protocol for a client to query the global pointer. KDE's
KWin scripting API does (`workspace.cursorPos`), reachable over DBus. We load a
one-line KWin script that pushes the position back to us via callDBus - far
faster than the journalctl route - then print and exit.

Run as a short-lived subprocess from wayland_kde.WaylandKdeBackend.pointer_position
so the round-trip's GLib main loop never tangles with the main app's loop.
"""
from __future__ import annotations

import os
import sys
import tempfile

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib  # noqa: E402

import dbus  # noqa: E402
import dbus.service  # noqa: E402
from dbus.mainloop.glib import DBusGMainLoop  # noqa: E402

# App-id-prefixed so a Flatpak sandbox auto-allows owning it (the bus filter
# only lets an app own its app-id and sub-names; the old "org.linuxpop.Cursor"
# was rejected with org.freedesktop.DBus.Error.ServiceUnknown). Harmless
# outside Flatpak. _JS references the constant so both sides stay in sync.
BUS_NAME = "io.github.GaimsDevSoftware.LinuxPop.Cursor"
OBJ_PATH = "/cursor"
_JS = (
    f'callDBus("{BUS_NAME}", "{OBJ_PATH}", "{BUS_NAME}", '
    '"Report", workspace.cursorPos.x, workspace.cursorPos.y);'
)


def main() -> int:
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    loop = GLib.MainLoop()
    got: dict[str, tuple[int, int]] = {}

    class _Svc(dbus.service.Object):
        @dbus.service.method(BUS_NAME, in_signature="ii", out_signature="")
        def Report(self, x, y):  # noqa: N802 - DBus method name
            got["xy"] = (int(x), int(y))
            print(f"{int(x)} {int(y)}", flush=True)
            loop.quit()

    name = dbus.service.BusName(BUS_NAME, bus)
    _svc = _Svc(name, OBJ_PATH)

    # Write the script to the per-user runtime dir, not /tmp: under Flatpak
    # $XDG_RUNTIME_DIR/linuxpop maps to the same host path KWin reads (granted
    # via --filesystem=xdg-run/linuxpop), so no host /tmp access is needed.
    _rt = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    _dir = os.path.join(_rt, "linuxpop")
    os.makedirs(_dir, mode=0o700, exist_ok=True)
    fd, path = tempfile.mkstemp(suffix=".js", prefix="linuxpop-cursor-", dir=_dir)
    try:
        os.write(fd, _JS.encode("utf-8"))
        os.close(fd)
        scripting = dbus.Interface(
            bus.get_object("org.kde.KWin", "/Scripting"),
            "org.kde.kwin.Scripting",
        )
        script_id = int(scripting.loadScript(path))
        script = bus.get_object("org.kde.KWin", f"/Scripting/Script{script_id}")
        dbus.Interface(script, "org.kde.kwin.Script").run()
        GLib.timeout_add(1500, lambda: (loop.quit(), False)[1])
        loop.run()
        # CRUCIAL: unload the script. KWin keeps every loaded script resident;
        # loading one per cursor read (i.e. per popup) without unloading leaks
        # them and eventually destabilises the compositor.
        try:
            scripting.unloadScript(path)
        except Exception:
            pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0 if "xy" in got else 1


if __name__ == "__main__":
    sys.exit(main())
