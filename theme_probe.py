"""Render a search entry + HdyExpanderRow with the dark theme, for a
screenshot check that the KDE/Wayland dark-base fix works. Throwaway."""
import os
os.environ.setdefault("LINUXPOP_BACKEND", "wayland_kde")
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gtk, Handy, GLib

import theme
Handy.init()
theme.install_premium_theme("dark")

w = Gtk.Window()
w.set_default_size(520, 320)
box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
box.set_margin_top(10); box.set_margin_bottom(10)
box.set_margin_start(10); box.set_margin_end(10)
box.pack_start(Gtk.SearchEntry(), False, False, 0)
lb = Gtk.ListBox()
lb.get_style_context().add_class("boxed-list")
exp = Handy.ExpanderRow()
exp.set_title("Per-service method (advanced)")
exp.set_subtitle("Override the global send method for individual services.")
lb.add(exp)
ar = Handy.ActionRow(); ar.set_title("Gemini"); ar.set_subtitle("Google's chat assistant")
lb.add(ar)
box.pack_start(lb, True, True, 0)
w.add(box)
w.show_all()
GLib.timeout_add(6000, Gtk.main_quit)
Gtk.main()
