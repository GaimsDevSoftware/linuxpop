"""GTK + libhandy preferences window for LinuxPop.

Uses Hdy.PreferencesWindow with grouped action rows instead of the legacy
Gtk.Dialog + grid layout - gives a modern GNOME-Settings-style boxed-list UI
without migrating to GTK4.

Apply-on-change semantics: edits save immediately, no Save/Cancel buttons.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import threading
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


def _detect_userscript_manager() -> tuple[str, str] | None:
    """Look for a Tampermonkey / Violentmonkey / Greasemonkey install
    in any of the common Firefox / Chrome family browser profiles on
    Linux. Returns (manager_name, browser_label) or None when nothing
    is found. We can't poke the browser sandbox directly, but extension
    IDs in the profile-on-disk are stable across versions."""
    import json
    from pathlib import Path
    home = Path.home()

    # Firefox-family profile roots. The .config/ path is what Mint's
    # system Firefox uses; .mozilla/ is the upstream default; the
    # snap/flatpak paths cover sandboxed installs; Zen / LibreWolf are
    # popular Firefox forks LinuxPop users might have.
    firefox_roots = [
        (home / ".config" / "mozilla" / "firefox", "Firefox"),
        (home / ".mozilla" / "firefox", "Firefox"),
        (home / "snap" / "firefox" / "common" / ".mozilla" / "firefox", "Firefox (Snap)"),
        (home / ".var" / "app" / "org.mozilla.firefox" / ".mozilla" / "firefox", "Firefox (Flatpak)"),
        (home / ".var" / "app" / "app.zen_browser.zen" / ".zen", "Zen Browser"),
        (home / ".librewolf", "LibreWolf"),
    ]
    firefox_ids = {
        "{aecec67f-0d10-4fa7-b7c7-609a2db280cf}": "Violentmonkey",
        "firefox@tampermonkey.net":               "Tampermonkey",
        "{e4a8a97b-f2ed-450b-b12d-ee082ba24781}": "Greasemonkey",
    }
    for root, label in firefox_roots:
        if not root.is_dir():
            continue
        # Each profile dir contains extensions.json. Brute-force scan
        # all immediate subdirs - typically 1-2 profiles per user.
        for prof in root.iterdir():
            ext_json = prof / "extensions.json"
            if not ext_json.is_file():
                continue
            try:
                data = json.loads(ext_json.read_text())
            except Exception:
                continue
            for addon in data.get("addons", []):
                aid = addon.get("id") or ""
                if not addon.get("active", False):
                    continue
                name = firefox_ids.get(aid)
                if name:
                    return name, label

    # Chrome family: each extension is a subdirectory whose name is
    # the extension ID. We don't need to parse anything, just check
    # that the directory exists.
    chrome_roots = [
        (home / ".config" / "google-chrome", "Chrome"),
        (home / ".config" / "chromium", "Chromium"),
        (home / ".config" / "BraveSoftware" / "Brave-Browser", "Brave"),
        (home / ".config" / "microsoft-edge", "Edge"),
        (home / ".config" / "vivaldi", "Vivaldi"),
        (home / ".config" / "opera", "Opera"),
    ]
    chrome_ids = {
        "dhdgffkkebhmkfjojejmpbldmpobfkfo": "Tampermonkey",
        "jinjaccalgkegednnccohejagnlnfdag": "Violentmonkey",
        # Tampermonkey on Edge uses the same Chromium ID generally,
        # but its store ID differs - cover both.
        "iikmkjmpaadaobahmlepeloendndfphd": "Tampermonkey",
    }
    for root, label in chrome_roots:
        if not root.is_dir():
            continue
        # Profiles: "Default", "Profile 1", "Profile 2", ...
        for profile in root.iterdir():
            ext_root = profile / "Extensions"
            if not ext_root.is_dir():
                continue
            for ext_dir in ext_root.iterdir():
                name = chrome_ids.get(ext_dir.name)
                if name and ext_dir.is_dir():
                    return name, label

    return None


def _detect_default_browser_family() -> str | None:
    """Return a coarse browser family name ('firefox', 'chrome', 'edge',
    'brave', 'opera', 'vivaldi', 'chromium') so the userscript-manager
    install button can deep-link to the right add-on store. Returns
    None when we can't tell - the caller falls back to a two-button
    chooser."""
    try:
        out = subprocess.run(
            ["xdg-mime", "query", "default", "x-scheme-handler/https"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        desktop = (out.stdout or "").strip().lower()
    except (OSError, subprocess.SubprocessError):
        return None
    # Order matters: 'chromium' must check before generic 'chrome' since
    # the chromium desktop file often contains both substrings.
    table = [
        ("firefox",  "firefox"),
        ("librewolf", "firefox"),
        ("tor-browser", "firefox"),
        ("zen", "firefox"),
        ("brave",    "brave"),
        ("vivaldi",  "vivaldi"),
        ("opera",    "opera"),
        ("microsoft-edge", "edge"),
        ("edge",     "edge"),
        ("chromium", "chromium"),
        ("google-chrome", "chrome"),
        ("chrome",   "chrome"),
    ]
    for needle, family in table:
        if needle in desktop:
            return family
    return None


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
        win.set_default_size(720, 640)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_modal(False)
        win.connect("destroy", self._on_destroy)

        page = Handy.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")

        # Snippets & Clipboard first - that's the core of what people use
        # LinuxPop for. Activation comes second because users new to
        # the app reach for "what's my hotkey" before anything else.
        # AI services sits at position 3 because that's the most-edited
        # section after the engagement curve picks up - burying it past
        # 8 groups (the historic order) hurt discoverability.
        page.add(self._build_snippets_clipboard_group())
        page.add(self._build_activation_group())
        for ai_group in self._build_ai_groups():
            page.add(ai_group)
        page.add(self._build_appearance_group())
        page.add(self._build_timing_group())
        page.add(self._build_filter_group())
        page.add(self._build_search_group())
        page.add(self._build_terminal_group())
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
        # popup is. Clamped to [16, 32] pixels - under 16 the symbolic
        # icons go muddy at every common Linux resolution; over 32 is
        # touch-target territory mouse users don't benefit from, and it
        # turns the popup into a screen-spanning bar.
        size_row = Handy.ActionRow()
        size_row.set_title("Popup button size")
        size_row.set_subtitle(
            "How big each action button in the popup is, in pixels. "
            "16 is small and dense; 32 is roomy and easy to click.")
        size_adj = Gtk.Adjustment(
            value=int(self._settings.get("popup_button_size", 22) or 22),
            lower=16, upper=32, step_increment=1, page_increment=4,
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

        # Max buttons per popup: how many actions can show before the
        # popup wraps to a second row and (past 2 rows) drops to a
        # "+N" overflow chip. Pairs with popup_button_size to govern
        # popup density end-to-end. Range starts at 4 (anything less
        # makes the popup feel broken) and tops out at 40 (point of
        # diminishing returns; the chip handles the rest).
        count_row = Handy.ActionRow()
        count_row.set_title("Maximum buttons in the popup")
        count_row.set_subtitle(
            "Cap on how many actions the popup shows. Extras wrap to "
            "a second row first; beyond that, a '+N' chip lets you open "
            "Plugin Manager to reorder. Raise this if you have lots of "
            "plugins enabled and want them all visible.")
        count_adj = Gtk.Adjustment(
            value=int(self._settings.get("max_popup_buttons", 24) or 24),
            lower=4, upper=40, step_increment=1, page_increment=4,
        )
        count_spin = Gtk.SpinButton()
        count_spin.set_valign(Gtk.Align.CENTER)
        count_spin.set_adjustment(count_adj)
        count_spin.set_numeric(True)
        count_spin.connect(
            "value-changed",
            lambda spin: self._save_key("max_popup_buttons", int(spin.get_value())))
        count_row.add(count_spin)
        count_row.set_activatable_widget(count_spin)
        group.add(count_row)

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

        # OCR hotkey row - sits next to the popup hotkey so the two
        # "summon something with a key chord" rows cluster together.
        # The status subtitle changes based on whether the OCR backend
        # is reachable (maim + tesseract), so the user knows whether
        # the hotkey will actually do anything when pressed.
        ocr_row = Handy.ActionRow()
        ocr_row.set_title("Screen OCR hotkey")
        try:
            from screen_ocr import is_supported as _ocr_supported
            ocr_ok, ocr_reason = _ocr_supported()
        except Exception:
            ocr_ok, ocr_reason = False, "screen_ocr module not available"
        if ocr_ok:
            ocr_row.set_subtitle(
                "Press this anywhere to draw a rectangle on screen. "
                "Text inside the rectangle is OCR'd by tesseract and "
                "lands on the clipboard.")
        else:
            ocr_row.set_subtitle(
                f"Setup needed - {ocr_reason}. The hotkey is saved "
                "but won't fire until the missing tools are installed.")
        ocr_recorder = HotkeyRecorder(
            self._settings.get("ocr_hotkey") or "",
            on_changed=lambda v: self._save_key("ocr_hotkey", v),
        )
        ocr_row.add(ocr_recorder)
        ocr_clear = Gtk.Button.new_from_icon_name(
            "edit-clear-symbolic", Gtk.IconSize.BUTTON)
        ocr_clear.set_valign(Gtk.Align.CENTER)
        ocr_clear.set_tooltip_text("Disable hotkey")
        ocr_clear.connect("clicked", lambda *_: ocr_recorder.set_value(""))
        ocr_row.add(ocr_clear)
        group.add(ocr_row)

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

    def _build_ai_groups(self) -> list[Handy.PreferencesGroup]:
        """Build the AI-services section as several PreferencesGroups
        instead of one long flat list. Each conditional block (browser-
        only explainer, userscript bridge, API keys) becomes its own
        group so it reads as a separate card with its own heading; the
        whole card hides when the matching send method isn't active.

        Returned groups, in order they should be added to the page:
          1. main      - method picker + service toggles + overrides
          2. browser   - browser-mode explainer (conditional)
          3. userscript- bridge status + manager install (conditional)
          4. api       - API key entries (conditional)
        """
        group = Handy.PreferencesGroup()
        group.set_title("AI services")
        group.set_description(
            "Which chat-AI buttons the popup shows, and how they "
            "deliver text. Toggle off any service you don't use."
        )
        # The conditional sub-cards. Each carries its own title so it
        # reads as a self-contained block rather than free-floating
        # rows under the main heading.
        browser_group = Handy.PreferencesGroup()
        browser_group.set_title("Browser mode")
        browser_group.set_description(
            "How the plain Browser option behaves per service.")
        userscript_group = Handy.PreferencesGroup()
        userscript_group.set_title("Userscript bridge")
        userscript_group.set_description(
            "One-time setup that makes Claude and Gemini as reliable "
            "as the URL-prefill services.")
        api_group = Handy.PreferencesGroup()
        api_group.set_title("API keys")
        api_group.set_description(
            "Required for API send mode. Stored as plain text - keep "
            "the machine to yourself.")

        # ---- "How to send" mode picker ----------------------------------
        # Three deliberate choices, plainly worded:
        #   browser  - what we did before: opens the chat site in your
        #              browser with the text prefilled (or paste-via-
        #              xdotool when the site doesn't take a URL). No
        #              setup, no subscription needed - but it's fragile
        #              in Electron-based browsers and creates a tab
        #              every time.
        #   cli      - sends through the official Anthropic/OpenAI/Google
        #              CLI (claude, codex, antigravity) that the user
        #              already signed into with their subscription.
        #              Reply shows up in a LinuxPop dialog. Most reliable
        #              when a CLI is installed; per-service fallback to
        #              browser otherwise.
        #   api      - sends via the provider's REST API with the user's
        #              own key. Most reliable for those who already pay
        #              per call. Falls back to browser per service
        #              without a key.
        # "How to send" picker. ActionRow puts the combo on the right of
        # the title in a fixed-width column - long combo labels (e.g.
        # "Browser + userscript bridge (reliable on Claude/Gemini)")
        # ate into the title's space and wrapped "How to send the text"
        # onto two lines at any reasonable window width. Switching to a
        # vertical layout where the combo gets the full row width fixes
        # both: title and subtitle stack at top, combo spans below.
        method_row = Handy.ActionRow()
        method_row.set_title("How to send the text")
        method_row.set_subtitle(
            "Where AI buttons deliver your selection.")
        method_row.set_selectable(False)
        method_row.set_activatable(False)
        method_combo = Gtk.ComboBoxText()
        method_combo.set_hexpand(True)
        method_combo.set_margin_top(2)
        method_combo.set_margin_bottom(2)
        for key, label in [
            ("browser",    "Browser - open chat website (no setup)"),
            ("userscript", "Browser + userscript bridge (reliable on Claude/Gemini)"),
            ("api",        "API key - use pay-as-you-go API"),
        ]:
            method_combo.append(key, label)
        current_method = (self._settings.get("ai_send_method") or "userscript").lower()
        method_combo.set_active_id(current_method)
        method_combo.connect(
            "changed",
            lambda c: (
                self._save_key("ai_send_method", c.get_active_id() or "userscript"),
                _refresh_method_visibility(),
            ),
        )
        group.add(method_row)
        # Combo lives in its own ActionRow underneath so it spans full
        # width. set_activatable_widget on this row lets clicks open the
        # dropdown directly.
        method_combo_row = Handy.ActionRow()
        method_combo_row.set_selectable(False)
        method_combo_row.add(method_combo)
        method_combo_row.set_activatable_widget(method_combo)
        group.add(method_combo_row)

        # Longer explanation as its own row underneath. Handy.ActionRow
        # crushes long subtitles into a narrow column when there's a
        # widget on the right; pulling the paragraph out of the subtitle
        # lets it span the full row width.
        method_hint_row = Handy.ActionRow()
        method_hint_row.set_selectable(False)
        method_hint_row.set_activatable(False)
        method_hint_label = Gtk.Label(
            label=(
                "Switch any time - changes apply on the next click. "
                "Methods that need extra setup (CLI install, API key) "
                "fall back to the browser per-service when the setup "
                "isn't there yet."),
            xalign=0)
        method_hint_label.set_line_wrap(True)
        method_hint_label.set_max_width_chars(64)
        method_hint_label.get_style_context().add_class("dim-label")
        method_hint_label.set_margin_top(2)
        method_hint_label.set_margin_bottom(2)
        method_hint_row.add(method_hint_label)
        group.add(method_hint_row)

        # CLI mode was dropped 2026-05-29: it routed to vendor coding
        # agents (Claude Code, Codex, Antigravity) rather than the
        # conversational chat users expected from "Ask Claude", and
        # the OAuth-token-reuse trick that would have made it work for
        # subscription chat was banned by Anthropic in Jan 2026.
        cli_install_rows: list[Handy.ActionRow] = []

        # ---- Browser-only explainer (visible in plain browser mode) -----
        # When the user picks "Browser - open chat website" we want them
        # to know exactly what they're getting: URL prefill on services
        # that support it, fragile paste-via-xdotool everywhere else. The
        # userscript bridge fixes both, so the row gently nudges toward
        # the upgrade without forcing them into it.
        browser_rows: list[Handy.ActionRow] = []
        browser_info_row = Handy.ActionRow()
        browser_info_row.set_selectable(False)
        browser_info_row.set_activatable(False)
        try:
            br_icon = Gtk.Image.new_from_icon_name(
                "applications-internet", Gtk.IconSize.LARGE_TOOLBAR)
            br_icon.set_pixel_size(20)
            browser_info_row.add_prefix(br_icon)
        except Exception:
            pass
        browser_info_label = Gtk.Label(
            label=(
                "Plain browser mode opens the chat website with the prompt "
                "preloaded in the URL where the service supports it - "
                "ChatGPT, Perplexity, and Google AI Search auto-submit on "
                "load. For Claude and Gemini there's no URL prefill, so "
                "LinuxPop falls back to xdotool paste after the page "
                "settles. That paste path fights React/ProseMirror and "
                "drops keystrokes intermittently.\n\n"
                "Switch to 'Browser + userscript bridge' above for a "
                "one-time setup that makes Claude and Gemini just as "
                "reliable as the others. Until then, the URL-prefill "
                "services still work fine here."),
            xalign=0)
        browser_info_label.set_line_wrap(True)
        browser_info_label.set_max_width_chars(72)
        browser_info_label.get_style_context().add_class("dim-label")
        browser_info_label.set_margin_top(2)
        browser_info_label.set_margin_bottom(2)
        browser_info_row.add(browser_info_label)
        browser_rows.append(browser_info_row)
        browser_group.add(browser_info_row)

        # ---- Userscript bridge panel (visible in userscript mode) -------
        userscript_rows: list[Handy.ActionRow] = []

        bridge_row = Handy.ActionRow()
        bridge_row.set_title("Browser bridge")
        bridge_row.set_subtitle("…")
        try:
            img = Gtk.Image.new_from_icon_name(
                "applications-internet", Gtk.IconSize.LARGE_TOOLBAR)
            img.set_pixel_size(28)
            bridge_row.add_prefix(img)
        except Exception:
            pass

        bridge_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bridge_btn_box.set_valign(Gtk.Align.CENTER)
        bridge_install_btn = Gtk.Button(label="Install userscript")
        # Persisted marker file: written by the bridge on /installed
        # ping. Survives daemon restarts so the user isn't prompted to
        # reinstall on every Settings open.
        try:
            import bridge_server as _bsv
            marker_exists = _bsv.userscript_marker_exists()
        except Exception:
            _bsv = None
            marker_exists = False
        if marker_exists:
            bridge_install_btn.set_label("Userscript installed ✓")
            bridge_install_btn.get_style_context().add_class(
                "suggested-action")
        # Secondary path for users who already installed the userscript
        # on a previous run (or before we shipped marker persistence):
        # this just writes the marker without bouncing them through the
        # install flow. Hidden once the marker exists.
        already_btn = Gtk.Button(label="Already installed")
        already_btn.set_tooltip_text(
            "Click if the userscript is already active in your "
            "Tampermonkey/Violentmonkey dashboard - sets the install "
            "marker so this dialog stops asking.")
        already_btn.get_style_context().add_class("flat")

        def _on_already_installed(_b):
            try:
                import bridge_server as _bs
                _bs._mark_userscript_installed()
                bridge_install_btn.set_label("Userscript installed ✓")
                bridge_install_btn.get_style_context().add_class(
                    "suggested-action")
                already_btn.set_visible(False)
            except Exception as exc:
                bridge_row.set_subtitle(f"Could not record install: {exc}")
        already_btn.connect("clicked", _on_already_installed)
        if marker_exists:
            already_btn.set_no_show_all(True)
            already_btn.hide()
        # Poll the bridge's /installed/status endpoint so this label can
        # flip to "Userscript installed ✓" once the userscript actually
        # pings us back. The poll runs while the bridge row is visible
        # and stops when the window closes.
        userscript_check_ticks = {"n": 0}

        def _userscript_install_poll() -> bool:
            userscript_check_ticks["n"] += 1
            # Stop polling after 5 minutes of nothing; user can reopen
            # Settings to re-arm if they came back later.
            if userscript_check_ticks["n"] > 100:
                return False
            try:
                import urllib.request
                import json as _json
                port = int(self._settings.get(
                    "ai_userscript_bridge_port", 8766) or 8766)
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/installed/status",
                        timeout=1.0) as r:
                    data = _json.loads(r.read().decode())
                if data.get("installed"):
                    bridge_install_btn.set_label("Userscript installed ✓")
                    bridge_install_btn.get_style_context().add_class(
                        "suggested-action")
                    return False  # stop polling
            except Exception:
                pass
            return True
        # Kick the first poll immediately; subsequent ones every 3 s.
        GLib.timeout_add(3000, _userscript_install_poll)

        def _bridge_status_text() -> str:
            try:
                import sys
                # Bridge import is best-effort; the daemon process may
                # not have it on path when the settings GUI runs.
                mod = sys.modules.get("bridge_server")
                if mod is None:
                    import importlib
                    try:
                        mod = importlib.import_module("bridge_server")
                    except Exception:
                        mod = None
                if mod and getattr(mod, "is_running", lambda: False)():
                    port = mod.current_port()
                    return (
                        f"Running on 127.0.0.1:{port}. After installing the "
                        "userscript, AI buttons send instantly to Claude / "
                        "ChatGPT / Gemini / Perplexity.")
                port = int(self._settings.get(
                    "ai_userscript_bridge_port", 8766) or 8766)
                return (
                    f"Bridge starts on first AI click (port {port}). "
                    "Install the userscript once - it stays put.")
            except Exception:
                return "Local HTTP bridge on 127.0.0.1."

        def _refresh_bridge_status():
            bridge_row.set_subtitle(_bridge_status_text())
            return False

        bridge_row.set_subtitle(_bridge_status_text())

        def _on_install_userscript(_btn):
            # Start (or reuse) the bridge so the install URL is reachable,
            # then open it in the user's browser. Tampermonkey/Violentmonkey
            # detects the .user.js extension and shows the install prompt.
            import importlib
            try:
                mod = importlib.import_module("bridge_server")
                port = int(self._settings.get(
                    "ai_userscript_bridge_port", 8766) or 8766)
                actual_port = mod.start(port)
                # Save the actually-bound port so the rest of the system
                # uses the same number.
                if actual_port != port:
                    self._save_key("ai_userscript_bridge_port", actual_port)
                url = f"http://127.0.0.1:{actual_port}/linuxpop.user.js"
                subprocess.Popen(
                    ["xdg-open", url],
                    start_new_session=True,
                )
                bridge_row.set_subtitle(
                    f"Opened {url} in your browser. Confirm in "
                    "Tampermonkey/Violentmonkey. If nothing happened, "
                    "install one of those extensions first.")
            except Exception as exc:
                bridge_row.set_subtitle(f"Could not start bridge: {exc}")

        bridge_install_btn.connect("clicked", _on_install_userscript)
        bridge_btn_box.pack_start(already_btn, False, False, 0)
        bridge_btn_box.pack_start(bridge_install_btn, False, False, 0)
        bridge_row.add(bridge_btn_box)
        userscript_rows.append(bridge_row)
        userscript_group.add(bridge_row)

        prereq_row = Handy.ActionRow()
        existing_manager = _detect_userscript_manager()
        if existing_manager is not None:
            mname, mbrowser = existing_manager
            prereq_row.set_title(f"{mname} detected in {mbrowser}")
            prereq_row.set_subtitle(
                "Userscript manager is installed - you're all set. "
                "Click 'Install userscript' above to install LinuxPop's "
                "Send-to-AI script into it.")
        else:
            prereq_row.set_title("Need a userscript manager?")
            prereq_row.set_subtitle(
                "One-time install of Tampermonkey or Violentmonkey. The "
                "button below opens the right add-on page in your default "
                "browser - finish the install there, then click Install "
                "userscript above.")
        try:
            icon_name = ("emblem-default-symbolic"
                         if existing_manager is not None
                         else "system-software-install")
            img = Gtk.Image.new_from_icon_name(
                icon_name, Gtk.IconSize.LARGE_TOOLBAR)
            img.set_pixel_size(28)
            prereq_row.add_prefix(img)
        except Exception:
            pass
        prereq_btn_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prereq_btn_box.set_valign(Gtk.Align.CENTER)
        browser_family = _detect_default_browser_family()
        # Per-family install URL. Firefox gets Violentmonkey because
        # Tampermonkey's free Firefox build has been a stale fork for
        # years; Violentmonkey is the actively-maintained option there.
        family_to_url = {
            "firefox":  "https://addons.mozilla.org/firefox/addon/violentmonkey/",
            "chrome":   "https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo",
            "chromium": "https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo",
            "brave":    "https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo",
            "edge":     "https://microsoftedge.microsoft.com/addons/detail/tampermonkey/iikmkjmpaadaobahmlepeloendndfphd",
            "opera":    "https://addons.opera.com/extensions/details/tampermonkey-beta/",
            "vivaldi":  "https://chromewebstore.google.com/detail/tampermonkey/dhdgffkkebhmkfjojejmpbldmpobfkfo",
        }
        family_label = {
            "firefox":  "Firefox (Violentmonkey)",
            "chrome":   "Chrome (Tampermonkey)",
            "chromium": "Chromium (Tampermonkey)",
            "brave":    "Brave (Tampermonkey)",
            "edge":     "Edge (Tampermonkey)",
            "opera":    "Opera (Tampermonkey)",
            "vivaldi":  "Vivaldi (Tampermonkey)",
        }
        if existing_manager is not None:
            check = Gtk.Image.new_from_icon_name(
                "emblem-ok-symbolic", Gtk.IconSize.BUTTON)
            check.set_valign(Gtk.Align.CENTER)
            prereq_btn_box.pack_start(check, False, False, 0)
        elif browser_family and browser_family in family_to_url:
            install_btn = Gtk.Button(label=f"Install for {family_label[browser_family]}")
            install_btn.set_tooltip_text(
                f"Opens {family_to_url[browser_family]} in your default browser.")
            install_btn.connect(
                "clicked",
                lambda _b, url=family_to_url[browser_family]:
                    subprocess.Popen(["xdg-open", url], start_new_session=True))
            prereq_btn_box.pack_start(install_btn, False, False, 0)
        else:
            # No default detected (or unknown family). Show two side-by-
            # side buttons so the user can pick.
            chrome_btn = Gtk.Button(label="For Chrome / Edge / Brave")
            chrome_btn.connect(
                "clicked",
                lambda _b, url=family_to_url["chrome"]:
                    subprocess.Popen(["xdg-open", url], start_new_session=True))
            firefox_btn = Gtk.Button(label="For Firefox")
            firefox_btn.connect(
                "clicked",
                lambda _b, url=family_to_url["firefox"]:
                    subprocess.Popen(["xdg-open", url], start_new_session=True))
            prereq_btn_box.pack_start(chrome_btn, False, False, 0)
            prereq_btn_box.pack_start(firefox_btn, False, False, 0)
        prereq_row.add(prereq_btn_box)
        userscript_rows.append(prereq_row)
        userscript_group.add(prereq_row)

        # ---- API key panel (visible in api mode) ------------------------
        api_rows: list[Handy.ActionRow] = []
        for label, setting_key, icon_name in [
            ("Anthropic API key (Claude)", "ai_anthropic_api_key", "linuxpop-claude"),
            ("OpenAI API key (ChatGPT)",   "ai_openai_api_key",    "linuxpop-chatgpt"),
        ]:
            row = Handy.ActionRow()
            row.set_title(label)
            row.set_subtitle(
                "Stored in settings.json as plain text. Treat it like "
                "the file holds a secret - keep your machine to yourself.")
            try:
                img = Gtk.Image.new_from_icon_name(
                    icon_name, Gtk.IconSize.LARGE_TOOLBAR)
                img.set_pixel_size(28)
                row.add_prefix(img)
            except Exception:
                pass
            entry = Gtk.Entry()
            entry.set_visibility(False)
            entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            entry.set_valign(Gtk.Align.CENTER)
            entry.set_width_chars(20)
            entry.set_text(self._settings.get(setting_key) or "")
            entry.set_placeholder_text("sk-...")
            entry.connect(
                "changed",
                lambda e, k=setting_key:
                    self._save_key(k, e.get_text().strip()))
            row.add(entry)
            api_rows.append(row)
            api_group.add(row)

        # ---- Service list -----------------------------------------------
        services = [
            ("claude",     "Claude",            "linuxpop-claude",
             "Anthropic's chat assistant. Best for nuanced writing and reasoning."),
            ("chatgpt",    "ChatGPT",           "linuxpop-chatgpt",
             "OpenAI's chat assistant. Default URL mode prefills without sending."),
            ("gemini",     "Gemini",            "linuxpop-gemini",
             "Google's chat assistant. Currently paste-mode in the browser."),
            ("perplexity", "Perplexity",        "linuxpop-perplexity",
             "Search-grounded answers with citations. Auto-submits."),
            ("google_ai",  "Google AI Search",  "linuxpop-google-ai",
             "Google Search's AI Mode. No login, instant answers."),
        ]
        current = list(self._settings.get("ai_services") or [])
        for key, label, icon_name, host in services:
            row = Handy.ActionRow()
            row.set_title(label)
            row.set_subtitle(host)
            try:
                img = Gtk.Image.new_from_icon_name(
                    icon_name, Gtk.IconSize.LARGE_TOOLBAR)
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

        # ---- Per-service overrides (advanced, collapsed by default) ----
        # Most users pick one global method and stick with it. Power users
        # want Claude on CLI (their Pro sub) while keeping Gemini on the
        # userscript bridge (Google has no free CLI tier). Hidden behind
        # an expander so the casual UI stays clean.
        override_expander = Handy.ExpanderRow()
        override_expander.set_title("Per-service method (advanced)")
        override_expander.set_subtitle(
            "Override the global send method for individual services. "
            "Useful when you have a CLI installed for one but not others.")
        # NB: the red "stop-sign" glyph you may see in the right slot
        # under some icon themes (e.g. WhiteSur) is NOT a libhandy bug.
        # HdyExpanderRow asks for the icon "hdy-expander-arrow-symbolic"
        # which some themes don't ship (they ship the libadwaita-prefixed
        # "adw-expander-arrow-symbolic" instead). GTK then falls back to
        # "image-missing", which WhiteSur renders in #da4453 red. Fix is
        # at the theme level: symlink hdy- -> adw- in the icon theme. See
        # ~/.claude/CLAUDE.md for the script.
        override_expander.set_enable_expansion(True)
        override_expander.set_show_enable_switch(False)
        override_options = [
            ("",           "Use default (global method)"),
            ("browser",    "Browser - open chat website"),
            ("userscript", "Browser + userscript bridge"),
            ("api",        "API key"),
        ]
        for key, label, icon_name, _host in services:
            sub_row = Handy.ActionRow()
            sub_row.set_title(label)
            try:
                img = Gtk.Image.new_from_icon_name(
                    icon_name, Gtk.IconSize.LARGE_TOOLBAR)
                img.set_pixel_size(22)
                sub_row.add_prefix(img)
            except Exception:
                pass
            combo = Gtk.ComboBoxText()
            combo.set_valign(Gtk.Align.CENTER)
            for opt_key, opt_label in override_options:
                combo.append(opt_key or "default", opt_label)
            saved = (self._settings.get(f"ai_{key}_mode") or "").lower()
            combo.set_active_id(saved if saved else "default")
            combo.connect(
                "changed",
                lambda c, k=key: self._on_ai_mode_override(c, k),
            )
            sub_row.add(combo)
            sub_row.set_activatable_widget(combo)
            override_expander.add(sub_row)
        group.add(override_expander)

        # ---- Auto-submit (browser mode only) ----------------------------
        submit_row = Handy.ActionRow()
        submit_row.set_title("Auto-submit after paste")
        submit_row.set_subtitle(
            "In browser mode for paste-fallback services (Claude, "
            "Gemini), press Return after the prompt is pasted so the "
            "chat sends immediately. Off by default to let you edit the "
            "prompt first.")
        submit_sw = Gtk.Switch()
        submit_sw.set_valign(Gtk.Align.CENTER)
        submit_sw.set_active(
            bool(self._settings.get("ai_paste_auto_submit", False)))
        submit_sw.connect(
            "notify::active", self._on_switch, "ai_paste_auto_submit")
        submit_row.add(submit_sw)
        submit_row.set_activatable_widget(submit_sw)
        group.add(submit_row)

        def _refresh_method_visibility() -> None:
            current = (self._settings.get("ai_send_method") or "userscript").lower()
            api_group.set_visible(current == "api")
            userscript_group.set_visible(current == "userscript")
            browser_group.set_visible(current == "browser")
            # Auto-submit applies to both browser-paste and userscript
            # modes; the userscript respects the `submit` flag we send
            # with the prompt.
            submit_row.set_visible(current in ("browser", "userscript"))
            if current == "userscript":
                bridge_row.set_subtitle(_bridge_status_text())
        # Run once now and again after the window is realised so the
        # initial visibility matches the saved setting.
        _refresh_method_visibility()
        GLib.idle_add(_refresh_method_visibility)

        # Order matters: main first, then the conditional sub-cards in
        # the same vertical position they'd occupy as inline rows under
        # the main card. The browser/userscript/api groups are hidden
        # via set_visible based on the active send method.
        return [group, browser_group, userscript_group, api_group]


    def _on_ai_toggle(self, switch: Gtk.Switch, _param, key: str) -> None:
        current = list(self._settings.get("ai_services") or [])
        if switch.get_active():
            if key not in current:
                current.append(key)
        else:
            current = [k for k in current if k != key]
        self._save_key("ai_services", current)
        # Auto-insert into plugin_order when the user customised one. If
        # plugin_order is empty (priority-fallback), there's nothing to
        # do - the AI service registers at its built-in priority. If
        # plugin_order is set and doesn't include this service, the
        # listed plugins eat all the popup slots before the new service
        # ever gets a chance to render. Slot it next to existing
        # send-to-* entries so it inherits their visual rank instead
        # of being banished to the tail past max_popup_buttons.
        if switch.get_active():
            order = list(self._settings.get("plugin_order") or [])
            plugin_name = f"send-to-{key.replace('_', '-')}"
            if order and plugin_name not in order:
                # Insert after the last existing send-to-* so all AI
                # services cluster together. No siblings? Tail-append.
                last_ai = -1
                for i, name in enumerate(order):
                    if name.startswith("send-to-"):
                        last_ai = i
                if last_ai >= 0:
                    order.insert(last_ai + 1, plugin_name)
                else:
                    order.append(plugin_name)
                self._save_key("plugin_order", order)

    def _on_ai_mode_override(self, combo: Gtk.ComboBoxText, key: str) -> None:
        """Per-service override of the global ai_send_method. 'default'
        clears the override (back to global). Anything else writes
        ai_<key>_mode which the dispatch in send_to_ai._send() honours."""
        choice = combo.get_active_id() or "default"
        setting_key = f"ai_{key}_mode"
        if choice == "default" or not choice:
            # Drop the key entirely so the dispatch falls back cleanly.
            try:
                data = self._settings._data  # type: ignore[attr-defined]
                if setting_key in data:
                    del data[setting_key]
                    self._settings.save()
            except Exception:
                # Save an empty string as a fallback - dispatch checks
                # for truthy, so "" reads as "no override".
                self._save_key(setting_key, "")
        else:
            self._save_key(setting_key, choice)

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

        # ---- MCP server -----------------------------------------------
        # Power users wiring LinuxPop into Claude Desktop / Cursor /
        # any other MCP-aware client need the JSON snippet that points
        # at the linuxpop-mcp launcher. One-click copy to clipboard is
        # the friendly version of "go read the source".
        mcp_row = Handy.ActionRow()
        mcp_row.set_title("MCP server (advanced)")
        # Resolve the actual on-disk path so the snippet works when
        # LinuxPop is run from a non-standard location (e.g. a dev
        # checkout in ~/Dokumenter/Kode-prosjekter/).
        try:
            import sys as _sys
            from pathlib import Path as _P
            _here = _P(_sys.modules["__main__"].__file__).parent.resolve()
            _launcher = _here / "linuxpop-mcp"
        except Exception:
            _launcher = None
        if _launcher and _launcher.exists():
            mcp_row.set_subtitle(
                f"LinuxPop ships an MCP stdio server at {_launcher}. "
                "Copy the Claude Desktop config snippet below and paste "
                "it into ~/.config/Claude/claude_desktop_config.json, "
                "then restart Claude Desktop.")
        else:
            mcp_row.set_subtitle(
                "LinuxPop ships an MCP stdio server. Copy the Claude "
                "Desktop config snippet below into "
                "~/.config/Claude/claude_desktop_config.json.")

        def _on_copy_mcp_snippet(_b):
            import json as _json
            launcher_str = str(_launcher) if _launcher else "/path/to/linuxpop-mcp"
            snippet = _json.dumps(
                {"mcpServers": {"linuxpop": {"command": launcher_str}}},
                indent=2)
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=snippet.encode("utf-8"), check=False, timeout=2.0,
            )
            subprocess.run(
                ["notify-send", "--hint=byte:transient:1", "-t", "3000",
                 "-i", "edit-paste-symbolic", "LinuxPop MCP",
                 "Snippet on clipboard. Paste into Claude Desktop's "
                 "config and restart it."],
                check=False,
            )

        mcp_btn = Gtk.Button(label="Copy snippet")
        mcp_btn.set_valign(Gtk.Align.CENTER)
        mcp_btn.connect("clicked", _on_copy_mcp_snippet)
        mcp_row.add(mcp_btn)
        group.add(mcp_row)

        # ---- Reset settings -------------------------------------------
        # Destructive: clears every key in settings.json that has a
        # built-in default. Plugins, recipes, snippets, and clipboard
        # history are kept (those are user data, not preferences). A
        # confirmation dialog stands in the way so this can't be
        # triggered by an accidental Enter.
        reset_row = Handy.ActionRow()
        reset_row.set_title("Reset settings to defaults")
        reset_row.set_subtitle(
            "Restores every preference (hotkeys, timing, popup look, "
            "AI services, etc.) to its factory default. Your snippets, "
            "clipboard history, installed plugins, and custom buttons "
            "are kept - this only resets preferences.")
        reset_btn = Gtk.Button(label="Reset…")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.get_style_context().add_class("destructive-action")
        reset_btn.connect("clicked", self._on_reset_to_defaults)
        reset_row.add(reset_btn)
        group.add(reset_row)
        return group

    def _on_reset_to_defaults(self, _btn: Gtk.Button) -> None:
        from settings import DEFAULTS
        confirm = Gtk.MessageDialog(
            transient_for=self._window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Reset all preferences to defaults?",
            secondary_text=(
                "Every setting in this window will go back to how "
                "LinuxPop ships out of the box.\n\n"
                "Kept: your snippets, clipboard history, installed "
                "plugins, custom buttons.\n"
                "Reset: hotkeys, timing, popup look, AI services, "
                "blocklists, search engine, terminal behaviour.\n\n"
                "There's no undo."
            ),
        )
        confirm.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Reset", Gtk.ResponseType.OK,
        )
        ok_btn = confirm.get_widget_for_response(Gtk.ResponseType.OK)
        if ok_btn is not None:
            ok_btn.get_style_context().add_class("destructive-action")
        resp = confirm.run()
        confirm.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        # Replace the singleton's data with the canonical defaults and
        # persist. Use a fresh copy so the dict isn't aliased with the
        # module-level DEFAULTS.
        try:
            self._settings._data = {**DEFAULTS}  # type: ignore[attr-defined]
            self._settings.save()
        except Exception as exc:
            err = Gtk.MessageDialog(
                transient_for=self._window, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.CLOSE,
                text="Couldn't reset settings",
                secondary_text=f"{exc}")
            err.run()
            err.destroy()
            return
        # Trigger the daemon-side reload so hotkeys / watchers / etc.
        # pick up the new state without restart, then rebuild this
        # window so its widgets reflect what's on disk.
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass
        if self._window is not None:
            self._window.destroy()
        self.show()

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
