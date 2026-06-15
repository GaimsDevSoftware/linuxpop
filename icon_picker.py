"""Searchable icon-picker dialog backed by the current GTK icon theme.

Lists every icon name the running theme exposes (usually 1500-3000), with
a live search box and a grid of preview thumbnails. Click → returns the
chosen icon name; Esc → returns None.

Usage:
    chosen = IconPicker(parent=some_window, initial="folder-symbolic").run()
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

USER_ICONS_DIR = Path(os.path.expanduser("~/.config/linuxpop/icons"))
HICOLOR_APPS = Path.home() / ".local/share/icons/hicolor/scalable/apps"

# How many icons to render per "page". GTK FlowBox is fast enough that
# 1000+ per page is fine on modern hardware; lazy loading was solving a
# problem we didn't really have.
_PAGE_SIZE = 1500
_ICON_RENDER_SIZE = 24  # pixels in the preview grid

# When a search has zero matches, offer these generic icons. Brand-specific
# icons aren't in standard themes (Adwaita, hicolor) for trademark reasons.
_FALLBACK_SUGGESTIONS = [
    "applications-internet",
    "web-browser-symbolic",
    "system-search-symbolic",
    "applications-utilities-symbolic",
    "help-about-symbolic",
    "starred-symbolic",
    "view-list-symbolic",
    "applications-other",
]


def _sync_user_icons() -> None:
    """Make sure user-supplied icons in ~/.config/linuxpop/icons/ are
    visible to the GTK icon theme. Mirror them into hicolor so they show
    up by basename in the picker and in plugin button references.
    """
    USER_ICONS_DIR.mkdir(parents=True, exist_ok=True)
    HICOLOR_APPS.mkdir(parents=True, exist_ok=True)
    copied = False
    for src in list(USER_ICONS_DIR.glob("*.svg")) + list(USER_ICONS_DIR.glob("*.png")):
        dst = HICOLOR_APPS / src.name
        if not dst.is_file() or src.stat().st_mtime > dst.stat().st_mtime:
            try:
                shutil.copy2(src, dst)
                copied = True
            except OSError as exc:
                print(f"[icon_picker] could not mirror {src.name}: {exc}")
    if copied:
        # Force GTK to rescan so the new icons appear without a restart
        try:
            theme = Gtk.IconTheme.get_default()
            if theme is not None:
                theme.rescan_if_needed()
        except Exception:
            pass


def _list_all_icons() -> list[str]:
    _sync_user_icons()
    theme = Gtk.IconTheme.get_default()
    if theme is None:
        return []
    try:
        names = list(theme.list_icons())
    except Exception:
        return []
    # Stable order, deduplicated
    return sorted(set(names))


class IconPicker:
    def __init__(self, parent=None, initial: str = "") -> None:
        self._parent = parent
        self._initial = initial
        self._all_icons: list[str] = []
        self._filtered: list[str] = []
        self._rendered: int = 0
        self._chosen: str | None = None
        self._symbolic_only = True

    # ---- public ----------------------------------------------------------

    def run(self) -> str | None:
        self._all_icons = _list_all_icons()

        dlg = Gtk.Dialog(
            title="Pick an icon",
            transient_for=self._parent,
            flags=Gtk.DialogFlags.MODAL,
        )
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL)
        dlg.set_default_size(640, 520)
        dlg.set_icon_name("linuxpop")
        self._dialog = dlg

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                        margin=10)

        # Top bar: search + symbolic-only toggle
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self._search = Gtk.SearchEntry()
        self._search.set_placeholder_text("Search icons… (e.g. folder, mail, save)")
        self._search.connect("search-changed", self._on_search)
        top.pack_start(self._search, True, True, 0)

        self._symbolic_toggle = Gtk.CheckButton(label="Symbolic only")
        self._symbolic_toggle.set_active(True)
        self._symbolic_toggle.set_tooltip_text(
            "Show only monochrome icons (the ones that tint with your theme)."
        )
        self._symbolic_toggle.connect("toggled", self._on_symbolic_toggle)
        top.pack_start(self._symbolic_toggle, False, False, 0)

        outer.pack_start(top, False, False, 0)

        # Result count line - supports clickable links in empty-state
        self._count_label = Gtk.Label(xalign=0)
        self._count_label.set_line_wrap(True)
        self._count_label.set_track_visited_links(False)
        self._count_label.connect("activate-link", self._on_link_clicked)
        self._count_label.get_style_context().add_class("dim-label")
        outer.pack_start(self._count_label, False, False, 0)

        # Scrollable grid
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._scroll.set_hexpand(True)
        self._scroll.set_vexpand(True)
        self._scroll.get_vadjustment().connect("value-changed", self._on_scroll)

        self._flowbox = Gtk.FlowBox()
        # valign=FILL lets the box take all the vertical space the scroller
        # gives it, so rows wrap downward to fill the dialog instead of
        # bunching at the top.
        self._flowbox.set_valign(Gtk.Align.START)
        self._flowbox.set_halign(Gtk.Align.FILL)
        self._flowbox.set_hexpand(True)
        # Letting GTK pick wrapping (no min/max) so it lays out as many
        # children per line as the current width allows.
        self._flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flowbox.set_row_spacing(2)
        self._flowbox.set_column_spacing(2)
        self._scroll.add(self._flowbox)
        outer.pack_start(self._scroll, True, True, 0)

        dlg.get_content_area().add(outer)
        dlg.show_all()
        self._search.grab_focus()

        # Initial population
        self._apply_filter()

        dlg.run()
        dlg.destroy()
        return self._chosen

    # ---- filtering -------------------------------------------------------

    def _apply_filter(self) -> None:
        query = self._search.get_text().strip().lower()
        symbolic_only = self._symbolic_toggle.get_active()

        if symbolic_only:
            pool = [n for n in self._all_icons if n.endswith("-symbolic")]
        else:
            pool = list(self._all_icons)

        if query:
            # Substring match across icon names
            self._filtered = [n for n in pool if query in n.lower()]
        else:
            self._filtered = pool

        # Wipe current grid
        for child in list(self._flowbox.get_children()):
            self._flowbox.remove(child)
        self._rendered = 0

        if not self._filtered and query:
            # Zero matches - show suggestions + hint about Symbolic toggle + user icons
            esc_query = GLib.markup_escape_text(query)
            symbolic_hint = (
                " <b>Tip:</b> if you added a colored brand icon, "
                "uncheck <i>Symbolic only</i> above to see it."
                if symbolic_only else ""
            )
            self._count_label.set_markup(
                f"<i>No icons match “{esc_query}”. Standard themes don't ship "
                f"brand-specific icons (Wikipedia, GitHub etc.).</i>\n"
                f"<small>Drop your own <tt>.svg</tt> or <tt>.png</tt> files in "
                f"<a href=\"linuxpop://open-user-icons\">"
                f"~/.config/linuxpop/icons/</a>.{symbolic_hint} "
                f"Or pick a generic one:</small>"
            )
            self._filtered = _FALLBACK_SUGGESTIONS
        else:
            self._count_label.set_markup(
                f"{len(self._filtered)} matching icons "
                f"({len(self._all_icons)} total in your theme) - "
                f"<a href=\"linuxpop://open-user-icons\">add your own</a>"
            )

        self._render_next_page()

    def _render_next_page(self) -> None:
        end = min(self._rendered + _PAGE_SIZE, len(self._filtered))
        for name in self._filtered[self._rendered:end]:
            child = self._make_icon_tile(name)
            if child is not None:
                self._flowbox.add(child)
        self._rendered = end
        self._flowbox.show_all()

    def _make_icon_tile(self, name: str) -> Gtk.Widget | None:
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(name)
        img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.LARGE_TOOLBAR)
        img.set_pixel_size(_ICON_RENDER_SIZE)
        btn.add(img)
        btn.connect("clicked", self._on_icon_clicked, name)
        return btn

    # ---- events ----------------------------------------------------------

    def _on_search(self, _entry) -> None:
        # Debounce slightly: tiny GLib timeout
        GLib.idle_add(self._apply_filter)

    def _on_symbolic_toggle(self, _btn) -> None:
        self._apply_filter()

    def _on_scroll(self, adj) -> None:
        # When near the bottom, render the next page
        if self._rendered >= len(self._filtered):
            return
        if adj.get_value() + adj.get_page_size() >= adj.get_upper() - 100:
            self._render_next_page()

    def _on_icon_clicked(self, _btn, name: str) -> None:
        self._chosen = name
        self._dialog.response(Gtk.ResponseType.OK)

    def _on_link_clicked(self, _label, uri: str) -> bool:
        """Handle the custom linuxpop:// link in the empty-state message."""
        if uri == "linuxpop://open-user-icons":
            USER_ICONS_DIR.mkdir(parents=True, exist_ok=True)
            argv = (["flatpak-spawn", "--host", "xdg-open", str(USER_ICONS_DIR)]
                    if os.path.exists("/.flatpak-info")
                    else ["xdg-open", str(USER_ICONS_DIR)])
            try:
                subprocess.Popen(argv, start_new_session=True)
            except FileNotFoundError:
                pass
            return True  # consumed
        return False     # let GTK handle real URIs
