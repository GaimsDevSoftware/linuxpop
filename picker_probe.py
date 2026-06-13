"""Open the real clipboard picker with the dark theme for a screenshot check.
Throwaway."""
import os
os.environ.setdefault("LINUXPOP_BACKEND", "wayland_kde")
import sys
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gtk, Handy, GLib

import theme
import plugin_loader

Handy.init()
theme.install_premium_theme("dark")
plugin_loader.load_all()


def go():
    mod = sys.modules.get("linuxpop_user_clipboard_history")
    if mod and hasattr(mod, "open_picker"):
        mod.open_picker(None)
        print("[picker_probe] open_picker called")
    else:
        print("[picker_probe] no picker module:", mod)
    return False


GLib.idle_add(go)
GLib.timeout_add(8000, Gtk.main_quit)
Gtk.main()
