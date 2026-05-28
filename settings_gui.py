"""GTK + libhandy preferences window for LinuxPop.

Uses Hdy.PreferencesWindow with grouped action rows instead of the legacy
Gtk.Dialog + grid layout - gives a modern GNOME-Settings-style boxed-list UI
without migrating to GTK4.

Apply-on-change semantics: edits save immediately, no Save/Cancel buttons.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gdk, GLib, Gtk, Handy, Pango  # noqa: E402

# Some PyGObject builds don't expose Gdk.X11 as a top-level submodule -
# attempt to require it so Gdk.X11.get_server_time is available.
try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: F401, E402
except (ImportError, ValueError):
    pass

from settings import get_settings

Handy.init()


def _unwrap_subtitle_labels(root: Gtk.Widget) -> None:
    """Walk every label under `root` and let any HdyActionRow / HdyPreferencesRow
    subtitle wrap onto multiple lines instead of ellipsising.

    libhandy renders the subtitle as a Gtk.Label with style class ``subtitle``
    and ``ellipsize=END``, ``lines=1`` baked in - there's no public API to
    flip that off per row. The only way to keep the descriptions readable
    in a narrow dialog is to walk the realised widget tree after show_all()
    and patch each subtitle label directly.

    Also re-runs once on idle: HdyPreferencesGroup re-creates / re-styles
    rows during its own first allocation pass, which would replace the
    labels we just patched.
    """
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
                    # Allow Pango to use as many lines as it needs.
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


def _open_text_editor_modal(
    parent: Gtk.Window | None,
    title: str,
    subtitle: str,
    initial_text: str,
    placeholder_text: str,
) -> str | None:
    """Pop a modal editor with a big multi-line TextView, Save / Cancel.
    Returns the new text on Save, None on Cancel. Used for blocklists,
    snippet variables - things that need a whole textarea but shouldn't
    occupy permanent space in the Settings page when empty."""
    dlg = Gtk.Dialog(title=title, transient_for=parent, flags=0)
    dlg.set_default_size(580, 420)
    dlg.set_icon_name("linuxpop")
    dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                    "Save", Gtk.ResponseType.OK)
    dlg.set_default_response(Gtk.ResponseType.OK)

    content = dlg.get_content_area()
    content.set_spacing(8)
    content.set_margin_top(12)
    content.set_margin_bottom(12)
    content.set_margin_start(14)
    content.set_margin_end(14)

    sub_lbl = Gtk.Label(xalign=0)
    sub_lbl.set_line_wrap(True)
    sub_lbl.set_markup(f"<small>{GLib.markup_escape_text(subtitle)}</small>")
    sub_lbl.get_style_context().add_class("dim-label")
    content.add(sub_lbl)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    scroll.set_hexpand(True)
    scroll.set_vexpand(True)
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.NONE)
    try:
        view.set_monospace(True)
    except AttributeError:
        pass
    view.get_style_context().add_class("lp-cmd-edit")
    if initial_text:
        view.get_buffer().set_text(initial_text)
    _attach_textarea_placeholder(view, placeholder_text)
    scroll.add(view)
    content.pack_start(scroll, True, True, 0)

    dlg.show_all()
    view.grab_focus()
    response = dlg.run()
    buf = view.get_buffer()
    s, e = buf.get_bounds()
    raw = buf.get_text(s, e, True)
    is_placeholder = getattr(view, "_placeholder_active", False)
    dlg.destroy()
    if response != Gtk.ResponseType.OK:
        return None
    return "" if is_placeholder else raw


def _attach_textarea_placeholder(view: Gtk.TextView, placeholder_text: str) -> None:
    """Give a Gtk.TextView the placeholder-text behaviour that Gtk.Entry
    has built-in. When the buffer is empty the placeholder shows in
    grey italics; focus or any typing wipes it. Empty state is restored
    on focus-out so the user always sees the hint when the field is blank.

    Save handlers MUST check view._placeholder_active and treat the
    placeholder text as "no value" - otherwise the placeholder string
    would be persisted as data.
    """
    buf = view.get_buffer()
    # Per-view tag so callers can have multiple placeholders without
    # name collisions in the buffer's tag table.
    tag = buf.create_tag(
        f"lp-placeholder-{id(view)}",
        foreground="#7a8090",
        style=Pango.Style.ITALIC,
    )
    view._placeholder_active = False

    def _set_placeholder() -> None:
        # Suppress the 'changed' signal during the placeholder swap so
        # debounced save handlers don't fire on what is really UI chrome.
        view._setting_placeholder = True
        buf.set_text(placeholder_text)
        s, e = buf.get_bounds()
        buf.apply_tag(tag, s, e)
        view._placeholder_active = True
        view._setting_placeholder = False

    s, e = buf.get_bounds()
    if not buf.get_text(s, e, True):
        _set_placeholder()

    def _on_focus_in(_w, _e):
        if view._placeholder_active:
            view._setting_placeholder = True
            buf.set_text("")
            view._placeholder_active = False
            view._setting_placeholder = False
        return False

    def _on_focus_out(_w, _e):
        s, e = buf.get_bounds()
        if not buf.get_text(s, e, True).strip():
            _set_placeholder()
        return False

    view.connect("focus-in-event", _on_focus_in)
    view.connect("focus-out-event", _on_focus_out)


def _force_to_front(window: Gtk.Window) -> None:
    """Raise the window to the foreground on X11 even when WM focus-
    stealing prevention or another LinuxPop dialog's keep_above would
    otherwise rank it lower in the stack."""
    try:
        window.deiconify()
        gdk_win = window.get_window()
        if gdk_win is not None:
            try:
                ts = Gdk.X11.get_server_time(gdk_win)
            except Exception:
                ts = Gtk.get_current_event_time() or 0
            window.present_with_time(ts)
            # Explicit X11 raise bypasses focus arbitration -- needed
            # because the clipboard picker holds permanent keep_above
            # and would otherwise float over us.
            try:
                gdk_win.raise_()
            except Exception:
                pass
        else:
            window.present()
        # Quick keep-above toggle nudges most WMs into raising it.
        window.set_keep_above(True)
        GLib.timeout_add(150, lambda: (window.set_keep_above(False), False)[1])
        window.present()
    except Exception:
        try:
            window.present()
        except Exception:
            pass


_MODIFIER_KEYVALS = {
    Gdk.KEY_Control_L, Gdk.KEY_Control_R,
    Gdk.KEY_Shift_L, Gdk.KEY_Shift_R,
    Gdk.KEY_Alt_L, Gdk.KEY_Alt_R,
    Gdk.KEY_Super_L, Gdk.KEY_Super_R,
    Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
    Gdk.KEY_Hyper_L, Gdk.KEY_Hyper_R,
}


def _format_combo(keyval: int, state: int) -> str:
    parts = []
    if state & Gdk.ModifierType.CONTROL_MASK:
        parts.append("ctrl")
    if state & Gdk.ModifierType.MOD1_MASK:
        parts.append("alt")
    if state & Gdk.ModifierType.SHIFT_MASK:
        parts.append("shift")
    if state & Gdk.ModifierType.MOD4_MASK:
        parts.append("super")
    name = Gdk.keyval_name(keyval) or ""
    parts.append(name.lower())
    return "+".join(parts)


class HotkeyRecorder(Gtk.Button):
    """Compact button that captures a keypress combo when clicked."""

    def __init__(self, initial: str = "", on_changed: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._value = initial
        self._recording = False
        self._on_changed = on_changed
        self.set_valign(Gtk.Align.CENTER)
        self._update_label()
        self.connect("clicked", self._on_clicked)
        self.connect("key-press-event", self._on_key_press)
        self.set_can_focus(True)

    def get_value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        if self._value == value:
            return
        self._value = value
        self._recording = False
        self._update_label()
        if self._on_changed:
            self._on_changed(value)

    def _update_label(self) -> None:
        if self._recording:
            self.set_label("⏺  Press a hotkey  (Esc to cancel)")
        elif self._value:
            self.set_label(self._value)
        else:
            self.set_label("Click to record…")

    def _on_clicked(self, *_):
        self._recording = True
        self._update_label()
        self.grab_focus()

    def _on_key_press(self, _widget, event):
        if not self._recording:
            return False
        plain_esc = (
            event.keyval == Gdk.KEY_Escape
            and not (event.state & (
                Gdk.ModifierType.CONTROL_MASK
                | Gdk.ModifierType.MOD1_MASK
                | Gdk.ModifierType.MOD4_MASK
            ))
        )
        if plain_esc:
            self._recording = False
            self._update_label()
            return True
        if event.keyval in _MODIFIER_KEYVALS:
            return True
        self.set_value(_format_combo(event.keyval, event.state))
        return True


class SettingsDialog:
    def __init__(self, on_changed: Callable[[], None] | None = None) -> None:
        self._on_changed = on_changed
        self._settings = get_settings()
        self._window: Handy.PreferencesWindow | None = None

    def show(self) -> None:
        if self._window is not None and self._window.get_visible():
            _force_to_front(self._window)
            return

        win = Handy.PreferencesWindow()
        win.set_title("LinuxPop")
        win.set_search_enabled(False)
        win.set_default_size(560, 600)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_modal(False)
        win.connect("destroy", self._on_destroy)

        page = Handy.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")

        # Snippets & Clipboard first - that's the core of what people use
        # LinuxPop for. Everything else is supporting infrastructure.
        page.add(self._build_snippets_clipboard_group())
        page.add(self._build_appearance_group())
        page.add(self._build_activation_group())
        page.add(self._build_timing_group())
        page.add(self._build_filter_group())
        page.add(self._build_search_group())
        page.add(self._build_terminal_group())
        page.add(self._build_ai_group())
        page.add(self._build_advanced_group())
        # Donation entry-points live in the tray menu, the About dialog
        # and the first-run welcome - see welcome.open_support_picker.
        # Settings stays focused on configuration.

        win.add(page)
        win.show_all()
        # Subtitle wrap must be patched AFTER show_all() so the realised
        # label widgets exist to flip.
        _unwrap_subtitle_labels(win)
        self._window = win
        _force_to_front(win)

    def _on_destroy(self, *_):
        self._window = None

    # ---- groups --------------------------------------------------------------

    def _build_appearance_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Appearance")

        theme_row = Handy.ActionRow()
        theme_row.set_title("Theme")
        theme_row.set_subtitle(
            "Dark uses the cobalt + violet palette. Light gives you the "
            "same layout on a clean off-white. 'Follow system' picks one "
            "based on your desktop theme.")
        theme_combo = Gtk.ComboBoxText()
        theme_combo.set_valign(Gtk.Align.CENTER)
        theme_options = [
            ("dark",   "Dark"),
            ("light",  "Light"),
            ("system", "Follow system"),
        ]
        for key, label in theme_options:
            theme_combo.append(key, label)
        current = self._settings.get("theme", "dark") or "dark"
        theme_combo.set_active_id(current)

        def _on_theme_changed(_combo):
            mode = theme_combo.get_active_id() or "dark"
            self._save_key("theme", mode)
            try:
                import theme as _theme
                _theme.install_premium_theme(mode)
            except Exception as exc:
                print(f"[settings] theme reload failed: {exc}")
            # Popup uses its own CSS provider; rebuild it so the
            # selection popup picks up the new palette too.
            try:
                import popup as _popup
                _popup.reinstall_popup_css()
            except Exception as exc:
                print(f"[settings] popup theme reload failed: {exc}")
        theme_combo.connect("changed", _on_theme_changed)
        theme_row.add(theme_combo)
        theme_row.set_activatable_widget(theme_combo)
        group.add(theme_row)

        # Popup button size: how big each action chip in the floating
        # popup is. Clamped to [14, 48] pixels - smaller is unreadable,
        # bigger eats too much screen.
        size_row = Handy.ActionRow()
        size_row.set_title("Popup button size")
        size_row.set_subtitle(
            "How big each action button in the popup is, in pixels. "
            "14 is small and dense; 48 is large and easy to click.")
        size_adj = Gtk.Adjustment(
            value=int(self._settings.get("popup_button_size", 22) or 22),
            lower=14, upper=48, step_increment=1, page_increment=4,
        )
        size_spin = Gtk.SpinButton()
        size_spin.set_valign(Gtk.Align.CENTER)
        size_spin.set_adjustment(size_adj)
        size_spin.set_numeric(True)

        def _on_size_changed(spin: Gtk.SpinButton) -> None:
            self._save_key("popup_button_size", int(spin.get_value()))
            # Live-apply: rebuild the popup's CSS provider so the next
            # show_for() renders at the new size.
            try:
                import popup as _popup
                _popup.reinstall_popup_css()
            except Exception as exc:
                print(f"[settings] popup css reload failed: {exc}")
        size_spin.connect("value-changed", _on_size_changed)
        size_row.add(size_spin)
        size_row.set_activatable_widget(size_spin)
        group.add(size_row)

        return group

    def _build_activation_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Activation")
        group.set_description("How LinuxPop appears.")

        # Popup hotkey row. Lives here rather than in a dedicated Hotkeys
        # group because, with the clipboard shortcut moved to Snippets &
        # Clipboard, there's only one keyboard shortcut left and it
        # belongs alongside the other "how do I summon the popup" toggles.
        hk_row = Handy.ActionRow()
        hk_row.set_title("Popup hotkey")
        hk_row.set_subtitle(
            "Press this anywhere to open the popup. If you have text "
            "highlighted, it shows actions for that text. If not, it "
            "shows a paste menu instead.")
        recorder = HotkeyRecorder(
            self._settings.get("hotkey") or "",
            on_changed=lambda v: self._save_key("hotkey", v),
        )
        hk_row.add(recorder)
        clear = Gtk.Button.new_from_icon_name(
            "edit-clear-symbolic", Gtk.IconSize.BUTTON)
        clear.set_valign(Gtk.Align.CENTER)
        clear.set_tooltip_text("Disable hotkey")
        clear.connect("clicked", lambda *_: recorder.set_value(""))
        hk_row.add(clear)
        group.add(hk_row)

        # Auto-popup on selection (switch row)
        sel_row = Handy.ActionRow()
        sel_row.set_title("Auto-popup on selection")
        sel_row.set_subtitle(
            "Show the popup automatically whenever you highlight text in any app.")
        sel_switch = Gtk.Switch()
        sel_switch.set_valign(Gtk.Align.CENTER)
        sel_switch.set_active(bool(self._settings.get("show_on_selection")))
        sel_switch.connect("notify::active", self._on_switch, "show_on_selection")
        sel_row.add(sel_switch)
        sel_row.set_activatable_widget(sel_switch)
        group.add(sel_row)

        # Double-click in an empty editable field shows the edit menu
        # (Paste / Select all / Backspace). PopClip's text-field gesture.
        dbl_row = Handy.ActionRow()
        dbl_row.set_title("Modifier+double-click for the edit menu")
        dbl_row.set_subtitle(
            "Hold the modifier key below and double-click inside any "
            "text field to bring up Paste / Select all / Backspace at "
            "the cursor. The modifier is required so it never collides "
            "with the app's own double-click-to-select-a-word gesture. "
            "Requires LinuxPop to watch mouse clicks globally - "
            "nothing is logged or sent.")
        dbl_switch = Gtk.Switch()
        dbl_switch.set_valign(Gtk.Align.CENTER)
        dbl_switch.set_active(
            bool(self._settings.get("double_click_popup_enabled", False)))
        dbl_switch.connect(
            "notify::active", self._on_switch, "double_click_popup_enabled")
        # Live-apply happens through the settings on_changed callback in
        # main.py - no separate plumbing needed here.
        dbl_row.add(dbl_switch)
        dbl_row.set_activatable_widget(dbl_switch)
        group.add(dbl_row)

        mod_row = Handy.ActionRow()
        mod_row.set_title("Modifier key")
        mod_row.set_subtitle(
            "Which key must be held during the double-click. "
            "Ctrl is the safest default; pick another if Ctrl is "
            "already mapped to something you do with the mouse.")
        mod_combo = Gtk.ComboBoxText()
        mod_combo.set_valign(Gtk.Align.CENTER)
        for key, label in [
            ("ctrl",  "Ctrl"),
            ("shift", "Shift"),
            ("alt",   "Alt"),
            ("super", "Super (Windows key)"),
        ]:
            mod_combo.append(key, label)
        current_mod = (self._settings.get("double_click_modifier") or "ctrl").lower()
        mod_combo.set_active_id(current_mod)
        mod_combo.connect(
            "changed",
            lambda c: self._save_key(
                "double_click_modifier", c.get_active_id() or "ctrl"))
        mod_row.add(mod_combo)
        mod_row.set_activatable_widget(mod_combo)
        # Sensitive only when the toggle above is on, so it's obvious
        # the picker is doing nothing while the feature itself is off.
        mod_row.set_sensitive(
            bool(self._settings.get("double_click_popup_enabled", False)))
        dbl_switch.connect(
            "notify::active",
            lambda s, _p: mod_row.set_sensitive(s.get_active()))
        group.add(mod_row)

        # Autostart at login (switch row) - driven by ~/.config/autostart
        # rather than a settings.json key, since the .desktop file is what
        # the DE actually reads. We just toggle the file from here.
        auto_row = Handy.ActionRow()
        auto_row.set_title("Start at login")
        auto_row.set_subtitle("Launch LinuxPop automatically when you sign in.")
        auto_switch = Gtk.Switch()
        auto_switch.set_valign(Gtk.Align.CENTER)
        try:
            import autostart
            auto_switch.set_active(autostart.is_enabled())
            def _on_auto_toggle(sw, _p):
                import autostart as _as
                ok = _as.set_enabled(sw.get_active())
                if not ok:
                    # Revert the visual if writing failed
                    sw.set_active(not sw.get_active())
            auto_switch.connect("notify::active", _on_auto_toggle)
        except Exception:
            auto_switch.set_sensitive(False)
        auto_row.add(auto_switch)
        auto_row.set_activatable_widget(auto_switch)
        group.add(auto_row)

        return group

    def _build_snippets_clipboard_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Snippets & Clipboard")
        group.set_description(
            "The picker that remembers what you've copied and the snippets "
            "you save for reuse. Most of LinuxPop lives here.")

        # Clipboard plugin master toggle. When off, the background
        # selection-watcher thread is not started, the picker hotkey
        # doesn't bind, and the popup button is hidden.
        clip_master_row = Handy.ActionRow()
        clip_master_row.set_title("Clipboard history")
        clip_master_row.set_subtitle(
            "Remember things you've recently copied so you can paste them "
            "later. Turn off to stop LinuxPop from watching the clipboard.")
        clip_master_switch = Gtk.Switch()
        clip_master_switch.set_valign(Gtk.Align.CENTER)
        clip_master_switch.set_active(
            bool(self._settings.get("clipboard_history_enabled", True)))
        clip_master_switch.connect(
            "notify::active", self._on_switch, "clipboard_history_enabled")
        clip_master_row.add(clip_master_switch)
        clip_master_row.set_activatable_widget(clip_master_switch)
        group.add(clip_master_row)

        # Clipboard-picker hotkey row -- only meaningful if the master
        # switch above is on, but we keep it visible so the binding can
        # be configured ahead of enabling.
        clip_row = Handy.ActionRow()
        clip_row.set_title("Optional clipboard shortcut")
        clip_row.set_subtitle(
            "A direct shortcut that opens the clipboard picker at the "
            "cursor. You don't have to set one - the popup hotkey above "
            "already gets you there when nothing is highlighted.")
        clip_recorder = HotkeyRecorder(
            self._settings.get("clipboard_hotkey") or "",
            on_changed=lambda v: self._save_key("clipboard_hotkey", v),
        )
        clip_row.add(clip_recorder)
        clip_clear = Gtk.Button.new_from_icon_name("edit-clear-symbolic", Gtk.IconSize.BUTTON)
        clip_clear.set_valign(Gtk.Align.CENTER)
        clip_clear.set_tooltip_text("Disable")
        clip_clear.connect("clicked", lambda *_: clip_recorder.set_value(""))
        clip_row.add(clip_clear)
        clip_row.set_sensitive(
            bool(self._settings.get("clipboard_history_enabled", True)))
        # Re-flow sensitivity when the master toggles
        clip_master_switch.connect(
            "notify::active",
            lambda s, _p: clip_row.set_sensitive(s.get_active()))
        group.add(clip_row)

        # Snippet triggers (text expansion). Off by default - turning it
        # on means LinuxPop attaches to the X11 RECORD extension and sees
        # every keystroke on the desktop. Be honest about that in the
        # subtitle so the user can decide.
        trigger_row = Handy.ActionRow()
        trigger_row.set_title("Snippet triggers (text expansion)")
        trigger_row.set_subtitle(
            "When ON, typing a snippet's trigger code (e.g. ';email') followed "
            "by space or tab auto-expands it. Requires LinuxPop to watch "
            "global keystrokes locally - keys are matched against your "
            "snippet triggers only, never logged or sent anywhere.")
        trigger_switch = Gtk.Switch()
        trigger_switch.set_valign(Gtk.Align.CENTER)
        trigger_switch.set_active(
            bool(self._settings.get("snippet_triggers_enabled", False)))
        trigger_switch.connect(
            "notify::active", self._on_switch, "snippet_triggers_enabled")
        trigger_switch.connect(
            "notify::active", lambda *_: self._apply_trigger_toggle())
        trigger_row.add(trigger_switch)
        trigger_row.set_activatable_widget(trigger_switch)
        trigger_row.set_sensitive(
            bool(self._settings.get("clipboard_history_enabled", True)))
        clip_master_switch.connect(
            "notify::active",
            lambda s, _p: trigger_row.set_sensitive(s.get_active()))
        group.add(trigger_row)

        # Per-app/site blocklist for trigger expansion - opens a modal
        # editor instead of taking permanent room in the page.
        tblock_row = Handy.ActionRow()
        tblock_row.set_activatable(True)

        def _refresh_tblock_row() -> None:
            patterns = list(self._settings.get("trigger_blocklist_patterns") or [])
            tblock_row.set_title("Don't expand triggers in these apps or sites")
            count = len(patterns)
            if count == 0:
                tblock_row.set_subtitle(
                    "Useful for password fields, terminals, and "
                    "security-sensitive sites. Click to add patterns.")
            else:
                examples = ", ".join(patterns[:3])
                if count > 3:
                    examples += f", +{count - 3} more"
                tblock_row.set_subtitle(
                    f"{count} pattern{'s' if count != 1 else ''} blocked: "
                    f"{examples}. Click to edit.")
        _refresh_tblock_row()

        def _on_tblock_edit(_row, _gesture=None) -> None:
            patterns = list(self._settings.get("trigger_blocklist_patterns") or [])
            initial = "\n".join(patterns)
            new_text = _open_text_editor_modal(
                parent=self._window,
                title="Edit trigger blocklist",
                subtitle=(
                    "Apps and sites where snippet triggers should NEVER "
                    "auto-expand. Useful for password managers, terminals, "
                    "and banking sites - places where typing 'rraak' is "
                    "supposed to stay 'rraak'."),
                initial_text=initial,
                placeholder_text=(
                    "Type one app or site per line. Each line is matched "
                    "(case-insensitive) against the focused window's "
                    "title and class - if any line is a substring of "
                    "either, expansion is skipped for that window.\n\n"
                    "Try things like:\n"
                    "  KeePassXC        - the password manager\n"
                    "  gnome-terminal   - any terminal you use\n"
                    "  bank.no          - matches whatever your bank's\n"
                    "                     site name shows in the title\n"
                    "  1Password\n"
                    "  Bitwarden"),
            )
            if new_text is None:
                return
            new_patterns = [
                line.strip() for line in new_text.splitlines() if line.strip()
            ]
            self._save_key("trigger_blocklist_patterns", new_patterns)
            _refresh_tblock_row()

        tblock_row.connect("activated", _on_tblock_edit)
        edit_arrow = Gtk.Image.new_from_icon_name(
            "document-edit-symbolic", Gtk.IconSize.BUTTON)
        edit_arrow.set_valign(Gtk.Align.CENTER)
        tblock_row.add(edit_arrow)
        group.add(tblock_row)

        # Shared snippet variables: a key=value editor that backs the
        # {var:NAME} placeholder.
        vars_row = Handy.ActionRow()
        vars_row.set_activatable(True)

        def _parse_vars_text(raw: str) -> dict:
            out: dict[str, str] = {}
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                name, _, value = line.partition("=")
                name = name.strip()
                if not name:
                    continue
                out[name] = value.strip()
            return out

        def _refresh_vars_row() -> None:
            existing = self._settings.get("snippet_variables") or {}
            if not isinstance(existing, dict):
                existing = {}
            vars_row.set_title("Snippet variables")
            count = len(existing)
            if count == 0:
                vars_row.set_subtitle(
                    "Define your email, signature, phone, etc. once and "
                    "use {var:name} across snippets. Click to add.")
            else:
                names = sorted(existing.keys())
                preview = ", ".join(f"{{var:{n}}}" for n in names[:3])
                if count > 3:
                    preview += f", +{count - 3} more"
                vars_row.set_subtitle(
                    f"{count} variable{'s' if count != 1 else ''} defined: "
                    f"{preview}. Click to edit.")
        _refresh_vars_row()

        def _on_vars_edit(_row, _gesture=None) -> None:
            existing = self._settings.get("snippet_variables") or {}
            if isinstance(existing, dict):
                initial = "\n".join(
                    f"{k} = {v}" for k, v in sorted(existing.items())
                )
            else:
                initial = ""
            new_text = _open_text_editor_modal(
                parent=self._window,
                title="Edit snippet variables",
                subtitle=(
                    "Reusable values that any snippet can pull in. "
                    "Define a value once here, then write {var:name} "
                    "in any snippet to drop it in. Change the value "
                    "later and every snippet that uses it updates "
                    "automatically."),
                initial_text=initial,
                placeholder_text=(
                    "Type one variable per line in the form\n"
                    "    name = value\n\n"
                    "Then write {var:name} in your snippets to pull the "
                    "value in. Common ones to start with:\n\n"
                    "  email     = you@example.com\n"
                    "  signature = Best,\\nAlex\n"
                    "  phone     = +47 555 1234\n"
                    "  company   = Acme AS\n"
                    "  address   = Karl Johans gate 1, 0154 Oslo\n\n"
                    "Names can be anything you'd recognise later -\n"
                    "letters, digits, underscores, hyphens."),
            )
            if new_text is None:
                return
            self._save_key("snippet_variables", _parse_vars_text(new_text))
            _refresh_vars_row()

        vars_row.connect("activated", _on_vars_edit)
        vars_arrow = Gtk.Image.new_from_icon_name(
            "document-edit-symbolic", Gtk.IconSize.BUTTON)
        vars_arrow.set_valign(Gtk.Align.CENTER)
        vars_row.add(vars_arrow)
        group.add(vars_row)

        # Shell extension {shell:CMD} in snippets. Off by default - same
        # threat model as enabling macros: a hostile imported snippet
        # with a {shell:rm -rf ~} runs immediately when expanded.
        shell_row = Handy.ActionRow()
        shell_row.set_title("Shell expansion in snippets")
        shell_row.set_subtitle(
            "When ON, snippets containing {shell:CMD} run that command "
            "in bash and paste the output. Useful for {shell:git branch} "
            "or {shell:date -u}. Off by default - an imported snippet "
            "with a hostile command would execute immediately.")
        shell_switch = Gtk.Switch()
        shell_switch.set_valign(Gtk.Align.CENTER)
        shell_switch.set_active(
            bool(self._settings.get("snippet_shell_enabled", False)))
        shell_switch.connect(
            "notify::active", self._on_switch, "snippet_shell_enabled")
        shell_row.add(shell_switch)
        shell_row.set_activatable_widget(shell_switch)
        shell_row.set_sensitive(
            bool(self._settings.get("clipboard_history_enabled", True)))
        clip_master_switch.connect(
            "notify::active",
            lambda s, _p: shell_row.set_sensitive(s.get_active()))
        group.add(shell_row)

        return group

    def _build_timing_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Timing")
        group.set_description("Seconds for popup show / hide behaviour.")

        debounce_row = self._seconds_spin_row(
            "Delay before popup appears",
            "Seconds to wait after you finish selecting - keeps the "
            "popup from flashing while you're still dragging.",
            "selection_debounce_ms", 0.0, 2.0, 0.05,
        )
        initial_row = self._seconds_spin_row(
            "Auto-hide before mouse arrives",
            "Seconds the popup waits if you never move the mouse to it.",
            "auto_hide_initial_ms", 0.5, 30.0, 0.5,
        )
        leave_row = self._seconds_spin_row(
            "Auto-hide after mouse leaves",
            "Seconds the popup stays visible after you move the cursor "
            "away from it.",
            "auto_hide_leave_ms", 0.2, 20.0, 0.2,
        )
        group.add(debounce_row)
        group.add(initial_row)
        group.add(leave_row)
        return group

    def _build_filter_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Filters")
        group.set_description("When the popup should stay hidden.")

        # Minimum-length filter is a two-part control: a toggle (default
        # off, matches PopClip) gates whether the auto-popup ignores
        # short selections at all, and a spinner under it picks the
        # threshold. The spinner only goes 'live' when the toggle is on.
        min_toggle_row = Handy.ActionRow()
        min_toggle_row.set_title("Skip short auto-popup selections")
        min_toggle_row.set_subtitle(
            "If on, the auto-popup ignores selections shorter than the "
            "number below - useful if you keep getting popups for "
            "single-character misclicks. The hotkey is unaffected and "
            "always opens the popup, even with no text selected.")
        min_switch = Gtk.Switch()
        min_switch.set_valign(Gtk.Align.CENTER)
        min_switch.set_active(
            bool(self._settings.get("min_selection_length_enabled")))
        min_switch.connect(
            "notify::active", self._on_switch, "min_selection_length_enabled")
        min_toggle_row.add(min_switch)
        min_toggle_row.set_activatable_widget(min_switch)
        group.add(min_toggle_row)

        min_row = self._spin_row(
            "Minimum characters",
            "Selections shorter than this are skipped by the "
            "auto-popup (only when the switch above is on).",
            "min_selection_length", 1, 100, 1,
        )
        min_row.set_sensitive(min_switch.get_active())
        min_switch.connect(
            "notify::active",
            lambda s, _p: min_row.set_sensitive(s.get_active()))
        group.add(min_row)

        # Blocklist: one substring per line. Matches against the active
        # window's title and WM_CLASS (case-insensitive) at popup time.
        # Stored in settings.json as a list of strings; the UI gives a
        # plain multi-line text area because per-row HdyActionRows would
        # be overkill for a free-form list.
        block_row = Handy.ActionRow()
        block_row.set_activatable(True)

        def _refresh_block_row() -> None:
            patterns = list(self._settings.get("blocklist_patterns") or [])
            block_row.set_title("Don't show in these apps or pages")
            count = len(patterns)
            if count == 0:
                block_row.set_subtitle(
                    "Hide the popup in password managers, banking sites, "
                    "and anywhere else you'd rather not see it. Click to add.")
            else:
                examples = ", ".join(patterns[:3])
                if count > 3:
                    examples += f", +{count - 3} more"
                block_row.set_subtitle(
                    f"{count} pattern{'s' if count != 1 else ''} blocked: "
                    f"{examples}. Click to edit.")
        _refresh_block_row()

        def _on_block_edit(_row, _gesture=None) -> None:
            patterns = list(self._settings.get("blocklist_patterns") or [])
            initial = "\n".join(patterns)
            new_text = _open_text_editor_modal(
                parent=self._window,
                title="Edit popup blocklist",
                subtitle=(
                    "Apps and pages where the popup should never appear. "
                    "Useful for password managers, banking sites, and any "
                    "other context where a floating menu would be in "
                    "the way."),
                initial_text=initial,
                placeholder_text=(
                    "Type one app or site per line. Each line is matched "
                    "(case-insensitive) against the focused window's "
                    "title and class - if any line is a substring of "
                    "either, the popup stays hidden for that window.\n\n"
                    "Try things like:\n"
                    "  KeePassXC\n"
                    "  1Password\n"
                    "  Bitwarden\n"
                    "  Mozilla Firefox - DNB     - just the bank tab\n"
                    "  - kjøp                    - any browser tab whose\n"
                    "                              title contains this"),
            )
            if new_text is None:
                return
            new_patterns = [
                line.strip() for line in new_text.splitlines() if line.strip()
            ]
            self._save_key("blocklist_patterns", new_patterns)
            _refresh_block_row()

        block_row.connect("activated", _on_block_edit)
        block_arrow = Gtk.Image.new_from_icon_name(
            "document-edit-symbolic", Gtk.IconSize.BUTTON)
        block_arrow.set_valign(Gtk.Align.CENTER)
        block_row.add(block_arrow)
        group.add(block_row)
        return group

    def _build_ai_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("AI services")
        group.set_description(
            "Which chat-AI buttons the popup shows. Toggle off any you don't use. "
            "Requires the 'Send to chat AI' plugin to be installed."
        )

        services = [
            ("claude",     "Claude",            "linuxpop-claude",       "claude.ai · paste mode"),
            ("chatgpt",    "ChatGPT",           "linuxpop-chatgpt",      "chatgpt.com · URL prefill (you press Enter)"),
            ("gemini",     "Gemini",            "linuxpop-gemini",       "gemini.google.com · paste mode"),
            ("perplexity", "Perplexity",        "linuxpop-perplexity",   "perplexity.ai · URL search (auto-submits)"),
            ("google_ai",  "Google AI Search",  "linuxpop-google-ai",    "google.com/search?udm=50 · URL search (auto-submits)"),
        ]
        current = list(self._settings.get("ai_services") or [])

        for key, label, icon_name, host in services:
            row = Handy.ActionRow()
            row.set_title(label)
            row.set_subtitle(host)
            # Colored brand icon on the left
            try:
                img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
                img.set_pixel_size(28)
                row.add_prefix(img)
            except Exception:
                pass
            sw = Gtk.Switch()
            sw.set_valign(Gtk.Align.CENTER)
            sw.set_active(key in current)
            sw.connect("notify::active", self._on_ai_toggle, key)
            row.add(sw)
            row.set_activatable_widget(sw)
            group.add(row)

        # Auto-submit after paste. Speeds up the paste-mode services
        # (Claude, Gemini) by sending Return automatically, so the user
        # doesn't have to press Enter themselves.
        submit_row = Handy.ActionRow()
        submit_row.set_title("Auto-submit after paste")
        submit_row.set_subtitle(
            "Press Return for you after the prompt is pasted, so paste-mode "
            "services (Claude, Gemini) send immediately. Off by default so "
            "you can edit the prompt first. No effect on URL services that "
            "already auto-submit.")
        submit_sw = Gtk.Switch()
        submit_sw.set_valign(Gtk.Align.CENTER)
        submit_sw.set_active(
            bool(self._settings.get("ai_paste_auto_submit", False)))
        submit_sw.connect(
            "notify::active", self._on_switch, "ai_paste_auto_submit")
        submit_row.add(submit_sw)
        submit_row.set_activatable_widget(submit_sw)
        group.add(submit_row)

        return group

    def _on_ai_toggle(self, switch: Gtk.Switch, _param, key: str) -> None:
        current = list(self._settings.get("ai_services") or [])
        if switch.get_active():
            if key not in current:
                current.append(key)
        else:
            current = [k for k in current if k != key]
        self._save_key("ai_services", current)

    def _build_search_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Web search")
        group.set_description(
            "Which engine the popup's \"Search the web\" button uses. "
            "Google also surfaces Gemini answers; DuckDuckGo and Brave "
            "are private; Kagi requires an account."
        )

        # Pull catalog from actions.py so the two stay in sync.
        try:
            from actions import SEARCH_ENGINES
        except Exception:
            SEARCH_ENGINES = {"google": ("Google", "https://www.google.com/search?q={q}")}

        # Picker row
        engine_row = Handy.ActionRow()
        engine_row.set_title("Search engine")
        engine_row.set_subtitle("The button on the popup will open this site.")
        engine_combo = Gtk.ComboBoxText()
        engine_combo.set_valign(Gtk.Align.CENTER)
        for key, (label, _tmpl) in SEARCH_ENGINES.items():
            engine_combo.append(key, label)
        engine_combo.append("custom", "Custom URL…")
        current = (self._settings.get("search_engine") or "google").strip().lower()
        if current not in SEARCH_ENGINES and current != "custom":
            current = "google"
        engine_combo.set_active_id(current)
        engine_row.add(engine_combo)
        engine_row.set_activatable_widget(engine_combo)
        group.add(engine_row)

        # Custom-URL row (sensitive only when "Custom URL…" is picked)
        custom_row = Handy.ActionRow()
        custom_row.set_title("Custom search URL")
        custom_row.set_subtitle("Must contain {q} - replaced by the selection.")
        custom_entry = Gtk.Entry()
        custom_entry.set_valign(Gtk.Align.CENTER)
        custom_entry.set_width_chars(28)
        custom_entry.set_placeholder_text("https://searx.example.com/search?q={q}")
        custom_entry.set_text(self._settings.get("search_engine_custom_url") or "")
        custom_row.add(custom_entry)
        group.add(custom_row)

        def _sync_custom_visibility(*_):
            active = engine_combo.get_active_id() == "custom"
            custom_row.set_sensitive(active)
        _sync_custom_visibility()

        def _on_engine_changed(combo: Gtk.ComboBoxText) -> None:
            new_id = combo.get_active_id() or "google"
            self._save_key("search_engine", new_id)
            _sync_custom_visibility()

        def _on_custom_changed(entry: Gtk.Entry) -> None:
            self._save_key("search_engine_custom_url", entry.get_text().strip())

        engine_combo.connect("changed", _on_engine_changed)
        custom_entry.connect("changed", _on_custom_changed)
        return group

    def _build_advanced_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Advanced")
        group.set_description(
            "Experimental switches. Leave alone unless you know what "
            "you're doing.")

        poll_row = Handy.ActionRow()
        poll_row.set_title("Trigger hotkey on first press")
        poll_row.set_subtitle(
            "Some desktops (especially Cinnamon) swallow the first "
            "hotkey press, so the popup only appears after 2-3 tries. "
            "Turn this on to fix it. Runs a tiny background check - no "
            "noticeable impact on performance.")
        poll_switch = Gtk.Switch()
        poll_switch.set_valign(Gtk.Align.CENTER)
        poll_switch.set_active(bool(self._settings.get("hotkey_use_polling", False)))
        poll_switch.connect("notify::active", self._on_switch, "hotkey_use_polling")
        poll_row.add(poll_switch)
        poll_row.set_activatable_widget(poll_switch)
        group.add(poll_row)

        atspi_row = Handy.ActionRow()
        atspi_row.set_title("Smarter editable detection (experimental)")
        atspi_row.set_subtitle(
            "Listens on the system accessibility bus so the popup can "
            "tell apart 'cursor in chat input' from 'cursor in read-only "
            "history' inside Electron apps like Claude desktop. Off by "
            "default - it was correlated with a Cinnamon desktop-panel "
            "crash. The popup still works fine without it; it just "
            "falls back to a permissive default for unknown Electron "
            "widgets.")
        atspi_switch = Gtk.Switch()
        atspi_switch.set_valign(Gtk.Align.CENTER)
        atspi_switch.set_active(
            bool(self._settings.get("editable_atspi_listener_enabled")))
        atspi_switch.connect(
            "notify::active", self._on_switch,
            "editable_atspi_listener_enabled")
        atspi_row.add(atspi_switch)
        atspi_row.set_activatable_widget(atspi_switch)
        group.add(atspi_row)
        return group

    def _build_terminal_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Terminal commands")

        term_row = Handy.ActionRow()
        term_row.set_title("Keep terminal open after running")
        term_row.set_subtitle(
            "After running a command, leaves the terminal window open "
            "so you can read the output. Otherwise it closes right away.")
        sw = Gtk.Switch()
        sw.set_valign(Gtk.Align.CENTER)
        sw.set_active(bool(self._settings.get("terminal_keep_open", True)))
        sw.connect("notify::active", self._on_switch, "terminal_keep_open")
        term_row.add(sw)
        term_row.set_activatable_widget(sw)
        group.add(term_row)
        return group

    # ---- helpers -------------------------------------------------------------

    def _spin_row(
        self, title: str, subtitle: str, key: str,
        lo: int, hi: int, step: int,
    ) -> Handy.ActionRow:
        row = Handy.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        spin = Gtk.SpinButton.new_with_range(lo, hi, step)
        spin.set_valign(Gtk.Align.CENTER)
        spin.set_value(int(self._settings.get(key)))
        spin.connect("value-changed", self._on_spin, key)
        row.add(spin)
        row.set_activatable_widget(spin)
        return row

    def _seconds_spin_row(
        self, title: str, subtitle: str, key: str,
        lo_s: float, hi_s: float, step_s: float,
    ) -> Handy.ActionRow:
        """Spinner that DISPLAYS seconds but PERSISTS milliseconds, so
        the JSON schema (everything in *_ms) stays unchanged while users
        see and edit numbers that are actually meaningful at a glance."""
        row = Handy.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        spin = Gtk.SpinButton.new_with_range(lo_s, hi_s, step_s)
        spin.set_digits(2 if step_s < 0.1 else 1)
        spin.set_valign(Gtk.Align.CENTER)
        # Trailing 's' unit hint via tooltip - HdyActionRow already shows
        # the title in bold and a subtitle, no room for an inline suffix.
        spin.set_tooltip_text("Value in seconds")
        spin.set_value(float(self._settings.get(key)) / 1000.0)

        def _on_seconds_changed(sb: Gtk.SpinButton) -> None:
            self._save_key(key, int(round(sb.get_value() * 1000)))

        spin.connect("value-changed", _on_seconds_changed)
        row.add(spin)
        row.set_activatable_widget(spin)
        return row

    def _on_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self._save_key(key, bool(switch.get_active()))

    def _on_spin(self, spin: Gtk.SpinButton, key: str) -> None:
        self._save_key(key, int(spin.get_value()))

    def _save_key(self, key: str, value) -> None:
        self._settings.set(key, value)
        self._settings.save()
        if self._on_changed:
            self._on_changed()

    def _apply_trigger_toggle(self) -> None:
        """Start or stop the snippet-trigger XRecord watcher live, so the
        setting takes effect without a daemon restart. Looks up the
        already-loaded clipboard_history user module - that's where the
        watcher lives."""
        try:
            import sys
            mod = (sys.modules.get("linuxpop_user_clipboard_history")
                   or sys.modules.get("clipboard_history"))
            if mod is not None and hasattr(mod, "_maybe_start_trigger_watcher"):
                mod._maybe_start_trigger_watcher()
        except Exception as exc:
            print(f"[settings] could not apply trigger toggle: {exc}")
