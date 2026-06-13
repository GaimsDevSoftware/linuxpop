#!/usr/bin/env python3
"""Fase 0 spike — can LinuxPop show a popup at the cursor on KWin Wayland?

This is a THROWAWAY prototype. Its only job is to answer the go/no-go question
for the whole Fedora KDE plan: on KDE Plasma 6 / Wayland, can we

  1. position a GTK window at arbitrary coordinates        (layer-shell)
  2. read the global cursor position                       (KWin DBus script)
  3. detect text-selection changes                          (wl-paste --watch)
  4. do all three fast enough to feel instant               (latency)

It is NOT production code and is not wired into the app. Run it on a real
Fedora KDE Plasma 6 Wayland session — it proves nothing on X11.

Usage:
    python3 spike.py --check layer     # just test positioning (fixed coords)
    python3 spike.py --check cursor     # just read cursorPos once, print latency
    python3 spike.py --check full       # the real thing: popup at cursor on selection

See spike/README.md for setup and how to read the results.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# GtkLayerShell is imported lazily (see _layer_shell) so that --check cursor,
# which needs no positioning, runs even when the layer-shell typelib is absent.
_LAYER = None


def _layer_shell():
    global _LAYER
    if _LAYER is None:
        try:
            gi.require_version("GtkLayerShell", "0.1")
        except ValueError:
            sys.stderr.write(
                "GtkLayerShell typelib not found. Install it (Fedora):\n"
                "  sudo dnf install gtk-layer-shell\n"
            )
            raise
        from gi.repository import GtkLayerShell
        _LAYER = GtkLayerShell
    return _LAYER

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KWIN_SCRIPT = os.path.join(SCRIPT_DIR, "cursor_pos.js")

BUS_NAME = "org.linuxpop.SpikeCursor"
OBJ_PATH = "/cursor"


def _warn_if_not_kde_wayland() -> None:
    sess = os.environ.get("XDG_SESSION_TYPE", "")
    desk = os.environ.get("XDG_CURRENT_DESKTOP", "")
    if sess != "wayland" or "KDE" not in desk:
        sys.stderr.write(
            f"\n*** WARNING: XDG_SESSION_TYPE={sess!r} XDG_CURRENT_DESKTOP={desk!r}\n"
            "*** This spike only means anything on a KDE Plasma Wayland session.\n"
            "*** Results here do not generalise.\n\n"
        )


# --------------------------------------------------------------------------
# Positioning: layer-shell anchored top+left, with margins = absolute coords.
# --------------------------------------------------------------------------
def make_popup(label_text: str) -> Gtk.Window:
    win = Gtk.Window()
    win.set_default_size(220, 70)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.set_margin_top(10)
    box.set_margin_bottom(10)
    box.set_margin_start(12)
    box.set_margin_end(12)
    for txt in ("Copy", "Search", "Ask AI"):
        box.pack_start(Gtk.Button(label=txt), False, False, 0)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    outer.pack_start(Gtk.Label(label=label_text), False, False, 0)
    outer.pack_start(box, False, False, 0)
    win.add(outer)

    L = _layer_shell()
    L.init_for_window(win)
    L.set_layer(win, L.Layer.OVERLAY)
    # Anchor to the TOP-LEFT corner of the output, then use margins as the
    # absolute (x, y). This is how you emulate free positioning under the
    # layer-shell anchor+margin model.
    L.set_anchor(win, L.Edge.LEFT, True)
    L.set_anchor(win, L.Edge.TOP, True)
    return win


def place_at(win: Gtk.Window, x: int, y: int) -> None:
    L = _layer_shell()
    L.set_margin(win, L.Edge.LEFT, int(x))
    L.set_margin(win, L.Edge.TOP, int(y))


# --------------------------------------------------------------------------
# Cursor position via KWin scripting over DBus.
# --------------------------------------------------------------------------
class CursorReader:
    """Loads cursor_pos.js into KWin and receives the coords back via DBus.

    Uses callDBus from inside the KWin script (see cursor_pos.js) instead of
    parsing journalctl, so we get the value directly and can time it.
    """

    def __init__(self) -> None:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop

        DBusGMainLoop(set_as_default=True)
        self._dbus = dbus
        self._bus = dbus.SessionBus()
        self._latest: tuple[int, int] | None = None
        self._on_report = None

        outer = self

        class _Svc(dbus.service.Object):
            @dbus.service.method(BUS_NAME, in_signature="ii", out_signature="")
            def Report(self, x, y):  # noqa: N802 (DBus method name)
                outer._latest = (int(x), int(y))
                if outer._on_report:
                    outer._on_report(int(x), int(y))

        name = dbus.service.BusName(BUS_NAME, self._bus)
        self._svc = _Svc(name, OBJ_PATH)

        self._kwin = self._bus.get_object("org.kde.KWin", "/Scripting")
        self._scripting = dbus.Interface(self._kwin, "org.kde.kwin.Scripting")

    def request(self, on_report) -> None:
        """Fire one cursorPos read; on_report(x, y) is called when it returns."""
        self._on_report = on_report
        # loadScript(path) -> script id; then run it. We load+run each time so
        # the script body (the callDBus) executes fresh. This per-call cost is
        # exactly the latency we want to measure.
        script_id = int(self._scripting.loadScript(KWIN_SCRIPT))
        script = self._bus.get_object("org.kde.KWin", f"/Scripting/Script{script_id}")
        self._dbus.Interface(script, "org.kde.kwin.Script").run()


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------
def check_layer(seconds: float = 6.0) -> int:
    """Just prove layer-shell can place a window at fixed coordinates."""
    win = make_popup("layer-shell @ (600, 400)")
    place_at(win, 600, 400)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    print(f"Shown a popup anchored at (600,400) for {seconds:.0f}s — watch the screen.")
    GLib.timeout_add(int(seconds * 1000), lambda: (win.destroy(), False)[1])
    Gtk.main()
    return 0


def check_cursor() -> int:
    """Read the cursor position once and report round-trip latency."""
    reader = CursorReader()
    t0 = time.monotonic()
    done = {"ok": False}

    def on_report(x: int, y: int) -> None:
        dt = (time.monotonic() - t0) * 1000
        print(f"cursorPos = ({x}, {y})   round-trip = {dt:.1f} ms")
        done["ok"] = True
        Gtk.main_quit()

    reader.request(on_report)
    GLib.timeout_add(3000, lambda: (print("TIMEOUT: no Report from KWin in 3s — "
                                           "is this KDE Wayland? is the script blocked?"),
                                     Gtk.main_quit())[1] and False)
    Gtk.main()
    return 0 if done["ok"] else 1


def check_full(seconds: float = 30.0) -> int:
    """The real thing: watch the PRIMARY selection, pop up at the cursor."""
    if not subprocess_which("wl-paste"):
        sys.stderr.write("wl-paste not found — sudo dnf install wl-clipboard\n")
        return 1

    reader = CursorReader()
    win = make_popup("(selection)")
    win.connect("destroy", Gtk.main_quit)

    def show_for_selection(text: str) -> None:
        t0 = time.monotonic()

        def on_report(x: int, y: int) -> None:
            place_at(win, x + 8, y + 8)
            win.set_title(text[:40])
            for child in win.get_children():
                child.show_all()
            win.show_all()
            dt = (time.monotonic() - t0) * 1000
            print(f"selection {text[:30]!r} -> popup at ({x},{y})  ({dt:.1f} ms)")

        reader.request(on_report)

    # `wl-paste --watch` runs CMD each time the PRIMARY selection changes,
    # piping the new selection to CMD's stdin. This replaces the X11 XFIXES
    # watcher (watcher.py) wholesale.
    watch = subprocess.Popen(
        ["wl-paste", "--primary", "--watch", "cat"],
        stdout=subprocess.PIPE, text=True,
    )

    def on_stdout(src, _cond):
        line = src.readline()
        if not line:
            return False
        sel = line.strip()
        if sel:
            GLib.idle_add(show_for_selection, sel)
        return True

    GLib.io_add_watch(watch.stdout, GLib.IO_IN, on_stdout)
    print(f"Select text in any app for the next {seconds:.0f}s — popup should appear "
          "at the cursor. (auto-exits)")
    GLib.timeout_add(int(seconds * 1000), Gtk.main_quit)
    try:
        Gtk.main()
    finally:
        watch.terminate()
    return 0


def subprocess_which(name: str) -> bool:
    import shutil
    return shutil.which(name) is not None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", choices=["layer", "cursor", "full"], default="full")
    ap.add_argument("--seconds", type=float, default=None,
                    help="auto-exit after N seconds (layer/full)")
    args = ap.parse_args()
    _warn_if_not_kde_wayland()
    if args.check == "layer":
        return check_layer(args.seconds if args.seconds else 6.0)
    if args.check == "cursor":
        return check_cursor()
    return check_full(args.seconds if args.seconds else 30.0)


if __name__ == "__main__":
    sys.exit(main())
