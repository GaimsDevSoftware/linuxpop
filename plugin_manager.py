"""Plugin manager window using libhandy boxed-list rows."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Callable

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
                # For the tab labels specifically: also set width-chars
                # to the text length so the label REQUESTS at least that
                # many character-widths from its parent. Without this,
                # ellipsize=NONE alone just lets the label render beyond
                # its tight allocation and get visually clipped by the
                # parent button's overflow -- which is what was happening
                # ('Availat' shown without ellipsis dots, because there
                # were no ellipsis dots, just visual overflow clipping).
                text = widget.get_text() or ""
                if text in tab_names:
                    widget.set_width_chars(len(text) + 1)
                else:
                    widget.set_width_chars(-1)
            except Exception:
                pass
        if isinstance(widget, Gtk.Container):
            children = []
            try:
                # forall() exposes internal-template children that the
                # public get_children() hides — that's where the tab
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
    """Same trick as settings_gui — reliably raise on X11 against
    focus-stealing prevention."""
    try:
        window.deiconify()
        gdk_win = window.get_window()
        if gdk_win is not None:
            try:
                ts = Gdk.X11.get_server_time(gdk_win)
            except Exception:
                ts = Gtk.get_current_event_time() or 0
            window.present_with_time(ts)
        else:
            window.present()
        window.set_keep_above(True)
        GLib.timeout_add(150, lambda: (window.set_keep_above(False), False)[1])
    except Exception:
        try:
            window.present()
        except Exception:
            pass

REPO_DIR = Path(__file__).resolve().parent / "plugins_repo"
USER_PLUGIN_DIR = Path(os.path.expanduser("~/.config/linuxpop/plugins"))
USER_RECIPE_DIR = Path(os.path.expanduser("~/.config/linuxpop/recipes"))


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
        self._installed_group: Handy.PreferencesGroup | None = None
        self._order_group: Handy.PreferencesGroup | None = None
        self._custom_group: Handy.PreferencesGroup | None = None

    def show(self) -> None:
        if self._window is not None and self._window.get_visible():
            _force_to_front(self._window)
            return

        win = Handy.PreferencesWindow()
        win.set_title("LinuxPop — Plugins")
        win.set_search_enabled(False)
        win.set_default_size(780, 620)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_modal(False)
        win.connect("destroy", self._on_destroy)

        # Tab 1: catalog
        catalog_page = Handy.PreferencesPage()
        catalog_page.set_title("Available")
        catalog_page.set_icon_name("system-software-install-symbolic")
        self._catalog_group = self._build_catalog_group()
        catalog_page.add(self._catalog_group)
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
        self._window = win
        _force_to_front(win)

    def _on_destroy(self, *_):
        self._window = None
        self._catalog_group = None
        self._installed_group = None
        self._order_group = None
        self._custom_group = None

    # ---- catalog tab ---------------------------------------------------------

    def _build_catalog_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Plugin catalogue")
        group.set_description("Built-in plugins you can install with one click.")

        manifest = _load_manifest()
        if not manifest:
            row = Handy.ActionRow()
            row.set_title("Catalogue is empty")
            row.set_subtitle("plugins_repo/manifest.json is missing or unreadable")
            group.add(row)
            return group

        for entry in manifest:
            group.add(self._make_catalog_row(entry))
        return group

    def _make_catalog_row(self, entry: dict) -> Handy.ActionRow:
        row = Handy.ActionRow()
        row.set_title(entry.get("title", entry["file"]))
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
            f"Files in {USER_PLUGIN_DIR}. Drop your own .py files there or install from the Available tab."
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
        for path in files:
            entry = manifest_by_file.get(path.name)
            row = Handy.ActionRow()
            if entry is not None:
                row.set_title(entry.get("title", path.name))
                row.set_subtitle(f"{path.name}\n{entry.get('description','')}")
            else:
                row.set_title(path.name)
                row.set_subtitle("User-installed (not in catalogue)")

            remove_btn = Gtk.Button(label="Remove")
            remove_btn.set_valign(Gtk.Align.CENTER)
            remove_btn.get_style_context().add_class("destructive-action")
            remove_btn.connect("clicked", self._on_remove_clicked, path)
            row.add(remove_btn)
            group.add(row)

    def _refresh_installed_group(self) -> None:
        if self._installed_group is None:
            return
        for child in list(self._installed_group.get_children()):
            # PreferencesGroup uses an internal box — clearing rows
            self._installed_group.remove(child)
        self._fill_installed_group(self._installed_group)
        self._installed_group.show_all()

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
        for path, recipe in recipe_loader.list_recipes():
            row = Handy.ActionRow()
            row.set_title(recipe.get("tooltip") or recipe.get("name", path.stem))
            atype = (recipe.get("action") or {}).get("type", "?")
            template = (recipe.get("action") or {}).get("template", "")
            short = template if len(template) <= 70 else template[:67] + "…"
            row.set_subtitle(f"{atype}  ·  {short}")

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
        self._fill_custom_group_with_header(self._custom_group)
        self._custom_group.show_all()

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
            "Drag-free reordering: ↑ and ↓ buttons move a plugin up or down. "
            "The popup shows plugins in this order (those not listed fall back "
            "to their built-in priority)."
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

        for index, plugin in enumerate(sorted_plugins):
            row = Handy.ActionRow()
            row.set_title(plugin.tooltip or plugin.name)
            row.set_subtitle(plugin.name)
            try:
                img = Gtk.Image.new_from_icon_name(plugin.icon, Gtk.IconSize.LARGE_TOOLBAR)
                img.set_pixel_size(20)
                row.add_prefix(img)
            except Exception:
                pass

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
        self._fill_order_group(self._order_group)
        self._order_group.show_all()

    def _on_move(self, _btn, index: int, delta: int, sorted_plugins) -> None:
        """Swap index with index+delta; persist the new full ordering."""
        new_idx = index + delta
        if new_idx < 0 or new_idx >= len(sorted_plugins):
            return
        ordered_names = [p.name for p in sorted_plugins]
        ordered_names[index], ordered_names[new_idx] = (
            ordered_names[new_idx], ordered_names[index],
        )
        try:
            from settings import get_settings
            s = get_settings()
            s.set("plugin_order", ordered_names)
            s.save()
        except Exception as exc:  # noqa: BLE001
            print(f"[plugin_manager] could not save order: {exc}")
            return
        self._refresh_order_group()
        if self._on_changed:
            self._on_changed()

    def _rebuild_catalog_buttons(self) -> None:
        if self._catalog_group is None:
            return
        # Simplest: re-walk children and reset install buttons via filename
        # stored in the row title would be brittle — instead, rebuild the group.
        parent = self._catalog_group.get_parent()
        if parent is None:
            return
        parent.remove(self._catalog_group)
        new_group = self._build_catalog_group()
        parent.add(new_group)
        new_group.show_all()
        self._catalog_group = new_group
