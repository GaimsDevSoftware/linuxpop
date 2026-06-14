"""Print the active window's "resourceClass\\tcaption" and exit (KDE Wayland).

Same KWin-scripting-over-DBus trick as _kwin_cursor.py, used by
WaylandKdeBackend.active_window_haystacks for the popup blocklist. Only spawned
when the user actually has blocklist patterns, so its cost never hits the
default no-blocklist path.
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

BUS_NAME = "org.linuxpop.ActiveWin"
OBJ_PATH = "/win"
# KWin 6 renamed activeClient -> activeWindow; fall back for older builds.
_JS = (
    'var c = workspace.activeWindow || workspace.activeClient;'
    'callDBus("org.linuxpop.ActiveWin", "/win", "org.linuxpop.ActiveWin", '
    '"Report", c ? String(c.resourceClass) : "", c ? String(c.caption) : "");'
)


def main() -> int:
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    loop = GLib.MainLoop()
    got: dict[str, tuple[str, str]] = {}

    class _Svc(dbus.service.Object):
        @dbus.service.method(BUS_NAME, in_signature="ss", out_signature="")
        def Report(self, cls, caption):  # noqa: N802
            got["v"] = (str(cls), str(caption))
            print(f"{cls}\t{caption}", flush=True)
            loop.quit()

    name = dbus.service.BusName(BUS_NAME, bus)
    _svc = _Svc(name, OBJ_PATH)

    fd, path = tempfile.mkstemp(suffix=".js", prefix="linuxpop-active-")
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
        try:
            scripting.unloadScript(path)  # don't leak resident KWin scripts
        except Exception:
            pass
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    return 0 if "v" in got else 1


if __name__ == "__main__":
    sys.exit(main())
