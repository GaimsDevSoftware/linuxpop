"""Display the selection in giant text on screen.

Lifted straight from PopClip - useful for reading something to a
colleague across the room, showing a confirmation number from your
screen to a friend on the phone, or just enlarging a tiny menu item
your eyes don't like. Click anywhere or press Esc / Space to dismiss.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from plugin_base import Plugin


_CSS = b"""
window.linuxpop-large-type {
    background-color: rgba(10, 12, 20, 0.92);
}

label.linuxpop-large-type-label {
    color: #f7f8fa;
    font-size: 96pt;
    font-weight: 600;
    padding: 60px;
}
"""

_css_installed = False


def _install_css() -> None:
    global _css_installed
    if _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    _css_installed = True


def _show_large(text: str) -> None:
    # GTK calls must hop to the main loop - popup plugin handlers run
    # in worker threads, but Gtk.Window construction needs the main
    # thread to avoid threading errors.
    def _build() -> bool:
        _install_css()
        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("LinuxPop - Large Type")
        win.set_decorated(False)
        win.fullscreen()
        win.set_app_paintable(True)
        win.get_style_context().add_class("linuxpop-large-type")
        # RGBA visual so the dim background actually renders translucent.
        screen = win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            win.set_visual(visual)

        label = Gtk.Label()
        label.set_text(text)
        label.set_line_wrap(True)
        label.set_justify(Gtk.Justification.CENTER)
        label.set_xalign(0.5)
        label.set_yalign(0.5)
        label.set_selectable(True)
        label.get_style_context().add_class("linuxpop-large-type-label")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(label)
        win.add(scroll)

        def _dismiss(*_):
            win.destroy()
            return True

        win.connect("key-press-event",
                    lambda _w, e: _dismiss() if e.keyval in
                    (Gdk.KEY_Escape, Gdk.KEY_space, Gdk.KEY_Return) else False)
        win.connect("button-press-event", _dismiss)
        win.show_all()
        return False

    GLib.idle_add(_build)


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="large-type",
        icon="zoom-in-symbolic",
        tooltip="Show as large text",
        handler=_show_large,
        content_types=(),  # works for anything
        priority=70,
    ))
