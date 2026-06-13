"""Deterministic popup-render probe for Wayland/KDE.

Shows the real PopupWindow at a fixed point so a screenshot can confirm it
renders + positions via gtk-layer-shell. Throwaway test harness.
"""
import os
os.environ.setdefault("LINUXPOP_BACKEND", "wayland_kde")

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib

import plugin_loader
from classifier import classify
from popup import PopupWindow

plugin_loader.load_all()
X, Y = 900, 500
TEXT = "Hello KDE Wayland"


_p = None


def _show():
    global _p
    _p = PopupWindow(initial_grace_ms=60000, leave_grace_ms=60000)
    _p.show_for(TEXT, X, Y, classify(TEXT), editable=False)
    print(f"[probe] show_for at ({X},{Y}) done", flush=True)
    return False


def _diag(tag=""):
    w = _p.win
    print(f"[diag {tag}ms] --", flush=True)
    gw = w.get_window()
    print(f"[diag] visible={w.get_visible()} mapped={w.get_mapped()} "
          f"alloc={w.get_allocated_width()}x{w.get_allocated_height()}",
          flush=True)
    if gw is not None:
        print(f"[diag] gdkwindow pos={gw.get_position()} "
              f"size={gw.get_width()}x{gw.get_height()}", flush=True)
    try:
        import gi
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell as L
        print(f"[diag] layer anchored L={L.get_anchor(w, L.Edge.LEFT)} "
              f"T={L.get_anchor(w, L.Edge.TOP)} "
              f"marginL={L.get_margin(w, L.Edge.LEFT)} "
              f"marginT={L.get_margin(w, L.Edge.TOP)} "
              f"is_layer={L.is_layer_window(w)}", flush=True)
    except Exception as exc:
        print(f"[diag] layer query failed: {exc}", flush=True)
    return False


GLib.idle_add(_show)
GLib.timeout_add(400, lambda: _diag("400"))
GLib.timeout_add(900, lambda: _diag("900"))
GLib.timeout_add(2500, lambda: _diag("2500"))
GLib.timeout_add(7000, Gtk.main_quit)
Gtk.main()
