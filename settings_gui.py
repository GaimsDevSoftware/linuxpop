"""GTK + libhandy preferences window for LinuxPop.

Uses Hdy.PreferencesWindow with grouped action rows instead of the legacy
Gtk.Dialog + grid layout — gives a modern GNOME-Settings-style boxed-list UI
without migrating to GTK4.

Apply-on-change semantics: edits save immediately, no Save/Cancel buttons.
"""
from __future__ import annotations

from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Handy", "1")
from gi.repository import Gdk, GLib, Gtk, Handy  # noqa: E402

# Some PyGObject builds don't expose Gdk.X11 as a top-level submodule —
# attempt to require it so Gdk.X11.get_server_time is available.
try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: F401, E402
except (ImportError, ValueError):
    pass

from settings import get_settings

Handy.init()


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

        page.add(self._build_general_group())
        page.add(self._build_timing_group())
        page.add(self._build_filter_group())
        page.add(self._build_search_group())
        page.add(self._build_terminal_group())
        page.add(self._build_ai_group())

        win.add(page)
        win.show_all()
        self._window = win
        _force_to_front(win)

    def _on_destroy(self, *_):
        self._window = None

    # ---- groups --------------------------------------------------------------

    def _build_general_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Activation")
        group.set_description("How LinuxPop is summoned.")

        # Auto-popup on selection (switch row)
        sel_row = Handy.ActionRow()
        sel_row.set_title("Auto-popup on selection")
        sel_row.set_subtitle("Show the popup when you highlight text in any app")
        sel_switch = Gtk.Switch()
        sel_switch.set_valign(Gtk.Align.CENTER)
        sel_switch.set_active(bool(self._settings.get("show_on_selection")))
        sel_switch.connect("notify::active", self._on_switch, "show_on_selection")
        sel_row.add(sel_switch)
        sel_row.set_activatable_widget(sel_switch)
        group.add(sel_row)

        # Autostart at login (switch row) — driven by ~/.config/autostart
        # rather than a settings.json key, since the .desktop file is what
        # the DE actually reads. We just toggle the file from here.
        auto_row = Handy.ActionRow()
        auto_row.set_title("Start at login")
        auto_row.set_subtitle("Launch LinuxPop automatically when you log in")
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

        # Selection-popup hotkey row
        hk_row = Handy.ActionRow()
        hk_row.set_title("Selection-popup hotkey")
        hk_row.set_subtitle("Pops up actions for the current highlighted text.")
        recorder = HotkeyRecorder(
            self._settings.get("hotkey") or "",
            on_changed=lambda v: self._save_key("hotkey", v),
        )
        hk_row.add(recorder)
        clear = Gtk.Button.new_from_icon_name("edit-clear-symbolic", Gtk.IconSize.BUTTON)
        clear.set_valign(Gtk.Align.CENTER)
        clear.set_tooltip_text("Disable hotkey")
        clear.connect("clicked", lambda *_: recorder.set_value(""))
        hk_row.add(clear)
        group.add(hk_row)

        # Clipboard plugin master toggle. When off, the background
        # selection-watcher thread is not started, the picker hotkey
        # doesn't bind, and the popup button is hidden.
        clip_master_row = Handy.ActionRow()
        clip_master_row.set_title("Clipboard history & picker")
        clip_master_row.set_subtitle(
            "Track recent clipboard contents and pin snippets. "
            "Off = no clipboard watching at all.")
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
        clip_row.set_title("Clipboard picker hotkey")
        clip_row.set_subtitle("Opens the clipboard history + snippets picker at the cursor.")
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

        # Hotkey source (combo)
        src_row = Handy.ActionRow()
        src_row.set_title("Hotkey reads from")
        src_row.set_subtitle("Which X11 selection the hotkey captures")
        src_combo = Gtk.ComboBoxText()
        src_combo.set_valign(Gtk.Align.CENTER)
        src_combo.append("primary",   "PRIMARY (highlighted)")
        src_combo.append("clipboard", "CLIPBOARD (Ctrl+C)")
        src_combo.set_active_id(self._settings.get("hotkey_source") or "primary")
        src_combo.connect(
            "changed",
            lambda c: self._save_key("hotkey_source", c.get_active_id() or "primary"),
        )
        src_row.add(src_combo)
        src_row.set_activatable_widget(src_combo)
        group.add(src_row)

        return group

    def _build_timing_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Auto-hide timing")
        group.set_description("Milliseconds before the popup disappears.")

        initial_row = self._spin_row(
            "Before mouse arrives",
            "How long to wait if the mouse never reaches the popup",
            "auto_hide_initial_ms", 500, 30000, 500,
        )
        leave_row = self._spin_row(
            "After mouse leaves safe zone",
            "Safe zone = popup + 80 px around the original selection",
            "auto_hide_leave_ms", 200, 20000, 200,
        )
        group.add(initial_row)
        group.add(leave_row)
        return group

    def _build_filter_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Filters")
        group.set_description("When the popup should stay hidden.")

        min_row = self._spin_row(
            "Minimum characters",
            "Ignore selections shorter than this",
            "min_selection_length", 1, 100, 1,
        )
        group.add(min_row)

        # Blocklist: one substring per line. Matches against the active
        # window's title and WM_CLASS (case-insensitive) at popup time.
        # Stored in settings.json as a list of strings; the UI gives a
        # plain multi-line text area because per-row HdyActionRows would
        # be overkill for a free-form list.
        block_row = Handy.ActionRow()
        block_row.set_title("Don't show in these apps or pages")
        block_row.set_subtitle(
            "One pattern per line. Case-insensitive substring match against "
            "the active window's title and class. Examples: KeePassXC, "
            "1Password, Mozilla Firefox - DNB.")
        group.add(block_row)

        block_scroll = Gtk.ScrolledWindow()
        block_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        block_scroll.set_min_content_height(110)
        block_scroll.set_shadow_type(Gtk.ShadowType.IN)
        block_scroll.set_margin_top(2)
        block_scroll.set_margin_bottom(8)
        block_scroll.set_margin_start(14)
        block_scroll.set_margin_end(14)
        block_view = Gtk.TextView()
        block_view.set_wrap_mode(Gtk.WrapMode.NONE)
        try:
            block_view.set_monospace(True)
        except AttributeError:
            pass
        block_buf = block_view.get_buffer()
        block_buf.set_text("\n".join(
            self._settings.get("blocklist_patterns") or []))

        def _on_block_changed(buf: Gtk.TextBuffer) -> None:
            start, end = buf.get_start_iter(), buf.get_end_iter()
            raw = buf.get_text(start, end, True)
            patterns = [
                line.strip() for line in raw.splitlines()
                if line.strip()
            ]
            self._save_key("blocklist_patterns", patterns)
        block_buf.connect("changed", _on_block_changed)
        block_scroll.add(block_view)
        group.add(block_scroll)
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
        custom_row.set_subtitle("Must contain {q} — replaced by the selection.")
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

    def _build_terminal_group(self) -> Handy.PreferencesGroup:
        group = Handy.PreferencesGroup()
        group.set_title("Terminal commands")

        term_row = Handy.ActionRow()
        term_row.set_title("Keep terminal open after running")
        term_row.set_subtitle("Drops into an interactive shell so you can see output and continue")
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

    def _on_switch(self, switch: Gtk.Switch, _param, key: str) -> None:
        self._save_key(key, bool(switch.get_active()))

    def _on_spin(self, spin: Gtk.SpinButton, key: str) -> None:
        self._save_key(key, int(spin.get_value()))

    def _save_key(self, key: str, value) -> None:
        self._settings.set(key, value)
        self._settings.save()
        if self._on_changed:
            self._on_changed()
