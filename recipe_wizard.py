"""GTK dialog for creating or editing a recipe (no-code plugin).

Used by plugin_manager.py — instantiate, call run_and_get(), get either
the resulting recipe dict or None if the user cancelled.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

import recipe_loader
from icon_picker import IconPicker

_ACTION_TYPES = [
    ("open_url",         "Open a URL",
     "Opens a URL with the selection inserted (e.g. Wikipedia search)."),
    ("run_command",      "Run a shell command",
     "Runs a bash command with the selection. Output is not shown."),
    ("notify",           "Show a notification",
     "Pops up a desktop notification with the rendered template."),
    ("copy_transformed", "Transform and copy",
     "Renders the template and copies the result to the clipboard."),
]

_CONTENT_TYPES = [
    ("plain_text", "Plain text"),
    ("url",        "URL"),
    ("email",      "Email"),
    ("path",       "Path"),
    ("command",    "Shell command"),
]

# A small handful of common theme icons users can pick from. They can also
# type any other icon name (most theme icons work).
_COMMON_ICONS = [
    "applications-internet",
    "web-browser-symbolic",
    "system-search-symbolic",
    "mail-send-symbolic",
    "utilities-terminal-symbolic",
    "folder-open-symbolic",
    "accessories-text-editor-symbolic",
    "accessories-calculator-symbolic",
    "starred-symbolic",
    "emblem-favorite-symbolic",
    "emoji-people-symbolic",
    "view-list-symbolic",
    "format-text-bold-symbolic",
    "edit-copy-symbolic",
    "document-edit-symbolic",
]


class RecipeWizard:
    def __init__(self, parent=None, recipe: dict | None = None, source_path=None) -> None:
        self._parent = parent
        self._existing_name = (recipe or {}).get("name", "")
        self._recipe_in = recipe or {}
        self._source_path = source_path
        self._dialog: Gtk.Dialog | None = None

    def run_and_get(self) -> dict | None:
        dlg = Gtk.Dialog(
            title="Edit custom action" if self._recipe_in else "New custom action",
            transient_for=self._parent,
            flags=Gtk.DialogFlags.MODAL,
        )
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "Save", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.set_default_size(540, 560)
        dlg.set_icon_name("linuxpop")
        self._dialog = dlg

        content = dlg.get_content_area()
        content.set_spacing(8)

        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=16)

        def lbl(text):
            l = Gtk.Label(label=text, xalign=0)
            return l

        # --- Name (used as filename, must be safe) ----
        grid.attach(lbl("Internal name"), 0, 0, 1, 1)
        self._name_entry = Gtk.Entry()
        self._name_entry.set_text(self._recipe_in.get("name", ""))
        self._name_entry.set_placeholder_text("wikipedia-search")
        self._name_entry.set_tooltip_text(
            "Alphanumeric + hyphen/underscore. Becomes the filename."
        )
        grid.attach(self._name_entry, 1, 0, 1, 1)

        # --- Tooltip (button label) ---
        grid.attach(lbl("Button label"), 0, 1, 1, 1)
        self._tooltip_entry = Gtk.Entry()
        self._tooltip_entry.set_text(self._recipe_in.get("tooltip", ""))
        self._tooltip_entry.set_placeholder_text("Search Wikipedia")
        grid.attach(self._tooltip_entry, 1, 1, 1, 1)

        # --- Icon (entry + preview + Browse button that opens IconPicker) ---
        grid.attach(lbl("Icon"), 0, 2, 1, 1)
        icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._icon_entry = Gtk.Entry()
        self._icon_entry.set_text(self._recipe_in.get("icon", "applications-internet"))
        self._icon_entry.connect("changed", self._update_icon_preview)
        icon_box.pack_start(self._icon_entry, True, True, 0)
        self._icon_preview = Gtk.Image.new_from_icon_name(
            self._icon_entry.get_text(), Gtk.IconSize.LARGE_TOOLBAR,
        )
        icon_box.pack_start(self._icon_preview, False, False, 0)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.connect("clicked", self._on_browse_icons)
        icon_box.pack_start(browse_btn, False, False, 0)
        grid.attach(icon_box, 1, 2, 1, 1)

        # Quick-pick chips (small curated set under the entry)
        chips = Gtk.FlowBox()
        chips.set_max_children_per_line(8)
        chips.set_selection_mode(Gtk.SelectionMode.NONE)
        for name in _COMMON_ICONS:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            img = Gtk.Image.new_from_icon_name(name, Gtk.IconSize.LARGE_TOOLBAR)
            btn.add(img)
            btn.set_tooltip_text(name)
            btn.connect("clicked", lambda _b, n=name: self._icon_entry.set_text(n))
            chips.add(btn)
        scroll_chips = Gtk.ScrolledWindow()
        scroll_chips.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll_chips.set_min_content_height(60)
        scroll_chips.add(chips)
        grid.attach(scroll_chips, 1, 3, 1, 1)

        # --- Action type ---
        grid.attach(lbl("When clicked"), 0, 4, 1, 1)
        self._action_combo = Gtk.ComboBoxText()
        for key, label, _hint in _ACTION_TYPES:
            self._action_combo.append(key, label)
        current_type = (self._recipe_in.get("action") or {}).get("type", "open_url")
        self._action_combo.set_active_id(current_type)
        self._action_combo.connect("changed", self._update_hint)
        grid.attach(self._action_combo, 1, 4, 1, 1)

        # Action description (changes with the combo)
        self._action_hint = Gtk.Label(xalign=0)
        self._action_hint.set_line_wrap(True)
        self._action_hint.get_style_context().add_class("dim-label")
        grid.attach(self._action_hint, 1, 5, 1, 1)

        # --- Template (multi-line) ---
        grid.attach(lbl("Template"), 0, 6, 1, 1)
        tmpl_scroll = Gtk.ScrolledWindow()
        tmpl_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        tmpl_scroll.set_min_content_height(70)
        tmpl_scroll.set_shadow_type(Gtk.ShadowType.IN)
        self._template_view = Gtk.TextView()
        self._template_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._template_buffer = self._template_view.get_buffer()
        existing_tmpl = (self._recipe_in.get("action") or {}).get("template", "")
        self._template_buffer.set_text(existing_tmpl)
        tmpl_scroll.add(self._template_view)
        grid.attach(tmpl_scroll, 1, 6, 1, 1)

        # Clickable variable chips — clicking inserts the token at the cursor
        var_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        var_label = Gtk.Label(xalign=0)
        var_label.set_markup("<small>Click to insert at cursor:</small>")
        var_label.get_style_context().add_class("dim-label")
        var_box.pack_start(var_label, False, False, 0)

        var_chips = Gtk.FlowBox()
        var_chips.set_selection_mode(Gtk.SelectionMode.NONE)
        var_chips.set_max_children_per_line(6)
        var_chips.set_column_spacing(4)
        var_chips.set_row_spacing(4)
        for token in ("{text}", "{text_url}", "{text_shell}",
                      "{text_upper}", "{text_lower}", "{text_strip}"):
            chip = Gtk.Button(label=token)
            chip.get_style_context().add_class("flat")
            chip.set_tooltip_text(f"Insert {token} at cursor position")
            chip.connect("clicked", self._on_insert_token, token)
            var_chips.add(chip)
        var_box.pack_start(var_chips, False, False, 0)
        grid.attach(var_box, 1, 7, 1, 1)

        # --- Content types ---
        grid.attach(lbl("Show on"), 0, 8, 1, 1)
        types_box = Gtk.FlowBox()
        types_box.set_selection_mode(Gtk.SelectionMode.NONE)
        types_box.set_max_children_per_line(5)
        existing_types = set(self._recipe_in.get("content_types") or [])
        self._type_checks: dict[str, Gtk.CheckButton] = {}
        for key, label in _CONTENT_TYPES:
            cb = Gtk.CheckButton(label=label)
            cb.set_active(not existing_types or key in existing_types)
            self._type_checks[key] = cb
            types_box.add(cb)
        grid.attach(types_box, 1, 8, 1, 1)
        empty_note = Gtk.Label(xalign=0)
        empty_note.set_markup("<small>Unchecking all = button always available.</small>")
        empty_note.get_style_context().add_class("dim-label")
        grid.attach(empty_note, 1, 9, 1, 1)

        content.add(grid)
        self._update_hint()
        dlg.show_all()
        self._template_view.grab_focus() if existing_tmpl else self._name_entry.grab_focus()

        response = dlg.run()
        result = None
        if response == Gtk.ResponseType.OK:
            result = self._collect()
            if result is None:
                # Validation failed — keep dialog open isn't trivial with .run(),
                # so just inform the user via a sub-dialog and return None.
                self._show_validation_error()
        dlg.destroy()
        self._dialog = None
        return result

    def _update_icon_preview(self, _entry) -> None:
        self._icon_preview.set_from_icon_name(
            self._icon_entry.get_text(), Gtk.IconSize.LARGE_TOOLBAR,
        )

    def _on_browse_icons(self, _btn) -> None:
        picker = IconPicker(parent=self._dialog, initial=self._icon_entry.get_text())
        chosen = picker.run()
        if chosen:
            self._icon_entry.set_text(chosen)

    def _on_insert_token(self, _btn, token: str) -> None:
        """Insert `token` at the current cursor position in the template
        TextView, then refocus the view so the user can keep typing."""
        # Replace any selection, otherwise insert at the cursor mark
        if self._template_buffer.get_has_selection():
            self._template_buffer.delete_selection(True, True)
        self._template_buffer.insert_at_cursor(token)
        self._template_view.grab_focus()

    def _update_hint(self, *_) -> None:
        key = self._action_combo.get_active_id()
        for k, _l, hint in _ACTION_TYPES:
            if k == key:
                self._action_hint.set_text(hint)
                return

    def _get_template_text(self) -> str:
        start = self._template_buffer.get_start_iter()
        end = self._template_buffer.get_end_iter()
        return self._template_buffer.get_text(start, end, True)

    def _collect(self) -> dict | None:
        name = self._name_entry.get_text().strip()
        tooltip = self._tooltip_entry.get_text().strip() or name
        icon = self._icon_entry.get_text().strip() or "applications-other"
        atype = self._action_combo.get_active_id() or "open_url"
        template = self._get_template_text().strip()
        types = [k for k, cb in self._type_checks.items() if cb.get_active()]

        recipe = {
            "name": name,
            "tooltip": tooltip,
            "icon": icon,
            "content_types": types,
            "action": {
                "type": atype,
                "template": template,
            },
        }
        errors = recipe_loader.validate(recipe)
        self._last_errors = errors
        if errors:
            return None
        return recipe

    def _show_validation_error(self) -> None:
        errors = getattr(self, "_last_errors", ["Unknown error"])
        msg = Gtk.MessageDialog(
            transient_for=self._parent,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.OK,
            text="Couldn't save the recipe",
        )
        msg.format_secondary_text("\n".join(errors))
        msg.run()
        msg.destroy()
