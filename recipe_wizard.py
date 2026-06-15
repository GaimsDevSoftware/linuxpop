"""Step-by-step assistant for building a custom LinuxPop button (recipe).

Uses Gtk.Assistant so the user gets one decision per page, with a live
popup-mockup updating as they go. Each page hides the underlying
"recipe JSON" jargon and uses plain-language buttons (e.g. "Use selected
text" instead of "{text_url}").

Same external interface as before - plugin_manager calls run_and_get()
and gets a recipe dict back (or None if cancelled).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

import recipe_loader
from icon_picker import IconPicker


# --------------------------------------------------------------------------
# Action catalog -- the four building blocks of any custom button.
# Each entry carries everything the wizard needs to render its tile, its
# detail page, and its preview line.
# --------------------------------------------------------------------------

_ACTIONS = [
    dict(
        key="open_url",
        title="Open a web page",
        icon="web-browser-symbolic",
        blurb="Search a site, look up a word, open a map.",
        example="Look up a selected word in the dictionary.",
        # Detail page wording
        detail_prompt="What's the address of the page to open?",
        # Placeholder is a complete working URL with {text_url} visible
        # at the end - instantly shows where the selection will land.
        detail_placeholder="https://www.google.com/search?q={text_url}",
        insert_label="Insert the selected text here",
        insert_token="{text_url}",   # URL-safe encoded
        preview_label="If you select \"hello world\" and click your button:",
        preview_verb="Opens this address in your browser:",
        examples=[
            ("Google",     "https://www.google.com/search?q={text_url}"),
            ("Wikipedia",  "https://en.wikipedia.org/wiki/Special:Search?search={text_url}"),
            ("YouTube",    "https://www.youtube.com/results?search_query={text_url}"),
            ("Translate",  "https://translate.google.com/?op=translate&text={text_url}"),
            ("Maps",       "https://www.google.com/maps/search/{text_url}"),
            ("Dictionary", "https://www.google.com/search?q=define+{text_url}"),
        ],
    ),
    dict(
        key="run_command",
        title="Run a command",
        icon="utilities-terminal-symbolic",
        blurb="Open a folder, run a script, anything you'd type in a terminal.",
        example="Open the selected file path in VS Code.",
        detail_prompt="What command should run?",
        # notify-send is a safe, instantly observable demo - runs without
        # side-effects and shows the {text_shell} token in context so the
        # user can see how to use it.
        detail_placeholder="notify-send 'You selected:' {text_shell}",
        insert_label="Insert the selected text here",
        insert_token="{text_shell}", # shell-quoted
        preview_label="If you select \"hello world\" and click your button:",
        preview_verb="Runs this command in the background:",
        warning="Commands can change or delete files. Test with something harmless first.",
        examples=[
            ("Open in default app", "xdg-open {text_shell}"),
            ("Show notification",   "notify-send 'You selected' {text_shell}"),
            ("Open in VS Code",     "code {text_shell}"),
            ("Open folder in Files", "xdg-open \"$(dirname {text_shell})\""),
            ("Speak aloud",         "spd-say {text_shell}"),
            ("Count characters",    "echo -n {text_shell} | wc -c | xargs -I N notify-send 'Length' 'N characters'"),
        ],
    ),
    dict(
        key="notify",
        title="Show a notification",
        icon="dialog-information-symbolic",
        blurb="Pop a short message on your desktop. Handy for confirmations.",
        example="Show how many characters you've selected.",
        detail_prompt="What should the notification say?",
        # Show the {text} token inline so the placement is obvious.
        detail_placeholder="Selected: {text}",
        insert_label="Insert the selected text",
        insert_token="{text}",
        preview_label="If you select \"hello world\" and click your button:",
        preview_verb="Shows this notification:",
        examples=[
            ("Plain",       "Selected: {text}"),
            ("Trimmed",     "Trimmed: {text_strip}"),
            ("Uppercase",   "{text_upper}"),
            ("Lowercase",   "{text_lower}"),
            ("URL-encoded", "URL-safe form: {text_url}"),
        ],
    ),
    dict(
        key="copy_transformed",
        title="Transform and copy",
        icon="edit-copy-symbolic",
        blurb="Make a tweaked copy of the text - wrap it, change its case, encode it.",
        example="Wrap the selection in markdown highlight: ==text==",
        detail_prompt="How should the copied text look?",
        # Markdown highlight is a useful starter that students/note-takers
        # will recognise. Demonstrates the wrap pattern without being
        # esoteric.
        detail_placeholder="=={text}==",
        insert_label="Insert the selected text",
        insert_token="{text}",
        preview_label="If you select \"hello world\" and click your button:",
        preview_verb="Copies this to your clipboard:",
        examples=[
            ("Markdown highlight", "=={text}=="),
            ("Markdown bold",      "**{text}**"),
            ("Markdown quote",     "> {text}"),
            ("Markdown code",      "`{text}`"),
            ("Markdown link",      "[{text}]()"),
            ("UPPERCASE",          "{text_upper}"),
            ("lowercase",          "{text_lower}"),
            ("In quotes",          "\"{text}\""),
            ("mOcKiNg cAsE",       "{text_mock}"),
        ],
    ),
]

_ACTION_BY_KEY = {a["key"]: a for a in _ACTIONS}

# Extra inserter buttons offered for copy_transformed (this is the one
# action where a casual user might genuinely want a transform pipeline).
_TRANSFORM_INSERTERS = [
    ("Selected text",       "{text}"),
    ("UPPERCASE",           "{text_upper}"),
    ("lowercase",           "{text_lower}"),
    ("URL-safe",            "{text_url}"),
    ("Trimmed",             "{text_strip}"),
    ("mOcKiNg cAsE",        "{text_mock}"),
]

_CONTENT_TYPES = [
    ("url",        "Web addresses"),
    ("email",      "Email addresses"),
    ("path",       "File paths"),
    ("command",    "Shell commands"),
    ("plain_text", "Plain text"),
]

_COMMON_ICONS = [
    "applications-internet", "web-browser-symbolic", "system-search-symbolic",
    "mail-send-symbolic", "utilities-terminal-symbolic", "folder-open-symbolic",
    "accessories-text-editor-symbolic", "accessories-calculator-symbolic",
    "starred-symbolic", "emblem-favorite-symbolic", "emoji-people-symbolic",
    "view-list-symbolic", "format-text-bold-symbolic",
    "edit-copy-symbolic", "document-edit-symbolic",
]

_SAMPLE_SELECTION = "hello world"


# Wizard-local CSS for the live popup mockup. Loaded once per process.
_MOCK_CSS = b"""
.lp-mock-popup {
    background-image: linear-gradient(to bottom, #1d2230, #161a24);
    border: 1px solid #2c3346;
    border-radius: 9px;
    padding: 6px 8px;
    box-shadow: 0 8px 20px rgba(0, 0, 0, 0.45);
}
.lp-mock-popup button {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 5px 7px;
    min-width: 28px;
    min-height: 28px;
    color: #e8ecf4;
}
.lp-mock-popup button.lp-mock-new {
    background-image: linear-gradient(to bottom right, #5B7DF5, #7C3AED);
    color: #ffffff;
    box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.35);
}
.lp-step-tile {
    background-color: #161a24;
    border: 1px solid #262c3a;
    border-radius: 12px;
    padding: 16px;
    transition: border-color 120ms ease, background-color 120ms ease;
}
.lp-step-tile:hover {
    border-color: #5B7DF5;
    background-color: #1d2230;
}
.lp-step-tile.lp-selected {
    border-color: #7C3AED;
    background-image: linear-gradient(135deg,
        rgba(91, 125, 245, 0.10),
        rgba(124, 58, 237, 0.10));
}
.lp-step-number {
    background-image: linear-gradient(to bottom right, #5B7DF5, #7C3AED);
    color: #ffffff;
    border-radius: 50%;
    min-width: 28px;
    min-height: 28px;
    font-weight: 700;
}
.lp-warning {
    background-color: rgba(220, 38, 38, 0.12);
    border: 1px solid rgba(220, 38, 38, 0.4);
    border-radius: 8px;
    padding: 8px 12px;
    color: #fca5a5;
}
.lp-preview-card {
    background-color: #14171f;
    border: 1px solid #262c3a;
    border-radius: 10px;
    padding: 12px;
}
.lp-preview-result {
    font-family: monospace;
    color: #a5b4fc;
}
/* The step tiles and preview cards are dark by design (they echo the
   premium popup mockup), so their text/icons must be light explicitly -
   otherwise in the LIGHT app theme the labels inherit dark text and end
   up dark-on-dark / unreadable. */
.lp-step-tile label,
.lp-preview-card label {
    color: #e8ecf4;
}
.lp-step-tile .dim-label,
.lp-preview-card .dim-label {
    color: #aeb6c7;
}
.lp-step-tile image,
.lp-preview-card image {
    color: #c7cedd;
}
"""
_css_installed = False


def _install_mock_css() -> None:
    global _css_installed
    if _css_installed:
        return
    try:
        from gi.repository import Gdk
        screen = Gdk.Screen.get_default()
        if screen is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(_MOCK_CSS)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_installed = True
    except Exception:
        pass


# --------------------------------------------------------------------------
# RecipeWizard
# --------------------------------------------------------------------------

class RecipeWizard:
    """Multi-step assistant. Same public API as the legacy wizard."""

    def __init__(self, parent=None, recipe: dict | None = None,
                 source_path=None) -> None:
        self._parent = parent
        self._source_path = source_path
        existing = recipe or {}
        action = existing.get("action") or {}
        # All decisions live in this dict so each page is a pure view of state.
        self._state = dict(
            name=existing.get("name", ""),
            tooltip=existing.get("tooltip", ""),
            icon=existing.get("icon", ""),
            action_type=action.get("type", "open_url"),
            template=action.get("template", ""),
            content_types=list(existing.get("content_types") or []),
        )
        self._editing = bool(recipe)
        self._result: dict | None = None
        self._assistant: Gtk.Assistant | None = None
        self._mock_button: Gtk.Button | None = None
        self._mock_label: Gtk.Label | None = None

    # ---- public entry point -------------------------------------------------

    def run_and_get(self) -> dict | None:
        _install_mock_css()
        a = Gtk.Assistant()
        a.set_title("Edit custom button" if self._editing else "New custom button")
        a.set_default_size(720, 560)
        a.set_modal(True)
        if self._parent is not None:
            a.set_transient_for(self._parent)
        a.set_icon_name("linuxpop")
        self._assistant = a

        # Build pages in order. add_page returns the index.
        self._add_intro_page(a)
        self._add_action_page(a)
        self._add_label_page(a)
        self._add_detail_page(a)
        self._add_filter_page(a)
        self._add_confirm_page(a)

        a.connect("apply", self._on_apply)
        a.connect("cancel", lambda *_: a.destroy())
        a.connect("close",  lambda *_: a.destroy())
        a.connect("destroy", lambda *_: Gtk.main_quit())

        a.show_all()
        Gtk.main()
        return self._result

    # ---- page 1: intro ------------------------------------------------------

    def _add_intro_page(self, a: Gtk.Assistant) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=24, margin_bottom=24,
                       margin_start=32, margin_end=32)

        title = Gtk.Label(xalign=0)
        title.set_markup(
            "<span size='xx-large' weight='bold'>Make your own LinuxPop button</span>"
        )
        page.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(xalign=0)
        subtitle.set_markup(
            "<span foreground='#9aa3b8'>Custom buttons turn any text you "
            "select into an action. We'll set yours up in a few short "
            "steps -- no coding needed.</span>"
        )
        subtitle.set_line_wrap(True)
        page.pack_start(subtitle, False, False, 0)

        # 3-step visual flow
        flow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14,
                       margin_top=18)
        for n, icon, head, body in [
            (1, "edit-select-all-symbolic", "Select",
             "Highlight any text\nin any app."),
            (2, "input-mouse-symbolic", "Click",
             "The LinuxPop popup\nshows up. Pick your\nnew button."),
            (3, "starred-symbolic", "Done",
             "Whatever you set up\nhappens. Open a URL,\nrun a command -- you choose."),
        ]:
            tile = self._build_step_tile(n, icon, head, body)
            flow.pack_start(tile, True, True, 0)
        page.pack_start(flow, False, False, 0)

        hint = Gtk.Label(xalign=0)
        hint.set_markup(
            "<span foreground='#9aa3b8' size='small'>"
            "Click <b>Next</b> when you're ready. You can go back at any point.</span>"
        )
        hint.set_margin_top(10)
        page.pack_start(hint, False, False, 0)

        idx = a.append_page(page)
        a.set_page_type(page, Gtk.AssistantPageType.INTRO)
        a.set_page_title(page, "Welcome")
        a.set_page_complete(page, True)

    def _build_step_tile(self, n: int, icon_name: str, head: str, body: str) -> Gtk.Widget:
        tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tile.get_style_context().add_class("lp-step-tile")

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        num = Gtk.Label(label=str(n))
        num.get_style_context().add_class("lp-step-number")
        num.set_xalign(0.5)
        num.set_yalign(0.5)
        top.pack_start(num, False, False, 0)
        img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DIALOG)
        top.pack_start(img, False, False, 0)
        tile.pack_start(top, False, False, 0)

        h = Gtk.Label(xalign=0)
        h.set_markup(f"<b>{head}</b>")
        tile.pack_start(h, False, False, 0)

        b = Gtk.Label(label=body, xalign=0)
        b.set_line_wrap(True)
        b.get_style_context().add_class("dim-label")
        tile.pack_start(b, False, False, 0)
        return tile

    # ---- page 2: choose action type -----------------------------------------

    def _add_action_page(self, a: Gtk.Assistant) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_top=20, margin_bottom=20,
                       margin_start=28, margin_end=28)

        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='x-large' weight='bold'>What should your button do?</span>")
        page.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(xalign=0)
        subtitle.set_markup(
            "<span foreground='#9aa3b8'>Pick one. You can change it later if you "
            "want to try something else.</span>"
        )
        page.pack_start(subtitle, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12, margin_top=8)
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(True)

        self._action_tiles: dict[str, Gtk.Widget] = {}
        for i, spec in enumerate(_ACTIONS):
            tile = self._build_action_tile(spec)
            grid.attach(tile, i % 2, i // 2, 1, 1)
            self._action_tiles[spec["key"]] = tile

        page.pack_start(grid, True, True, 0)
        self._reflect_action_selection()

        idx = a.append_page(page)
        a.set_page_title(page, "Pick action")
        a.set_page_complete(page, bool(self._state["action_type"]))

    def _build_action_tile(self, spec: dict) -> Gtk.Widget:
        # An EventBox-wrapped tile that becomes "selected" when clicked.
        ev = Gtk.EventBox()
        ev.set_above_child(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                      margin_top=14, margin_bottom=14,
                      margin_start=16, margin_end=16)
        box.get_style_context().add_class("lp-step-tile")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        img = Gtk.Image.new_from_icon_name(spec["icon"], Gtk.IconSize.DIALOG)
        row.pack_start(img, False, False, 0)
        head = Gtk.Label(xalign=0)
        head.set_markup(f"<span size='large' weight='bold'>{spec['title']}</span>")
        row.pack_start(head, False, False, 0)
        box.pack_start(row, False, False, 0)

        blurb = Gtk.Label(label=spec["blurb"], xalign=0)
        blurb.set_line_wrap(True)
        box.pack_start(blurb, False, False, 0)

        eg = Gtk.Label(xalign=0)
        eg.set_markup(f"<span foreground='#9aa3b8' size='small'>"
                      f"Example: {spec['example']}</span>")
        eg.set_line_wrap(True)
        box.pack_start(eg, False, False, 0)

        ev.add(box)

        def on_click(_w, _e):
            self._state["action_type"] = spec["key"]
            self._reflect_action_selection()
            # Refresh detail page since contents depend on chosen action
            self._refresh_detail_page()
            self._refresh_confirm_page()
            if self._assistant is not None:
                current_page = self._assistant.get_nth_page(
                    self._assistant.get_current_page())
                self._assistant.set_page_complete(current_page, True)
            return False
        ev.connect("button-press-event", on_click)
        # Keyboard activation too
        ev.set_can_focus(True)
        return ev

    def _reflect_action_selection(self) -> None:
        chosen = self._state["action_type"]
        for key, tile in (self._action_tiles if hasattr(self, "_action_tiles") else {}).items():
            box = tile.get_child()
            ctx = box.get_style_context()
            if key == chosen:
                ctx.add_class("lp-selected")
            else:
                ctx.remove_class("lp-selected")

    # ---- page 3: name & icon ------------------------------------------------

    def _add_label_page(self, a: Gtk.Assistant) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16,
                       margin_top=20, margin_bottom=20,
                       margin_start=24, margin_end=24)

        # ---- LEFT column: inputs ----
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='x-large' weight='bold'>Name it and pick an icon</span>")
        left.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(xalign=0)
        subtitle.set_markup(
            "<span foreground='#9aa3b8'>The icon is what shows up in the popup. "
            "The label is the tooltip when you hover.</span>"
        )
        subtitle.set_line_wrap(True)
        left.pack_start(subtitle, False, False, 0)

        # Tooltip / button label
        lbl = Gtk.Label(xalign=0)
        lbl.set_markup("<b>What's the button called?</b>")
        left.pack_start(lbl, False, False, 0)

        self._tooltip_entry = Gtk.Entry()
        self._tooltip_entry.set_text(self._state.get("tooltip") or "")
        self._tooltip_entry.set_placeholder_text("e.g. Search Wikipedia")
        self._tooltip_entry.connect("changed", self._on_label_input)
        left.pack_start(self._tooltip_entry, False, False, 0)

        # Icon picker chips + browse button
        ilbl = Gtk.Label(xalign=0)
        ilbl.set_markup("<b>Pick an icon</b>")
        ilbl.set_margin_top(6)
        left.pack_start(ilbl, False, False, 0)

        chips_scroll = Gtk.ScrolledWindow()
        chips_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        chips_scroll.set_min_content_height(110)
        chips_flow = Gtk.FlowBox()
        chips_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        chips_flow.set_max_children_per_line(7)
        chips_flow.set_homogeneous(True)
        for name in _COMMON_ICONS:
            btn = Gtk.Button()
            btn.set_relief(Gtk.ReliefStyle.NONE)
            btn.add(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.DND))
            btn.set_tooltip_text(name)
            btn.connect("clicked", self._on_icon_chip, name)
            chips_flow.add(btn)
        chips_scroll.add(chips_flow)
        left.pack_start(chips_scroll, False, False, 0)

        browse_btn = Gtk.Button(label="Browse all icons...")
        browse_btn.connect("clicked", self._on_browse_icons)
        left.pack_start(browse_btn, False, False, 0)

        page.pack_start(left, True, True, 0)

        # ---- RIGHT column: live preview ----
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        right.set_size_request(280, -1)

        plbl = Gtk.Label(xalign=0)
        plbl.set_markup("<b>Live preview</b>")
        right.pack_start(plbl, False, False, 0)

        explain = Gtk.Label(xalign=0)
        explain.set_markup(
            "<span foreground='#9aa3b8' size='small'>"
            "This is roughly how your button will appear in the LinuxPop popup, "
            "next to the built-in ones.</span>"
        )
        explain.set_line_wrap(True)
        right.pack_start(explain, False, False, 0)

        # Mock popup bar
        mock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        mock.get_style_context().add_class("lp-mock-popup")
        # A couple of dummy buttons + the new one (highlighted)
        for icon_name, tip in [
            ("edit-copy-symbolic", "Copy"),
            ("system-search-symbolic", "Search"),
        ]:
            b = Gtk.Button()
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.add(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR))
            b.set_tooltip_text(tip)
            b.set_can_focus(False)
            mock.pack_start(b, False, False, 0)
        # The user's button (highlighted)
        self._mock_button = Gtk.Button()
        self._mock_button.set_relief(Gtk.ReliefStyle.NONE)
        self._mock_button.get_style_context().add_class("lp-mock-new")
        self._mock_button.set_can_focus(False)
        self._refresh_mock_button()
        mock.pack_start(self._mock_button, False, False, 0)

        right.pack_start(mock, False, False, 0)

        self._mock_label = Gtk.Label(xalign=0)
        self._mock_label.set_line_wrap(True)
        self._mock_label.get_style_context().add_class("dim-label")
        self._refresh_mock_label()
        right.pack_start(self._mock_label, False, False, 0)

        page.pack_start(right, False, False, 0)

        a.append_page(page)
        a.set_page_title(page, "Name + icon")
        a.set_page_complete(page, self._label_page_complete())

    def _label_page_complete(self) -> bool:
        return bool((self._state.get("tooltip") or "").strip())

    def _on_label_input(self, _entry) -> None:
        self._state["tooltip"] = self._tooltip_entry.get_text()
        if self._assistant is not None:
            page = self._assistant.get_nth_page(self._assistant.get_current_page())
            self._assistant.set_page_complete(page, self._label_page_complete())
        self._refresh_mock_button()
        self._refresh_mock_label()
        self._refresh_confirm_page()

    def _on_icon_chip(self, _btn, name: str) -> None:
        self._state["icon"] = name
        self._refresh_mock_button()
        self._refresh_confirm_page()

    def _on_browse_icons(self, _btn) -> None:
        picker = IconPicker(parent=self._assistant,
                            initial=self._state.get("icon") or "applications-other")
        chosen = picker.run()
        if chosen:
            self._state["icon"] = chosen
            self._refresh_mock_button()
            self._refresh_confirm_page()

    def _refresh_mock_button(self) -> None:
        if self._mock_button is None:
            return
        for ch in self._mock_button.get_children():
            self._mock_button.remove(ch)
        icon_name = self._state.get("icon") or "applications-other"
        self._mock_button.add(Gtk.Image.new_from_icon_name(
            icon_name, Gtk.IconSize.LARGE_TOOLBAR))
        self._mock_button.set_tooltip_text(
            self._state.get("tooltip") or "Your button")
        self._mock_button.show_all()

    def _refresh_mock_label(self) -> None:
        if self._mock_label is None:
            return
        name = (self._state.get("tooltip") or "").strip() or "Your button"
        spec = _ACTION_BY_KEY[self._state["action_type"]]
        self._mock_label.set_markup(
            f"Hovering shows: <b>{GLib.markup_escape_text(name)}</b>\n"
            f"<span size='small'>Clicking will: {GLib.markup_escape_text(spec['blurb'].lower())}</span>"
        )

    # ---- page 4: action details --------------------------------------------

    def _add_detail_page(self, a: Gtk.Assistant) -> None:
        # We hold a reference to the outer container so we can wipe and
        # rebuild it when the user changes action type on page 2.
        self._detail_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                         spacing=12,
                                         margin_top=20, margin_bottom=20,
                                         margin_start=28, margin_end=28)
        self._refresh_detail_page()
        a.append_page(self._detail_container)
        a.set_page_title(self._detail_container, "Details")
        a.set_page_complete(self._detail_container, self._detail_page_complete())

    def _detail_page_complete(self) -> bool:
        # Pre-filled placeholder text doesn't count - only actual user
        # input (typed, pasted, or picked from a chip). _placeholder_active
        # is True only while the dimmed sample is on screen.
        if getattr(self, "_placeholder_active", False):
            return False
        return bool((self._state.get("template") or "").strip())

    def _refresh_detail_page(self) -> None:
        if not hasattr(self, "_detail_container"):
            return
        # Wipe existing children
        for ch in list(self._detail_container.get_children()):
            self._detail_container.remove(ch)

        spec = _ACTION_BY_KEY[self._state["action_type"]]

        title = Gtk.Label(xalign=0)
        title.set_markup(f"<span size='x-large' weight='bold'>{spec['title']}</span>")
        self._detail_container.pack_start(title, False, False, 0)

        prompt = Gtk.Label(xalign=0)
        prompt.set_markup(f"<b>{spec['detail_prompt']}</b>")
        self._detail_container.pack_start(prompt, False, False, 0)

        # Template editor
        tmpl_scroll = Gtk.ScrolledWindow()
        tmpl_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        tmpl_scroll.set_min_content_height(70)
        tmpl_scroll.set_shadow_type(Gtk.ShadowType.IN)
        self._template_view = Gtk.TextView()
        self._template_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._template_view.override_font(
            Pango.FontDescription("monospace 11"))
        # Visible input-field background so the user can see it's editable.
        self._template_view.get_style_context().add_class("lp-cmd-edit")
        self._template_buffer = self._template_view.get_buffer()

        # Style tag for the dimmed sample text. Has to live on the
        # buffer's tag table - recreated per page render because we
        # rebuild the whole detail container when action type changes.
        self._placeholder_tag = self._template_buffer.create_tag(
            "lp-placeholder",
            foreground="#6b7080",     # muted grey vs the normal #e8ecf4
            style=Pango.Style.ITALIC,
        )

        # If the user already typed something on a previous visit, show
        # that and treat as real input. Otherwise drop the sample text
        # in with the dim-italic tag, and remember it's "just a hint".
        existing = self._state.get("template") or ""
        if existing:
            self._template_buffer.set_text(existing)
            self._placeholder_active = False
        else:
            placeholder_text = spec.get("detail_placeholder") or ""
            if placeholder_text:
                self._template_buffer.set_text(placeholder_text)
                start, end = self._template_buffer.get_bounds()
                self._template_buffer.apply_tag(
                    self._placeholder_tag, start, end)
                self._placeholder_active = True
            else:
                self._placeholder_active = False

        # Connect AFTER the initial set_text so the seed itself doesn't
        # trip the change handler and clobber _placeholder_active.
        self._template_buffer.connect("changed", self._on_template_changed)
        # Wipe the dim sample as soon as the user clicks into the field -
        # avoids the weird state where their typing lands next to greyed
        # placeholder content.
        self._template_view.connect(
            "focus-in-event", self._on_template_focus_in)
        tmpl_scroll.add(self._template_view)
        self._detail_container.pack_start(tmpl_scroll, False, False, 0)

        # Inserter row -- plain-language buttons that drop the right token in
        inserter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if spec["key"] == "copy_transformed":
            # Several flavor options
            for label, token in _TRANSFORM_INSERTERS:
                btn = Gtk.Button(label=f"Insert {label}")
                btn.set_tooltip_text(
                    "Click to drop this into your template at the cursor.")
                btn.connect("clicked", self._on_insert, token)
                inserter_row.pack_start(btn, False, False, 0)
        else:
            btn = Gtk.Button(label=spec["insert_label"])
            btn.get_style_context().add_class("suggested-action")
            btn.set_tooltip_text(
                "Click to drop the selected text into your "
                + ("URL" if spec["key"] == "open_url"
                   else "command" if spec["key"] == "run_command"
                   else "message")
                + " at the cursor.")
            btn.connect("clicked", self._on_insert, spec["insert_token"])
            inserter_row.pack_start(btn, False, False, 0)
        self._detail_container.pack_start(inserter_row, False, False, 0)

        # More-placeholders chip row: dynamic tokens recipes pick up
        # from the snippet engine. Inserting one of these drops a tag
        # like {date} or {clipboard} into the template at the cursor;
        # it gets resolved when the button is actually clicked, not now.
        extra_label = Gtk.Label(xalign=0)
        extra_label.set_markup(
            "<span foreground='#9aa3b8' size='small'>"
            "Or insert a dynamic value (filled in when the button runs):</span>")
        extra_label.set_margin_top(6)
        self._detail_container.pack_start(extra_label, False, False, 0)
        extra_flow = Gtk.FlowBox()
        extra_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        extra_flow.set_max_children_per_line(6)
        extra_flow.set_column_spacing(4)
        extra_flow.set_row_spacing(4)
        for chip_label, chip_token, chip_tip in [
            ("{date}",       "{date}",       "Today's date, e.g. 2026-05-27"),
            ("{time}",       "{time}",       "Current time, e.g. 14:30"),
            ("{weekday}",    "{weekday}",    "Name of the day"),
            ("{date:+7d}",   "{date:+7d}",   "Date math: +7d, -1w, +3m, +2y"),
            ("{name}",       "{name}",       "Your full name (from your user account)"),
            ("{clipboard}",  "{clipboard}",  "Whatever's on your clipboard when the button runs"),
            ("{selection}",  "{selection}",  "Whatever text is highlighted on screen"),
        ]:
            chip = Gtk.Button(label=chip_label)
            chip.set_tooltip_text(chip_tip)
            chip.connect("clicked",
                          lambda _b, t=chip_token: self._on_insert(None, t))
            extra_flow.add(chip)
        self._detail_container.pack_start(extra_flow, False, False, 0)

        # "Try also" chip row - replaces the template with a known-good
        # example. Saves the user from having to invent valid patterns
        # by hand the first time they meet an action type.
        examples = spec.get("examples") or []
        if examples:
            ex_label = Gtk.Label(xalign=0)
            ex_label.set_markup(
                "<span foreground='#9aa3b8' size='small'>"
                "Try one of these - click to fill the box above:</span>")
            ex_label.set_margin_top(4)
            self._detail_container.pack_start(ex_label, False, False, 0)

            ex_flow = Gtk.FlowBox()
            ex_flow.set_selection_mode(Gtk.SelectionMode.NONE)
            ex_flow.set_homogeneous(False)
            ex_flow.set_max_children_per_line(8)
            ex_flow.set_row_spacing(4)
            ex_flow.set_column_spacing(4)
            for label, template in examples:
                btn = Gtk.Button(label=label)
                btn.set_relief(Gtk.ReliefStyle.NORMAL)
                btn.set_tooltip_text(template)
                btn.connect("clicked", self._on_example_pick, template)
                ex_flow.add(btn)
            self._detail_container.pack_start(ex_flow, False, False, 0)

        # Warning (run_command)
        if spec.get("warning"):
            warn = Gtk.Label(xalign=0)
            warn.set_markup(
                "<b>Heads up:</b> " + GLib.markup_escape_text(spec["warning"]))
            warn.set_line_wrap(True)
            warn.get_style_context().add_class("lp-warning")
            self._detail_container.pack_start(warn, False, False, 0)

        # Preview card
        prev_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        prev_card.get_style_context().add_class("lp-preview-card")
        p_head = Gtk.Label(xalign=0)
        p_head.set_markup(f"<b>{spec['preview_label']}</b>")
        prev_card.pack_start(p_head, False, False, 0)
        p_verb = Gtk.Label(xalign=0)
        p_verb.set_markup(
            f"<span foreground='#9aa3b8' size='small'>{spec['preview_verb']}</span>")
        prev_card.pack_start(p_verb, False, False, 0)
        self._preview_result = Gtk.Label(xalign=0)
        self._preview_result.set_line_wrap(True)
        self._preview_result.set_selectable(True)
        self._preview_result.get_style_context().add_class("lp-preview-result")
        self._refresh_preview_result()
        prev_card.pack_start(self._preview_result, False, False, 0)
        self._detail_container.pack_start(prev_card, False, False, 0)

        self._detail_container.show_all()

    def _on_template_changed(self, buf: Gtk.TextBuffer) -> None:
        # Any actual buffer mutation means the sample text is no longer
        # what's there - clear the "this is just a hint" flag so the
        # Next button enables and the new content is real input.
        if getattr(self, "_placeholder_active", False):
            self._placeholder_active = False
            start, end = buf.get_bounds()
            buf.remove_tag(self._placeholder_tag, start, end)
        start, end = buf.get_start_iter(), buf.get_end_iter()
        self._state["template"] = buf.get_text(start, end, True)
        if self._assistant is not None:
            page = self._detail_container
            self._assistant.set_page_complete(page, self._detail_page_complete())
        self._refresh_preview_result()
        self._refresh_confirm_page()

    def _on_template_focus_in(self, _view, _event) -> bool:
        """Clear the dim placeholder on first focus so the user types into
        an empty field, not into the middle of the sample."""
        if getattr(self, "_placeholder_active", False):
            self._placeholder_active = False
            self._template_buffer.set_text("")
        return False  # don't intercept default focus handling

    def _on_insert(self, _btn, token: str) -> None:
        # If the sample text is still on screen, replace it wholesale
        # rather than appending the token to it - otherwise the user
        # ends up with "=={text}==<their token>" which is rarely what
        # they want.
        if getattr(self, "_placeholder_active", False):
            self._placeholder_active = False
            self._template_buffer.set_text(token)
        else:
            if self._template_buffer.get_has_selection():
                self._template_buffer.delete_selection(True, True)
            self._template_buffer.insert_at_cursor(token)
        self._template_view.grab_focus()

    def _on_example_pick(self, _btn, template: str) -> None:
        """Replace the template buffer with the chosen example. The user
        can still hand-edit afterwards - chips are starting points, not
        final answers."""
        self._template_buffer.set_text(template)
        self._template_view.grab_focus()

    def _refresh_preview_result(self) -> None:
        if not hasattr(self, "_preview_result"):
            return
        rendered = recipe_loader._render(
            self._state.get("template") or "", _SAMPLE_SELECTION)
        if not rendered.strip():
            # When the placeholder is showing, the box isn't empty but
            # state.template is - explain that explicitly so the user
            # doesn't wonder why the preview is blank.
            hint = ("(the grey text above is just an example - type "
                    "your own or click one of the chips to set it)"
                    if getattr(self, "_placeholder_active", False)
                    else "(nothing yet - pick a template above)")
            self._preview_result.set_text(hint)
            return
        spec = _ACTION_BY_KEY[self._state["action_type"]]
        if spec["key"] == "open_url":
            self._preview_result.set_text(rendered.strip())
        elif spec["key"] == "run_command":
            self._preview_result.set_text("$ " + rendered.strip())
        elif spec["key"] == "notify":
            self._preview_result.set_text(rendered.strip())
        else:
            self._preview_result.set_text(rendered.strip())

    # ---- page 5: when to show -----------------------------------------------

    def _add_filter_page(self, a: Gtk.Assistant) -> None:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=20, margin_bottom=20,
                       margin_start=28, margin_end=28)

        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='x-large' weight='bold'>When should it show up?</span>")
        page.pack_start(title, False, False, 0)

        subtitle = Gtk.Label(xalign=0)
        subtitle.set_markup(
            "<span foreground='#9aa3b8'>You can leave this as 'always' -- "
            "most people do.</span>"
        )
        page.pack_start(subtitle, False, False, 0)

        self._always_radio = Gtk.RadioButton.new_with_label_from_widget(
            None, "Always -- show on any selected text")
        self._always_radio.set_active(not self._state["content_types"])
        page.pack_start(self._always_radio, False, False, 0)

        self._only_radio = Gtk.RadioButton.new_with_label_from_widget(
            self._always_radio, "Only when I've selected one of these:")
        self._only_radio.set_active(bool(self._state["content_types"]))
        page.pack_start(self._only_radio, False, False, 0)

        # Indented checkboxes
        cb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                        margin_start=28)
        self._type_checks: dict[str, Gtk.CheckButton] = {}
        for key, label in _CONTENT_TYPES:
            cb = Gtk.CheckButton(label=label)
            cb.set_active(key in self._state["content_types"])
            cb.connect("toggled", self._on_filter_changed)
            cb_box.pack_start(cb, False, False, 0)
            self._type_checks[key] = cb
        page.pack_start(cb_box, False, False, 0)

        # Wire radios so checkboxes only matter when "Only" is active
        def sync(*_):
            only = self._only_radio.get_active()
            cb_box.set_sensitive(only)
            self._on_filter_changed()
        self._always_radio.connect("toggled", sync)
        self._only_radio.connect("toggled", sync)
        sync()

        a.append_page(page)
        a.set_page_title(page, "When to show")
        a.set_page_complete(page, True)

    def _on_filter_changed(self, *_):
        if self._only_radio.get_active():
            self._state["content_types"] = [
                k for k, cb in self._type_checks.items() if cb.get_active()
            ]
        else:
            self._state["content_types"] = []
        self._refresh_confirm_page()

    # ---- page 6: confirm ----------------------------------------------------

    def _add_confirm_page(self, a: Gtk.Assistant) -> None:
        self._confirm_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                          spacing=14,
                                          margin_top=20, margin_bottom=20,
                                          margin_start=28, margin_end=28)
        self._refresh_confirm_page()
        a.append_page(self._confirm_container)
        a.set_page_type(self._confirm_container, Gtk.AssistantPageType.CONFIRM)
        a.set_page_title(self._confirm_container, "Done")
        a.set_page_complete(self._confirm_container, True)

    def _refresh_confirm_page(self) -> None:
        if not hasattr(self, "_confirm_container"):
            return
        for ch in list(self._confirm_container.get_children()):
            self._confirm_container.remove(ch)

        spec = _ACTION_BY_KEY[self._state["action_type"]]

        title = Gtk.Label(xalign=0)
        title.set_markup("<span size='x-large' weight='bold'>Looks good?</span>")
        self._confirm_container.pack_start(title, False, False, 0)

        sub = Gtk.Label(xalign=0)
        sub.set_markup(
            "<span foreground='#9aa3b8'>Press <b>Apply</b> to save. You can "
            "edit or delete this button later from the Plugin Manager.</span>"
        )
        sub.set_line_wrap(True)
        self._confirm_container.pack_start(sub, False, False, 0)

        # Summary card
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.get_style_context().add_class("lp-preview-card")

        icon_img = Gtk.Image.new_from_icon_name(
            self._state.get("icon") or "applications-other", Gtk.IconSize.DIALOG)
        card.pack_start(icon_img, False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        name = (self._state.get("tooltip") or "").strip() or "(unnamed)"
        nlbl = Gtk.Label(xalign=0)
        nlbl.set_markup(f"<span size='large' weight='bold'>"
                        f"{GLib.markup_escape_text(name)}</span>")
        info.pack_start(nlbl, False, False, 0)

        action_descr = Gtk.Label(xalign=0)
        action_descr.set_markup(
            f"<span foreground='#9aa3b8'>{GLib.markup_escape_text(spec['blurb'])}</span>")
        action_descr.set_line_wrap(True)
        info.pack_start(action_descr, False, False, 0)

        # Render the actual outcome for the sample selection
        rendered = recipe_loader._render(
            self._state.get("template") or "", _SAMPLE_SELECTION)
        verb_lbl = Gtk.Label(xalign=0)
        verb_lbl.set_markup(
            f"<span size='small'>When you select "
            f"<b>\"{_SAMPLE_SELECTION}\"</b> and click it:</span>")
        info.pack_start(verb_lbl, False, False, 0)

        result_lbl = Gtk.Label(xalign=0)
        result_lbl.set_line_wrap(True)
        result_lbl.set_selectable(True)
        result_lbl.get_style_context().add_class("lp-preview-result")
        if spec["key"] == "run_command":
            result_lbl.set_text("$ " + rendered.strip())
        else:
            result_lbl.set_text(rendered.strip() or "(empty)")
        info.pack_start(result_lbl, False, False, 0)

        # When to show
        when = ", ".join(
            label for k, label in _CONTENT_TYPES
            if k in self._state["content_types"]
        ) or "any selected text"
        when_lbl = Gtk.Label(xalign=0)
        when_lbl.set_markup(
            f"<span size='small' foreground='#9aa3b8'>"
            f"Shows on: {GLib.markup_escape_text(when)}</span>")
        info.pack_start(when_lbl, False, False, 0)

        card.pack_start(info, True, True, 0)
        self._confirm_container.pack_start(card, False, False, 0)

        # Validation hint if something's missing
        provisional = self._build_recipe_dict()
        errors = recipe_loader.validate(provisional)
        if errors:
            warn = Gtk.Label(xalign=0)
            warn.set_markup("<b>One more thing:</b> " +
                            GLib.markup_escape_text(errors[0]))
            warn.set_line_wrap(True)
            warn.get_style_context().add_class("lp-warning")
            self._confirm_container.pack_start(warn, False, False, 0)
        self._confirm_container.show_all()

    # ---- save ---------------------------------------------------------------

    def _build_recipe_dict(self) -> dict:
        # Derive an internal name from the user's button label if they're
        # creating fresh -- they shouldn't have to invent a filename.
        name = self._state.get("name") or _slugify(
            self._state.get("tooltip") or "custom-button")
        return dict(
            name=name,
            tooltip=(self._state.get("tooltip") or "").strip(),
            icon=self._state.get("icon") or "applications-other",
            content_types=list(self._state["content_types"]),
            action=dict(
                type=self._state["action_type"],
                template=(self._state.get("template") or "").strip(),
            ),
        )

    def _on_apply(self, a: Gtk.Assistant) -> None:
        recipe = self._build_recipe_dict()
        errors = recipe_loader.validate(recipe)
        if errors:
            msg = Gtk.MessageDialog(
                transient_for=a, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Couldn't save just yet",
            )
            msg.format_secondary_text("\n".join(errors))
            msg.run()
            msg.destroy()
            return
        self._result = recipe
        # IMPORTANT: don't destroy the assistant from inside the 'apply'
        # signal handler -- GtkAssistant fires 'apply' immediately followed
        # by 'close' on a confirm-page, and tearing the widget down mid-
        # cascade crashes when the 'close' emission lands on a half-freed
        # object. Defer to the next idle so the signal cascade finishes
        # first, then destroy cleanly.
        GLib.idle_add(a.destroy)


def _slugify(name: str) -> str:
    out = []
    for ch in name.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "custom-button"
