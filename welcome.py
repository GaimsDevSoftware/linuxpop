"""First-run welcome dialog.

Shown exactly once, on the first time LinuxPop starts. The goal is to:

  1. Explain in 10 seconds how to use the app (select text, get popup).
  2. Point at the tray icon so the user knows where to find settings.
  3. Quietly offer a way to support the project. The support row is a
     calm secondary CTA - no nag, no countdown, no modal-blocking guilt
     trip. Research is clear that nag screens kill goodwill and don't
     meaningfully lift conversion.

The dialog dismissal sets the same FIRST_RUN_MARKER that main.py was
using before, so the welcome flow can't trigger twice.
"""
from __future__ import annotations

import subprocess
from typing import Iterable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402


_SUPPORT_BUTTONS = [
    # (settings-key, icon, label, accent-class)
    # PayPal is the only donation channel exposed in the picker -
    # Ko-fi, Buy Me a Coffee and GitHub Sponsors were removed in
    # May 2026 because no real account existed behind any of them,
    # so the buttons led to 404s. (GitHub still shows its own
    # Sponsor button via .github/FUNDING.yml - that goes straight
    # to paypal.me/linuxpop too.) To add another channel later,
    # add its key here AND the default URL in settings.py.
    ("support_paypal_url",   "emblem-favorite-symbolic",
     "Support on PayPal",        "suggested-action"),
]


def show_welcome_dialog(
    settings,
    on_open_plugins=None,
    parent: Gtk.Window | None = None,
) -> None:
    """Render the welcome dialog. Non-modal so the user can still
    interact with the popup if they trigger it accidentally during the
    first run."""
    if not bool(settings.get("show_welcome_dialog")):
        return

    dlg = Gtk.Dialog(
        title="Welcome to LinuxPop",
        transient_for=parent,
        flags=Gtk.DialogFlags.MODAL,
    )
    dlg.set_default_size(560, 0)
    dlg.set_icon_name("linuxpop")
    dlg.set_position(Gtk.WindowPosition.CENTER)

    content = dlg.get_content_area()
    content.set_spacing(0)

    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                   margin_top=24, margin_bottom=20,
                   margin_start=28, margin_end=28)

    # ---- header ----
    hero_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    icon = Gtk.Image.new_from_icon_name("linuxpop", Gtk.IconSize.DIALOG)
    icon.set_pixel_size(56)
    hero_row.pack_start(icon, False, False, 0)

    head_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    title = Gtk.Label(xalign=0)
    title.set_markup("<span size='xx-large' weight='bold'>Welcome to LinuxPop</span>")
    head_box.pack_start(title, False, False, 0)
    sub = Gtk.Label(xalign=0)
    sub.set_markup(
        "<span foreground='#b8c0d4'>"
        "A floating popup of context-aware actions for any text you select."
        "</span>")
    sub.set_line_wrap(True)
    head_box.pack_start(sub, False, False, 0)
    hero_row.pack_start(head_box, True, True, 0)
    outer.pack_start(hero_row, False, False, 0)

    # Divider
    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
    sep.set_margin_top(4)
    sep.set_margin_bottom(4)
    outer.pack_start(sep, False, False, 0)

    # ---- 3-step usage explainer ----
    steps_lbl = Gtk.Label(xalign=0)
    steps_lbl.set_markup("<b>How to use it</b>")
    outer.pack_start(steps_lbl, False, False, 0)

    steps_grid = Gtk.Grid(column_spacing=10, row_spacing=6)
    for row, (n, head, body) in enumerate([
        (1, "Highlight text",
         "Select anything in any app -- a URL, an error message, a paragraph."),
        (2, "Click the popup button",
         "LinuxPop shows a small floating bar of actions tailored to what you selected."),
        (3, "Open the tray icon for more",
         "Top panel, right side -- find Settings, Plugin Manager, and your custom buttons."),
        (4, "Starter plugins are pre-installed",
         "Clipboard history, send-to-AI, word count, large type, transforms, and search shortcuts. "
         "Open Plugin Manager to add more (developer tools, code formatters, QR, etc.)."),
    ]):
        num = Gtk.Label(xalign=0.5, yalign=0)
        num.set_markup(
            f"<span foreground='#5B7DF5' weight='bold' size='large'>{n}.</span>")
        steps_grid.attach(num, 0, row, 1, 1)
        h = Gtk.Label(xalign=0, yalign=0)
        h.set_markup(f"<b>{head}</b>  <span foreground='#b8c0d4'>{body}</span>")
        h.set_line_wrap(True)
        h.set_hexpand(True)
        steps_grid.attach(h, 1, row, 1, 1)
    outer.pack_start(steps_grid, False, False, 0)

    # ---- support row (only if URLs are configured) ----
    support_urls = [
        (key, icon_name, label, accent)
        for key, icon_name, label, accent in _SUPPORT_BUTTONS
        if (settings.get(key) or "").strip()
    ]
    if support_urls:
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        sep2.set_margin_top(8)
        sep2.set_margin_bottom(4)
        outer.pack_start(sep2, False, False, 0)

        support_head = Gtk.Label(xalign=0)
        support_head.set_markup("<b>Like it?</b>")
        outer.pack_start(support_head, False, False, 0)

        support_blurb = Gtk.Label(xalign=0)
        support_blurb.set_markup(
            "<span foreground='#b8c0d4'>"
            "LinuxPop is free and open source. If it saves you time, a "
            "small tip keeps the project moving. No pressure -- the link "
            "is also in the tray menu under About."
            "</span>")
        support_blurb.set_line_wrap(True)
        outer.pack_start(support_blurb, False, False, 0)

        button_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for key, icon_name, label, accent in support_urls:
            url = (settings.get(key) or "").strip()
            btn = Gtk.Button()
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            inner.pack_start(
                Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON),
                False, False, 0)
            inner.pack_start(Gtk.Label(label=label), False, False, 0)
            btn.add(inner)
            if accent:
                btn.get_style_context().add_class(accent)
            btn.connect("clicked",
                        lambda _b, u=url: _open_url(u))
            button_row.pack_start(btn, False, False, 0)
        outer.pack_start(button_row, False, False, 0)

    # ---- bottom action row ----
    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                    margin_top=10)
    if on_open_plugins is not None:
        plug_btn = Gtk.Button(label="Open Plugin Manager...")
        plug_btn.connect("clicked",
                         lambda _b: (dlg.destroy(), on_open_plugins()))
        bottom.pack_start(plug_btn, False, False, 0)

    spacer = Gtk.Label()
    bottom.pack_start(spacer, True, True, 0)

    close_btn = Gtk.Button(label="Got it -- let's go")
    close_btn.get_style_context().add_class("suggested-action")
    close_btn.connect("clicked", lambda _b: dlg.destroy())
    bottom.pack_start(close_btn, False, False, 0)
    outer.pack_start(bottom, False, False, 0)

    content.add(outer)
    dlg.show_all()


def _open_url(url: str) -> None:
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
    except Exception:
        pass


# Reused by the tray "Support LinuxPop..." entry and the About dialog.
def open_support_picker(settings, parent: Gtk.Window | None = None) -> None:
    """Standalone dialog listing the configured support options. Same
    look as the welcome's support row, but reachable at any time."""
    urls = [
        (settings.get(key) or "").strip()
        for key, *_ in _SUPPORT_BUTTONS
    ]
    configured = [(spec, url) for spec, url in zip(_SUPPORT_BUTTONS, urls) if url]

    dlg = Gtk.Dialog(
        title="Support LinuxPop",
        transient_for=parent,
        flags=Gtk.DialogFlags.MODAL,
    )
    dlg.set_default_size(440, 0)
    dlg.set_icon_name("linuxpop")
    dlg.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

    content = dlg.get_content_area()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                 margin_top=20, margin_bottom=16,
                 margin_start=24, margin_end=24)

    if not configured:
        msg = Gtk.Label(xalign=0)
        msg.set_markup(
            "<span foreground='#b8c0d4'>"
            "No donation links are configured. Set "
            "<tt>support_paypal_url</tt> in your settings.json to "
            "enable the PayPal button."
            "</span>")
        msg.set_line_wrap(True)
        box.pack_start(msg, False, False, 0)
    else:
        head = Gtk.Label(xalign=0)
        head.set_markup(
            "<span size='large' weight='bold'>Thanks for considering it.</span>")
        box.pack_start(head, False, False, 0)
        blurb = Gtk.Label(xalign=0)
        blurb.set_markup(
            "<span foreground='#b8c0d4'>"
            "Tips are what let one-person Linux projects keep shipping."
            "</span>")
        blurb.set_line_wrap(True)
        box.pack_start(blurb, False, False, 0)

        for (key, icon_name, label, accent), url in configured:
            btn = Gtk.Button()
            inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            inner.pack_start(
                Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON),
                False, False, 0)
            inner.pack_start(Gtk.Label(label=label), False, False, 0)
            btn.add(inner)
            if accent:
                btn.get_style_context().add_class(accent)
            btn.connect("clicked",
                        lambda _b, u=url: _open_url(u))
            box.pack_start(btn, False, False, 0)

    close = Gtk.Button(label="Close")
    close.connect("clicked", lambda _b: dlg.destroy())
    bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, margin_top=6)
    spacer = Gtk.Label()
    bottom.pack_start(spacer, True, True, 0)
    bottom.pack_start(close, False, False, 0)
    box.pack_start(bottom, False, False, 0)

    content.add(box)
    dlg.show_all()
