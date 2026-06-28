"""Plugin manager window using libhandy boxed-list rows."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

from xdg_paths import CONFIG_DIR

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gdk, GLib, Gtk, Handy, Pango  # noqa: E402

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: F401, E402
except (ImportError, ValueError):
    pass

Handy.init()


def _unwrap_subtitle_labels(root: Gtk.Widget) -> None:
    """Same treatment as settings_gui - let HdyActionRow subtitles wrap
    to multiple lines so plugin descriptions stay fully visible instead
    of being cropped to a single ellipsis-tailed line."""
    def visit(widget: Gtk.Widget) -> None:
        if isinstance(widget, Gtk.Label):
            try:
                ctx = widget.get_style_context()
                if ctx.has_class("subtitle"):
                    widget.set_ellipsize(Pango.EllipsizeMode.NONE)
                    widget.set_line_wrap(True)
                    widget.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                    widget.set_max_width_chars(-1)
                    widget.set_xalign(0)
                    try:
                        widget.set_lines(-1)
                    except AttributeError:
                        pass
            except Exception:
                pass
        if isinstance(widget, Gtk.Container):
            children: list[Gtk.Widget] = []
            try:
                widget.forall(lambda c, _: children.append(c), None)
            except Exception:
                children = widget.get_children()
            for c in children:
                visit(c)
    visit(root)
    def _again() -> bool:
        visit(root)
        return False
    GLib.idle_add(_again)
    GLib.timeout_add(150, _again)


def _unellipsize_tab_labels(root: Gtk.Widget) -> None:
    """Walk every Gtk.Label under `root` (INCLUDING internal-template
    children) and turn off ellipsize + width-cap.

    Why this is needed in three layers of confusion:
      1. HdyPreferencesWindow puts its tab strip inside an internal HdyHeaderBar
         that get_children() doesn't expose -- you have to use forall() to
         reach the titlebar's descendants.
      2. Each HdyViewSwitcherButton wraps an internal Stack that holds *two*
         GtkLabel instances per tab (one for the wide layout, one for
         narrow). Both have ellipsize=PANGO_ELLIPSIZE_END set in C and
         allocations like 36 px, so they crop to 'Availat' / 'Installe'.
      3. CSS cannot reach the ellipsize attribute -- it's a widget property,
         not a CSS one.

    So: forall-walk the realised tree, find every label, force
    ellipsize=NONE on it. With ellipsize off, the label demands its
    natural width and the parent reallocates accordingly.
    """
    tab_names = {"Available", "Installed", "Custom", "Order"}

    def visit(widget: Gtk.Widget) -> None:
        if isinstance(widget, Gtk.Label):
            try:
                widget.set_ellipsize(Pango.EllipsizeMode.NONE)
                widget.set_max_width_chars(-1)
                text = widget.get_text() or ""
                if text in tab_names:
                    # width_chars alone wasn't enough: HdyViewSwitcher's
                    # internal Gtk.Stack allocates each tab a fixed slot
                    # that still clipped the last character. Force a
                    # generous pixel-based minimum and let the parent
                    # widen instead of cropping us. ~14 px per character
                    # is roomy for the system font.
                    widget.set_width_chars(len(text) + 2)
                    widget.set_size_request(len(text) * 14, -1)
                    widget.set_hexpand(True)
                else:
                    widget.set_width_chars(-1)
            except Exception:
                pass
        if isinstance(widget, Gtk.Container):
            children = []
            try:
                # forall() exposes internal-template children that the
                # public get_children() hides - that's where the tab
                # labels live.
                widget.forall(lambda c, _: children.append(c), None)
            except Exception:
                children = widget.get_children()
            for child in children:
                visit(child)
    visit(root)

    # Also queue a re-run on the next idle cycle. HdyViewSwitcherTitle
    # has an internal GtkStack that swaps between wide and narrow layouts
    # based on allocated width, and the swap may happen AFTER the first
    # show_all -- which would replace the labels we just patched. Re-
    # patching from idle catches the post-swap labels too.
    def _again():
        visit(root)
        return False
    GLib.idle_add(_again)
    GLib.timeout_add(150, _again)


def _force_to_front(window: Gtk.Window) -> None:
    """Raise on X11 even past the clipboard picker's permanent
    keep_above. Same shape as settings_gui's helper -- the explicit
    GdkWindow.raise_() is what wins against another LinuxPop dialog
    that's already on top."""
    try:
        window.deiconify()
        gdk_win = window.get_window()
        if gdk_win is not None:
            try:
                ts = Gdk.X11.get_server_time(gdk_win)
            except Exception:
                ts = Gtk.get_current_event_time() or 0
            window.present_with_time(ts)
            try:
                gdk_win.raise_()
            except Exception:
                pass
        else:
            window.present()
        window.set_keep_above(True)
        GLib.timeout_add(150, lambda: (window.set_keep_above(False), False)[1])
        window.present()
    except Exception:
        try:
            window.present()
        except Exception:
            pass

REPO_DIR = Path(__file__).resolve().parent / "plugins_repo"
USER_PLUGIN_DIR = CONFIG_DIR / "plugins"
USER_RECIPE_DIR = CONFIG_DIR / "recipes"


def _target_dir(entry: dict) -> Path:
    """Where this catalogue entry installs to, based on its kind."""
    return USER_RECIPE_DIR if entry.get("kind") == "recipe" else USER_PLUGIN_DIR


def _source_path(entry: dict) -> Path:
    """Where the bundled source lives inside plugins_repo."""
    if entry.get("kind") == "recipe":
        return REPO_DIR / "recipes" / entry["file"]
    return REPO_DIR / entry["file"]


def _load_manifest() -> list[dict]:
    manifest_path = REPO_DIR / "manifest.json"
    if not manifest_path.is_file():
        return []
    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[plugin_manager] could not read manifest: {exc}")
        return []


def _is_installed(entry_or_filename) -> bool:
    """Check whether a catalogue entry (dict) or filename (str) is installed."""
    if isinstance(entry_or_filename, dict):
        return (_target_dir(entry_or_filename) / entry_or_filename["file"]).is_file()
    # Backwards-compat: string means a plugin .py file
    return (USER_PLUGIN_DIR / entry_or_filename).is_file()


class PluginManagerDialog:
    def __init__(self, on_changed: Callable[[], None] | None = None) -> None:
        self._on_changed = on_changed
        self._window: Handy.PreferencesWindow | None = None
        self._catalog_group: Handy.PreferencesGroup | None = None
        self._catalog_groups: list[Handy.PreferencesGroup] = []
        self._catalog_page: Handy.PreferencesPage | None = None
        self._installed_group: Handy.PreferencesGroup | None = None
        self._order_group: Handy.PreferencesGroup | None = None
        self._custom_group: Handy.PreferencesGroup | None = None

    def show(self, tab: str | None = None) -> None:
        """Open the Plugin Manager. tab may be one of 'available',
        'installed', 'custom', 'order' to land on a specific page;
        defaults to 'available'."""
        if self._window is not None and self._window.get_visible():
            if tab:
                self._switch_to_tab(tab)
            _force_to_front(self._window)
            return

        win = Handy.PreferencesWindow()
        win.set_title("LinuxPop - Plugins")
        win.set_search_enabled(False)
        win.set_default_size(780, 620)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_modal(False)
        win.connect("destroy", self._on_destroy)

        # Tab 1: catalog. Built as one PreferencesGroup per category so
        # the user sees Productivity / Web / AI / Developer separately
        # rather than one undifferentiated flat list. _catalog_group is
        # tracked as a reference to the *first* group purely so the
        # parent-page lookup in _rebuild_catalog_buttons still works.
        catalog_page = Handy.PreferencesPage()
        catalog_page.set_title("Available")
        catalog_page.set_icon_name("system-software-install-symbolic")
        self._catalog_groups: list[Handy.PreferencesGroup] = []
        for group in self._build_catalog_groups():
            catalog_page.add(group)
            self._catalog_groups.append(group)
        self._catalog_group = self._catalog_groups[0] if self._catalog_groups else None
        self._catalog_page = catalog_page
        win.add(catalog_page)

        # Tab 2: installed
        installed_page = Handy.PreferencesPage()
        installed_page.set_title("Installed")
        installed_page.set_icon_name("emblem-default-symbolic")
        self._installed_group = self._build_installed_group()
        installed_page.add(self._installed_group)
        win.add(installed_page)

        # Tab 3: custom actions (recipes)
        custom_page = Handy.PreferencesPage()
        custom_page.set_title("Custom")
        custom_page.set_icon_name("document-new-symbolic")
        self._custom_group = self._build_custom_group()
        custom_page.add(self._custom_group)
        win.add(custom_page)

        # Tab 4: order
        order_page = Handy.PreferencesPage()
        order_page.set_title("Order")
        order_page.set_icon_name("view-sort-ascending-symbolic")
        self._order_group = self._build_order_group()
        order_page.add(self._order_group)
        win.add(order_page)

        win.show_all()
        # Libhandy's HdyViewSwitcherButton hard-codes
        # gtk_label_set_ellipsize(PANGO_ELLIPSIZE_END) on the tab labels
        # at the C level -- CSS can't undo that. Walk the widget tree
        # after show_all() and switch every label in the header area off
        # ellipsize so "Available", "Installed", "Custom" stop being
        # clipped to "Availat", "Installe", "Custor".
        _unellipsize_tab_labels(win)
        # Same after-show_all patch as the settings dialog: let plugin
        # description subtitles wrap rather than getting ellipsised.
        _unwrap_subtitle_labels(win)
        self._window = win
        # Remember the pages we created so we can switch tabs later.
        self._pages_by_tab = {
            "available": catalog_page,
            "installed": installed_page,
            "custom": custom_page,
            "order": order_page,
        }
        if tab:
            self._switch_to_tab(tab)
        _force_to_front(win)

    def _switch_to_tab(self, tab: str) -> None:
        """Activate one of the PreferencesWindow's tabs by name. Names
        match the keys of _pages_by_tab. No-op for unknown tabs."""
        if not hasattr(self, "_pages_by_tab"):
            return
        page = self._pages_by_tab.get(tab)
        if page is None or self._window is None:
            return
        try:
            self._window.set_visible_child(page)
        except Exception:
            # libhandy versions differ on the public API for tab switching;
            # silently degrade rather than crashing the popup overflow.
            pass

    def _on_destroy(self, *_):
        self._window = None
        self._catalog_group = None
        self._catalog_groups = []
        self._catalog_page = None
        self._installed_group = None
        self._order_group = None
        self._custom_group = None

    # ---- catalog tab ---------------------------------------------------------

    # Display order for category sections. Categories present in the
    # manifest but missing here are appended in first-seen order.
    _CATEGORY_ORDER = [
        "Snippets & Clipboard", "Productivity", "Web shortcuts",
        "AI", "Developer",
    ]
    _CATEGORY_DESCRIPTIONS = {
        "Snippets & Clipboard":
            "The picker that remembers what you've copied and the snippets "
            "you save for reuse. The core of what LinuxPop is for.",
        "Productivity":  "Everyday actions - math, counts, speech, formatting.",
        "Web shortcuts": "Open the selection in a search engine or web service.",
        "AI":            "Send the selection to a chat AI or run one locally.",
        "Developer":     "Encoders, hashes and converters for technical text.",
    }

    def _build_catalog_groups(self) -> list[Handy.PreferencesGroup]:
        manifest = _load_manifest()
        if not manifest:
            group = Handy.PreferencesGroup()
            group.set_title("Plugin catalogue")
            row = Handy.ActionRow()
            row.set_title("Catalogue is empty")
            row.set_subtitle("plugins_repo/manifest.json is missing or unreadable")
            group.add(row)
            return [group]

        # Bucket by category, preserving manifest order within each bucket.
        buckets: dict[str, list[dict]] = {}
        for entry in manifest:
            cat = entry.get("category") or "Other"
            buckets.setdefault(cat, []).append(entry)

        # Stable, opinionated ordering: known categories first in the order
        # defined above, then anything else by first appearance.
        seen = set()
        ordered_cats: list[str] = []
        for cat in self._CATEGORY_ORDER:
            if cat in buckets:
                ordered_cats.append(cat)
                seen.add(cat)
        for cat in buckets:
            if cat not in seen:
                ordered_cats.append(cat)

        groups: list[Handy.PreferencesGroup] = []
        for cat in ordered_cats:
            group = Handy.PreferencesGroup()
            group.set_title(cat)
            desc = self._CATEGORY_DESCRIPTIONS.get(cat)
            if desc:
                group.set_description(desc)
            for entry in buckets[cat]:
                group.add(self._make_catalog_row(entry))
            groups.append(group)
        return groups

    def _build_catalog_group(self) -> Handy.PreferencesGroup:
        # Kept for backwards-compat / single-group callers. Returns the
        # first category group; in practice _build_catalog_groups() is
        # what the page now uses.
        groups = self._build_catalog_groups()
        return groups[0] if groups else Handy.PreferencesGroup()

    def _badge(self, icon_name: str | None, key: str) -> Gtk.Widget:
        """Each row's leading icon, in the onboarding-store style: a rounded
        tile holding the plugin's REAL popup icon. Symbolic icons render white
        on a brand-coloured tile (the colour is stable per plugin); a colourful
        logo (e.g. an AI service) sits on a light tile so it reads. Falls back
        to a coloured initial only when there's no usable icon."""
        try:
            import icon_style
            icon_name = icon_style.resolve(icon_name)
        except Exception:
            pass
        key = (key or "?").strip()
        idx = (sum(ord(c) for c in key) % 4) if key else 0
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.set_size_request(34, 34)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_end(10)
        ctx = box.get_style_context()
        ctx.add_class("lp-badge")
        has = bool(icon_name) and Gtk.IconTheme.get_default().has_icon(icon_name)
        if has and icon_name.endswith("-symbolic"):
            ctx.add_class(f"lp-badge-{idx}")
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
            img.set_pixel_size(18)
            img.get_style_context().add_class("lp-badge-glyph")
            box.pack_start(img, True, True, 0)
        elif has:
            ctx.add_class("lp-badge-plain")
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
            img.set_pixel_size(22)
            box.pack_start(img, True, True, 0)
        else:
            ctx.add_class(f"lp-badge-{idx}")
            lbl = Gtk.Label(label=(key[:1].upper() if key else "*"))
            lbl.get_style_context().add_class("lp-badge-letter")
            box.pack_start(lbl, True, True, 0)
        box.show_all()
        return box

    def _make_catalog_row(self, entry: dict) -> Handy.ActionRow:
        row = Handy.ActionRow()
        row.set_title(entry.get("title", entry["file"]))
        row.add_prefix(self._badge(entry.get("icon"), entry.get("title", entry["file"])))
        desc = entry.get("description", "")
        tags = entry.get("tags") or []
        if tags:
            desc = f"{desc}\n{' · '.join(tags)}"
        row.set_subtitle(desc)

        btn = Gtk.Button()
        btn.set_valign(Gtk.Align.CENTER)
        self._update_install_button(btn, entry)
        btn.connect("clicked", self._on_install_clicked, entry, row)
        row.add(btn)
        return row

    def _update_install_button(self, btn: Gtk.Button, entry: dict) -> None:
        ctx = btn.get_style_context()
        ctx.remove_class("suggested-action")
        ctx.remove_class("destructive-action")
        if _is_installed(entry):
            btn.set_label("Remove")
            ctx.add_class("destructive-action")
        else:
            btn.set_label("Install")
            ctx.add_class("suggested-action")

    def _on_install_clicked(self, btn: Gtk.Button, entry: dict, row: Handy.ActionRow) -> None:
        filename = entry["file"]
        src = _source_path(entry)
        dst_dir = _target_dir(entry)
        dst = dst_dir / filename
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            if _is_installed(entry):
                dst.unlink()
                print(f"[plugin_manager] uninstalled {filename}")
            else:
                shutil.copy2(src, dst)
                print(f"[plugin_manager] installed {filename}")
        except OSError as exc:
            print(f"[plugin_manager] error: {exc}")
            return
        self._update_install_button(btn, entry)
        # Refresh installed-tab content if the window is still up
        self._refresh_installed_group()
        if self._on_changed:
            self._on_changed()

    # ---- installed tab -------------------------------------------------------

    def _build_installed_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("User plugins")
        group.set_description(
            "Plugins you currently have. Install more from the Available tab, "
            "or remove the ones you don't use."
        )
        self._fill_installed_group(group)
        return group

    def _fill_installed_group(self, group: Handy.PreferencesGroup) -> None:
        USER_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
        files = sorted(USER_PLUGIN_DIR.glob("*.py"))
        if not files:
            empty = Handy.ActionRow()
            empty.set_title("No plugins installed yet")
            empty.set_subtitle("Install one from the Available tab to get started.")
            group.add(empty)
            return
        manifest_by_file = {e["file"]: e for e in _load_manifest()}

        # Map source-file -> list of plugin names that file registered.
        # When a bundle exposes more than one action (editing_actions
        # registers cut/paste/paste-and-enter/backspace/select-all), we
        # render the file as an expander with one switch per sub-plugin
        # so the user can keep what they want and silence the rest.
        try:
            import plugin_loader as _pl
            by_source = _pl.plugins_by_source()
            all_plugins = {p.name: p for p in _pl.all_plugins()}
        except Exception:
            by_source = {}
            all_plugins = {}
        try:
            from settings import get_settings as _gs
            settings_obj = _gs()
            disabled_names = set(settings_obj.get("disabled_plugins") or [])
        except Exception:
            settings_obj = None
            disabled_names = set()

        for path in files:
            entry = manifest_by_file.get(path.name)
            sub_names = sorted(by_source.get(path.name, []))
            # Two layouts: single-action bundles render as ActionRow with
            # a Remove button on the right; multi-action bundles render
            # as ExpanderRow with sub-rows per sub-plugin, plus Remove.
            if len(sub_names) > 1:
                row = Handy.ExpanderRow()
                row.set_enable_expansion(True)
                row.set_show_enable_switch(False)
            else:
                row = Handy.ActionRow()
            if entry is not None:
                row.set_title(entry.get("title", path.name))
                row.set_subtitle(entry.get("description", ""))
            else:
                row.set_title(path.stem.replace("_", " ").title())
                row.set_subtitle("Installed by hand (not in the catalogue)")
            _icon = (entry.get("icon") if entry else None)
            if not _icon and sub_names:
                _p0 = all_plugins.get(sub_names[0])
                _icon = getattr(_p0, "icon", None) if _p0 else None
            row.add_prefix(self._badge(_icon, row.get_title()))

            remove_btn = Gtk.Button(label="Remove")
            remove_btn.set_valign(Gtk.Align.CENTER)
            remove_btn.get_style_context().add_class("destructive-action")
            remove_btn.connect("clicked", self._on_remove_clicked, path)
            row.add(remove_btn)

            if isinstance(row, Handy.ExpanderRow):
                for sub_name in sub_names:
                    sub_plugin = all_plugins.get(sub_name)
                    sub_row = Handy.ActionRow()
                    sub_row.set_title(
                        (sub_plugin.tooltip if sub_plugin else sub_name) or sub_name)
                    sub_row.set_subtitle(f"action: {sub_name}")
                    sw = Gtk.Switch()
                    sw.set_valign(Gtk.Align.CENTER)
                    sw.set_active(sub_name not in disabled_names)
                    sw.connect(
                        "notify::active",
                        self._on_subplugin_toggle, sub_name,
                    )
                    sub_row.add(sw)
                    sub_row.set_activatable_widget(sw)
                    row.add(sub_row)
            group.add(row)

    def _on_subplugin_toggle(self, switch: Gtk.Switch, _param, sub_name: str) -> None:
        try:
            from settings import get_settings as _gs
            settings_obj = _gs()
            disabled = list(settings_obj.get("disabled_plugins") or [])
        except Exception:
            return
        if switch.get_active():
            disabled = [d for d in disabled if d != sub_name]
        else:
            if sub_name not in disabled:
                disabled.append(sub_name)
        settings_obj.set("disabled_plugins", disabled)
        settings_obj.save()
        # Trigger the on_changed callback so the daemon reloads plugins
        # and the filter takes effect immediately.
        if self._on_changed:
            try:
                self._on_changed()
            except Exception:
                pass

    def _refresh_installed_group(self) -> None:
        if self._installed_group is None:
            return
        for child in list(self._installed_group.get_children()):
            # remove() only drops the parent ref - without destroy() the
            # row's GObject (and its signal-handler closures over Path
            # objects) stays alive until Python GC notices the orphan,
            # which on long-running daemons leaks across many refreshes.
            self._installed_group.remove(child)
            child.destroy()
        self._fill_installed_group(self._installed_group)
        self._installed_group.show_all()
        _unwrap_subtitle_labels(self._installed_group)

    def _on_remove_clicked(self, _btn: Gtk.Button, path: Path) -> None:
        try:
            path.unlink()
        except OSError as exc:
            print(f"[plugin_manager] could not remove {path}: {exc}")
            return
        # Refresh both tabs (install button on catalog tab changes too)
        self._refresh_installed_group()
        self._rebuild_catalog_buttons()
        if self._on_changed:
            self._on_changed()

    # ---- custom (recipes) tab ------------------------------------------------

    def _build_custom_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Custom actions")
        group.set_description(
            "Create your own popup buttons without writing Python. Each recipe "
            "is a small JSON file in ~/.config/linuxpop/recipes/."
        )

        # "+ New action" row at the top
        new_row = Handy.ActionRow()
        new_row.set_title("New action…")
        new_row.set_subtitle("Open URL · Run command · Notify · Transform clipboard")
        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.get_style_context().add_class("suggested-action")
        add_btn.connect("clicked", lambda *_: self._open_recipe_wizard())
        new_row.add(add_btn)
        new_row.set_activatable_widget(add_btn)
        group.add(new_row)

        self._fill_custom_group(group)
        return group

    def _fill_custom_group(self, group: Handy.PreferencesGroup) -> None:
        import recipe_loader
        # Translate the internal action-type slug into something a person
        # who didn't write the JSON would recognise.
        atype_labels = {
            "open_url":         "Opens a web page",
            "run_command":      "Runs a command",
            "notify":           "Shows a notification",
            "copy_transformed": "Transforms text and copies it",
        }
        for path, recipe in recipe_loader.list_recipes():
            row = Handy.ActionRow()
            row.set_title(recipe.get("tooltip") or recipe.get("name", path.stem))
            atype = (recipe.get("action") or {}).get("type", "")
            template = (recipe.get("action") or {}).get("template", "")
            short = template if len(template) <= 70 else template[:67] + "…"
            action_label = atype_labels.get(atype, atype or "Custom action")
            row.set_subtitle(f"{action_label}  ·  {short}")

            # On/off toggle. Lets the user disable a custom button without
            # deleting it. Default-True for recipes that don't carry the
            # 'enabled' key yet.
            enabled_switch = Gtk.Switch()
            enabled_switch.set_valign(Gtk.Align.CENTER)
            enabled_switch.set_tooltip_text("Show this button in the popup")
            enabled_switch.set_active(bool(recipe.get("enabled", True)))
            enabled_switch.connect("notify::active",
                                   self._on_toggle_recipe, path)
            row.add(enabled_switch)

            edit_btn = Gtk.Button.new_from_icon_name("document-edit-symbolic", Gtk.IconSize.BUTTON)
            edit_btn.set_valign(Gtk.Align.CENTER)
            edit_btn.set_tooltip_text("Edit")
            edit_btn.connect("clicked", self._on_edit_recipe, path, recipe)
            row.add(edit_btn)

            del_btn = Gtk.Button.new_from_icon_name("user-trash-symbolic", Gtk.IconSize.BUTTON)
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text("Delete")
            del_btn.get_style_context().add_class("destructive-action")
            del_btn.connect("clicked", self._on_delete_recipe, path)
            row.add(del_btn)
            group.add(row)

    def _refresh_custom_group(self) -> None:
        if self._custom_group is None:
            return
        for child in list(self._custom_group.get_children()):
            self._custom_group.remove(child)
            child.destroy()
        self._fill_custom_group_with_header(self._custom_group)
        self._custom_group.show_all()
        _unwrap_subtitle_labels(self._custom_group)

    def _fill_custom_group_with_header(self, group: Handy.PreferencesGroup) -> None:
        # "+ New action" row at the top (re-added on refresh)
        new_row = Handy.ActionRow()
        new_row.set_title("New action…")
        new_row.set_subtitle("Open URL · Run command · Notify · Transform clipboard")
        add_btn = Gtk.Button.new_from_icon_name("list-add-symbolic", Gtk.IconSize.BUTTON)
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.get_style_context().add_class("suggested-action")
        add_btn.connect("clicked", lambda *_: self._open_recipe_wizard())
        new_row.add(add_btn)
        new_row.set_activatable_widget(add_btn)
        group.add(new_row)
        self._fill_custom_group(group)

    def _open_recipe_wizard(self, existing: dict | None = None,
                            existing_path: Path | None = None) -> None:
        from recipe_wizard import RecipeWizard
        parent = self._window
        wizard = RecipeWizard(parent=parent, recipe=existing, source_path=existing_path)
        new_recipe = wizard.run_and_get()
        if new_recipe is None:
            return
        import recipe_loader
        try:
            saved_path = recipe_loader.save_recipe(new_recipe, target_path=existing_path)
            print(f"[plugin_manager] saved recipe to {saved_path}")
        except OSError as exc:
            print(f"[plugin_manager] could not save recipe: {exc}")
            return
        self._refresh_custom_group()
        if self._on_changed:
            self._on_changed()

    def _on_edit_recipe(self, _btn, path: Path, recipe: dict) -> None:
        self._open_recipe_wizard(existing=recipe, existing_path=path)

    def _on_delete_recipe(self, _btn, path: Path) -> None:
        import recipe_loader
        recipe_loader.delete_recipe(path)
        self._refresh_custom_group()
        if self._on_changed:
            self._on_changed()

    def _on_toggle_recipe(self, switch: Gtk.Switch, _param, path: Path) -> None:
        """Flip the recipe's 'enabled' field and reload the plugin layer
        so the popup picks up the change without a restart."""
        import recipe_loader
        recipe_loader.set_recipe_enabled(path, switch.get_active())
        # Don't repaint the whole group -- that would rebuild every row
        # and lose the switch animation feedback. Just trigger the
        # plugin-reload callback so the popup reflects the change.
        if self._on_changed:
            self._on_changed()

    # ---- order tab -----------------------------------------------------------

    def _build_order_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Popup button order")
        group.set_description(
            "Drag a plugin by its handle (≡) to drop it anywhere in the list, "
            "or use the ↑ / ↓ buttons for one step at a time. The popup shows "
            "plugins in this order (those not listed fall back to their "
            "built-in priority)."
        )
        self._fill_order_group(group)
        return group

    def _fill_order_group(self, group: Handy.PreferencesGroup) -> None:
        import plugin_loader
        try:
            from settings import get_settings
            settings = get_settings()
        except Exception:
            settings = None

        all_plugins = plugin_loader.all_plugins()
        if not all_plugins:
            row = Handy.ActionRow()
            row.set_title("No plugins loaded")
            group.add(row)
            return

        # Compute effective order: user list first, then unlisted by priority
        order = list((settings.get("plugin_order") if settings else None) or [])
        order_idx = {n: i for i, n in enumerate(order)}
        sorted_plugins = sorted(
            all_plugins,
            key=lambda p: (
                (0, order_idx[p.name]) if p.name in order_idx
                else (1, p.priority),
            ),
        )

        # Full ordered name list, kept in sync for drag-and-drop + arrow moves.
        self._order_names = [p.name for p in sorted_plugins]
        dnd_targets = [Gtk.TargetEntry.new(
            "LINUXPOP_ORDER_ROW", Gtk.TargetFlags.SAME_APP, 0)]

        for index, plugin in enumerate(sorted_plugins):
            row = Handy.ActionRow()
            row.set_title(plugin.tooltip or plugin.name)
            row.set_subtitle(plugin.name)
            row._order_index = index
            row._order_icon = plugin.icon
            try:
                img = Gtk.Image.new_from_icon_name(plugin.icon, Gtk.IconSize.LARGE_TOOLBAR)
                img.set_pixel_size(20)
                row.add_prefix(img)
            except Exception:
                pass

            # Drag-and-drop: the whole row is a drag source, and every row is a
            # drop target. Dropping onto another row moves this plugin to that
            # slot. The ↑/↓ buttons stay for precise / keyboard-driven moves.
            try:
                row.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, dnd_targets,
                                    Gdk.DragAction.MOVE)
                row.drag_dest_set(Gtk.DestDefaults.ALL, dnd_targets,
                                  Gdk.DragAction.MOVE)
                row.connect("drag-begin", self._on_order_drag_begin)
                row.connect("drag-data-get", self._on_order_drag_data_get)
                row.connect("drag-data-received", self._on_order_drag_data_received)
                theme = Gtk.IconTheme.get_default()
                if theme.has_icon("list-drag-handle-symbolic"):
                    grip = Gtk.Image.new_from_icon_name(
                        "list-drag-handle-symbolic", Gtk.IconSize.BUTTON)
                else:
                    grip = Gtk.Label(label="⣿")  # braille block, grip-like
                grip.set_valign(Gtk.Align.CENTER)
                grip.set_tooltip_text("Drag to reorder")
                grip.get_style_context().add_class("dim-label")
                row.add(grip)
            except Exception as exc:  # noqa: BLE001
                print(f"[plugin_manager] drag-reorder setup failed: {exc}")

            up_btn = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
            up_btn.set_valign(Gtk.Align.CENTER)
            up_btn.set_sensitive(index > 0)
            up_btn.set_tooltip_text("Move up")
            up_btn.connect("clicked", self._on_move, index, -1, sorted_plugins)
            row.add(up_btn)

            down_btn = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
            down_btn.set_valign(Gtk.Align.CENTER)
            down_btn.set_sensitive(index < len(sorted_plugins) - 1)
            down_btn.set_tooltip_text("Move down")
            down_btn.connect("clicked", self._on_move, index, +1, sorted_plugins)
            row.add(down_btn)

            group.add(row)

    def _refresh_order_group(self) -> None:
        if self._order_group is None:
            return
        for child in list(self._order_group.get_children()):
            self._order_group.remove(child)
            child.destroy()
        self._fill_order_group(self._order_group)
        self._order_group.show_all()
        _unwrap_subtitle_labels(self._order_group)

    def _on_move(self, _btn, index: int, delta: int, sorted_plugins) -> None:
        """Swap index with index+delta; persist the new full ordering."""
        new_idx = index + delta
        if new_idx < 0 or new_idx >= len(sorted_plugins):
            return
        names = [p.name for p in sorted_plugins]
        names[index], names[new_idx] = names[new_idx], names[index]
        self._persist_order(names)

    def _persist_order(self, ordered_names) -> None:
        """Save the full plugin order, then refresh the list and the popup."""
        try:
            from settings import get_settings
            s = get_settings()
            s.set("plugin_order", list(ordered_names))
            s.save()
        except Exception as exc:  # noqa: BLE001
            print(f"[plugin_manager] could not save order: {exc}")
            return
        self._refresh_order_group()
        if self._on_changed:
            self._on_changed()

    # ---- drag-and-drop reordering -------------------------------------------
    def _on_order_drag_begin(self, row, context) -> None:
        # Show the plugin's own icon under the cursor while dragging.
        try:
            Gtk.drag_set_icon_name(
                context, getattr(row, "_order_icon", "view-list-symbolic"), 8, 8)
        except Exception:
            pass

    def _on_order_drag_data_get(self, row, context, data, info, time) -> None:
        data.set_text(str(getattr(row, "_order_index", -1)), -1)

    def _on_order_drag_data_received(self, row, context, x, y, data, info, time) -> None:
        try:
            src = int(data.get_text())
        except (TypeError, ValueError):
            return
        dst = getattr(row, "_order_index", -1)
        names = list(getattr(self, "_order_names", []) or [])
        if not (0 <= src < len(names)) or not (0 <= dst < len(names)) or src == dst:
            return
        # Move the dragged plugin to the dropped-on row's position.
        names.insert(dst, names.pop(src))
        self._persist_order(names)

    def _rebuild_catalog_buttons(self) -> None:
        page = getattr(self, "_catalog_page", None)
        if page is None:
            return
        # Wipe every existing category group, then append fresh ones.
        # HdyPreferencesPage exposes children via forall(), not get_children().
        old_groups = list(getattr(self, "_catalog_groups", []) or [])
        for g in old_groups:
            parent = g.get_parent()
            if parent is not None:
                parent.remove(g)
        self._catalog_groups = []
        for group in self._build_catalog_groups():
            page.add(group)
            group.show_all()
            _unwrap_subtitle_labels(group)
            self._catalog_groups.append(group)
        self._catalog_group = self._catalog_groups[0] if self._catalog_groups else None
