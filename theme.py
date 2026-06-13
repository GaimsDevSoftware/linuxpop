"""Premium theme for LinuxPop -- one CSS provider for every window.

The base CSS is the dark palette tuned to the LinuxPop app icon (deep
cobalt base + blue->violet->magenta accent gradient). A light variant
is generated at runtime by remapping the surface / text hex values
while keeping the accent gradient intact (it reads well on both).

Mode resolution:
  - "dark"   -> always dark
  - "light"  -> always light
  - "system" -> ask Gtk.Settings whether the user's system theme is dark
                (gtk-application-prefer-dark-theme, or the theme-name
                containing "dark"). Falls back to dark if uncertain.

The installer is idempotent and hot-reloadable: calling it again with a
different mode swaps the active provider without restarting.
"""
from __future__ import annotations

import os
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

_PREMIUM_CSS = b"""
/* ============================================================
   LinuxPop -- premium dark theme
   palette:
     base       #0e1118  (deep cobalt)
     surface    #1c2231  (cards / list rows -- lifted for contrast)
     elevated   #262d3f  (hover / selected)
     border     #3a4258  (visible at a glance, not hairline)
     text       #f0f3fa
     muted      #b8c0d4  (higher contrast for dim labels)
     accent-1   #5B7DF5  (cobalt blue)
     accent-2   #7C3AED  (royal violet)
     accent-3   #EC4899  (magenta pink)
   ============================================================ */

window,
dialog,
.background,
hdypreferenceswindow,
hdypreferencespage,
hdypreferencesgroup {
    background-color: #0e1118;
    color: #f0f3fa;
}

/* Header bar: subtle gradient that picks up the accent palette without
   shouting. Adds visual depth at the top of every window. */
headerbar,
.titlebar {
    background-image: linear-gradient(to bottom,
        #232a3c 0%,
        #1a1f2e 100%);
    background-color: #1a1f2e;
    border-bottom: 1px solid #3a4258;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06),
                0 1px 0 rgba(0, 0, 0, 0.35);
    color: #f0f3fa;
    padding: 6px 10px;
    min-height: 38px;
}

headerbar label,
.titlebar label {
    color: #f0f3fa;
    font-weight: 600;
    letter-spacing: 0.01em;
}

/* Typography */
label {
    color: #f0f3fa;
}

label.title,
.title {
    font-weight: 600;
    letter-spacing: 0.005em;
}

label.subtitle,
label.dim-label,
.dim-label {
    color: #b8c0d4;
    font-size: 0.92em;
}

/* ----- libhandy boxed list (the GNOME-Settings card look) ----- */
list.boxed-list,
list.content {
    background-color: #1c2231;
    border: 1px solid #3a4258;
    border-radius: 12px;
    padding: 2px;
    /* Subtle inner top highlight + slightly stronger bottom border to give
       cards real lift instead of bleeding into the dark background. */
    box-shadow: 0 1px 0 rgba(255, 255, 255, 0.06) inset,
                0 -1px 0 rgba(0, 0, 0, 0.30) inset,
                0 6px 14px rgba(0, 0, 0, 0.30);
}

list.boxed-list > row,
list.content > row,
hdyactionrow {
    background-color: transparent;
    color: #f0f3fa;
    padding: 10px 14px;
    border-bottom: 1px solid #2c3346;
    transition: background-color 120ms ease;
}

/* HdyExpanderRow ("Per-service method (advanced)") was not covered by the
   row selectors above. On a non-Adwaita GTK theme (e.g. KDE/Breeze) its
   header fell back to a light background with near-invisible light text.
   Force the whole expander - header + nested rows - into the dark palette. */
hdyexpanderrow,
hdyexpanderrow > box,
hdyexpanderrow box,
hdyexpanderrow list,
hdyexpanderrow row {
    background-color: transparent;
    color: #f0f3fa;
}

list.boxed-list > row:last-child,
list.content > row:last-child {
    border-bottom: none;
}

list.boxed-list > row:hover,
list.content > row:hover {
    background-color: #262d3f;
}

list.boxed-list > row:selected,
list.content > row:selected,
list > row:selected {
    background-image: linear-gradient(to right,
        rgba(91, 125, 245, 0.18),
        rgba(124, 58, 237, 0.18));
    color: #ffffff;
}

/* ----- buttons ----- */
button {
    background-image: linear-gradient(to bottom, #1f2433, #1a1f2c);
    background-color: #262d3f;
    color: #f0f3fa;
    border: 1px solid #2c3346;
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 500;
    transition: background-color 120ms ease,
                border-color 120ms ease,
                box-shadow 120ms ease;
}

button:hover {
    background-image: linear-gradient(to bottom, #262c3e, #1f2433);
    border-color: #3a4258;
}

button:active,
button:checked {
    background-image: linear-gradient(to bottom, #1a1f2c, #1f2433);
    border-color: #5B7DF5;
}

button:focus {
    outline: none;
    box-shadow: 0 0 0 2px rgba(91, 125, 245, 0.35);
    border-color: #5B7DF5;
}

button:disabled {
    color: #4a5266;
    background-image: none;
    background-color: #14171f;
    border-color: #1f2433;
}

/* Suggested action: the headline blue->violet gradient */
button.suggested-action {
    background-image: linear-gradient(to bottom right, #5B7DF5, #7C3AED);
    color: #ffffff;
    border: none;
    box-shadow: 0 2px 6px rgba(91, 125, 245, 0.25);
    text-shadow: 0 1px 0 rgba(0, 0, 0, 0.2);
}

button.suggested-action:hover {
    background-image: linear-gradient(to bottom right, #6B8AF7, #8B4CF0);
    box-shadow: 0 4px 12px rgba(91, 125, 245, 0.35);
}

button.suggested-action:active {
    background-image: linear-gradient(to bottom right, #4A6CE3, #6929DB);
}

button.destructive-action {
    background-image: linear-gradient(to bottom right, #DC2626, #B91C1C);
    color: #ffffff;
    border: none;
    box-shadow: 0 2px 6px rgba(220, 38, 38, 0.25);
}

button.destructive-action:hover {
    background-image: linear-gradient(to bottom right, #EF4444, #DC2626);
}

button.flat,
button.image-button {
    background-image: none;
    background-color: transparent;
    border-color: transparent;
    box-shadow: none;
}

button.flat:hover,
button.image-button:hover {
    background-color: rgba(91, 125, 245, 0.12);
    border-color: transparent;
}

/* ----- entries / search ----- */
entry,
.entry,
searchentry,
spinbutton {
    background-color: #181d2a;
    color: #f0f3fa;
    border: 1px solid #3a4258;
    border-radius: 8px;
    padding: 6px 10px;
    caret-color: #5B7DF5;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.25) inset;
    transition: border-color 120ms ease, box-shadow 120ms ease;
}

entry:focus,
searchentry:focus,
spinbutton:focus {
    border-color: #5B7DF5;
    box-shadow: 0 0 0 2px rgba(91, 125, 245, 0.25);
    outline: none;
}

entry selection,
entry > selection {
    background-color: rgba(91, 125, 245, 0.45);
    color: #ffffff;
}

placeholder,
entry placeholder {
    color: #5a6378;
}

/* ----- switches ----- */
switch {
    background-color: #2a3145;
    border: 1px solid #353c52;
    border-radius: 14px;
    min-width: 44px;
    min-height: 24px;
}

switch:checked {
    background-image: linear-gradient(to right, #5B7DF5, #7C3AED);
    background-color: #5B7DF5;
    border-color: #5B7DF5;
}

switch slider {
    background-color: #f0f3fa;
    border-radius: 50%;
    border: none;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
    min-width: 18px;
    min-height: 18px;
    margin: 2px;
}

/* ----- combobox ----- */
combobox button,
combobox box.linked button {
    background-image: linear-gradient(to bottom, #1f2433, #1a1f2c);
    border: 1px solid #2c3346;
    color: #f0f3fa;
    border-radius: 8px;
}

combobox arrow {
    color: #b8c0d4;
}

/* ----- tray / panel menus ----------------------------------------
   The Ayatana tray menu is rendered by the desktop panel (Cinnamon),
   not by our process, but our CSS provider can leak into menu labels
   on some indicator implementations. Reset every styleable property
   on the menu widget tree to its default so the panel's own theme
   wins on colour - prevents the "dark text on dark panel" disappearing
   trick that happens when our colours get applied to the panel-side
   rendering. */
menu,
menu menuitem,
menu menuitem label,
menu separator {
    background-color: unset;
    background-image: unset;
    color: unset;
    border: unset;
    box-shadow: unset;
}

/* Hover/highlight on the menu item the pointer is over. Uses the
   accent overlay at low opacity so the panel's own background colour
   shows through - reads correctly on both a light and a dark panel
   without overriding the label colour the panel set. */
menu menuitem:hover,
menu menuitem:focus,
menu menuitem.highlight {
    background-color: rgba(91, 125, 245, 0.22);
    border-radius: 4px;
}

/* ----- popovers ----- */
popover,
popover.background {
    background-color: #1c2231;
    border: 1px solid #3a4258;
    border-radius: 10px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.45);
    color: #f0f3fa;
    padding: 4px;
}

popover button,
popover modelbutton {
    background-image: none;
    background-color: transparent;
    border: none;
    color: #f0f3fa;
    padding: 6px 10px;
    border-radius: 6px;
}

popover button:hover,
popover modelbutton:hover {
    background-color: #262d3f;
}

/* ----- scrolled windows + scrollbars ----- */
scrolledwindow {
    background-color: transparent;
}

scrollbar {
    background-color: transparent;
    border: none;
}

scrollbar slider {
    background-color: #2c3346;
    border-radius: 6px;
    min-width: 6px;
    min-height: 6px;
}

scrollbar slider:hover {
    background-color: #3a4258;
}

/* ----- frames / separators ----- */
frame {
    border: 1px solid #3a4258;
    border-radius: 10px;
    background-color: #1c2231;
}

separator {
    background-color: #3a4258;
    min-width: 1px;
    min-height: 1px;
}

/* ----- GtkAssistant left sidebar (recipe wizard's page list).
   The internal Box has class 'sidebar', child labels are inactive,
   the current page's label adds the 'highlight' class. Default
   theme paints these light-on-light which is illegible against the
   dark content panel. */
.sidebar {
    background-color: #161a24;
    border-right: 1px solid #3a4258;
}

.sidebar label {
    color: #b8c0d4;
    padding: 8px 16px;
    background-color: transparent;
}

.sidebar label.highlight {
    color: #f0f3fa;
    background-image: linear-gradient(to right,
        rgba(91, 125, 245, 0.18),
        rgba(124, 58, 237, 0.18));
    box-shadow: inset 3px 0 0 #7C3AED;
    font-weight: 600;
}

/* ----- libhandy view switcher / stack switcher (the tab strip in the
   Plugin Manager and other multi-page windows). These render as buttons
   in the header bar; the generic button styling above would give each
   tab a border + gradient + 14 px horizontal padding which clips long
   labels like 'Installed'. Strip the heavy styling so labels fit. */
hdyviewswitcher button,
stackswitcher button {
    background-image: none;
    background-color: transparent;
    border: none;
    border-radius: 6px;
    padding: 6px 8px;
    box-shadow: none;
    color: #b8c0d4;
    font-weight: 500;
}

hdyviewswitcher button:hover,
stackswitcher button:hover {
    background-image: none;
    background-color: rgba(91, 125, 245, 0.10);
    color: #f0f3fa;
}

hdyviewswitcher button:checked,
stackswitcher button:checked {
    background-image: none;
    background-color: rgba(124, 58, 237, 0.15);
    color: #f0f3fa;
    box-shadow: inset 0 -2px 0 #7C3AED;
}

hdyviewswitcher button label,
stackswitcher button label {
    /* Make sure long labels don't get ellipsised by an inherited rule */
    margin: 0 2px;
}

/* ----- notebooks / tabs (used by some dialogs) ----- */
notebook {
    background-color: transparent;
}

notebook > header {
    background-color: #141823;
    border-bottom: 1px solid #3a4258;
}

notebook > header > tabs > tab {
    background-color: transparent;
    color: #b8c0d4;
    border: none;
    padding: 8px 14px;
    transition: color 120ms ease;
}

notebook > header > tabs > tab:checked {
    color: #f0f3fa;
    box-shadow: inset 0 -2px 0 #5B7DF5;
}

notebook > header > tabs > tab:hover {
    color: #f0f3fa;
}

/* ----- tooltips ----- */
tooltip,
tooltip.background {
    background-color: #262d3f;
    color: #f0f3fa;
    border: 1px solid #2c3346;
    border-radius: 6px;
    padding: 4px 8px;
}

/* ----- check / radio buttons ----- */
check,
radio {
    background-color: #14171f;
    border: 1px solid #2c3346;
    color: #f0f3fa;
    min-width: 16px;
    min-height: 16px;
}

check:checked,
radio:checked {
    background-image: linear-gradient(to bottom right, #5B7DF5, #7C3AED);
    border-color: #5B7DF5;
    color: #ffffff;
}

/* ----- progress bars ----- */
progressbar trough {
    background-color: #262d3f;
    border: 1px solid #3a4258;
    border-radius: 6px;
    min-height: 6px;
}

progressbar progress {
    background-image: linear-gradient(to right, #5B7DF5, #7C3AED);
    border-radius: 6px;
    min-height: 6px;
}

/* Generic TextView coverage so multi-line text widgets pick up the dark
   theme by default. Without this, GTK falls back to the system theme's
   white text region (which sticks out in the terminal confirm dialog,
   the recipe wizard, etc.). */
textview,
textview text {
    background-color: transparent;
    background-image: none;
    color: #f0f3fa;
    caret-color: #5B7DF5;
}

/* Terminal confirm dialog: read-only "preview" state. Flat, no box,
   sits on the dialog's dark surface the way a label would. */
textview.lp-cmd-preview,
textview.lp-cmd-preview text {
    background-color: transparent;
    background-image: none;
    color: #f0f3fa;
    padding: 4px 6px;
}

/* Terminal confirm dialog: editable state after Edit was pressed.
   Picks up the entry-field look so it's obvious you can type now. */
textview.lp-cmd-edit,
textview.lp-cmd-edit text {
    background-color: #181d2a;
    background-image: none;
    color: #f0f3fa;
    caret-color: #5B7DF5;
    padding: 6px 10px;
}

/* ----- Snippet / clipboard picker -----
   Force every container inside the picker window to inherit the window
   background instead of the default GTK list/notebook tones. Without
   this the listbox area stays charcoal even in light mode because GTK
   paints a fallback there that our generic `window` rule doesn't reach.
   Selected row keeps the accent gradient from the global list rule;
   hover gets a tinted overlay that reads on either palette. */
.lp-picker,
.lp-picker box,
.lp-picker notebook,
.lp-picker notebook stack,
.lp-picker scrolledwindow,
.lp-picker viewport,
.lp-picker list,
.lp-picker list > row {
    background-color: transparent;
    background-image: none;
}

.lp-picker {
    background-color: #0e1118;
}

.lp-picker list > row:hover {
    background-color: rgba(91, 125, 245, 0.10);
}

.lp-picker list > row:selected {
    background-image: linear-gradient(to right,
        rgba(91, 125, 245, 0.22),
        rgba(124, 58, 237, 0.22));
    color: #ffffff;
}

/* ----- LinuxPop-specific helper classes -----
   Any widget that adds these style classes via add_css_class()
   gets premium accents on top of the generic widget styling. */
.lp-accent {
    color: #5B7DF5;
}

.lp-card {
    background-color: #1c2231;
    border: 1px solid #3a4258;
    border-radius: 12px;
    padding: 12px;
}

.lp-hero {
    background-image: linear-gradient(135deg, #5B7DF5 0%, #7C3AED 55%, #EC4899 100%);
    color: #ffffff;
    border-radius: 12px;
    padding: 14px 18px;
}

.lp-muted {
    color: #b8c0d4;
}

.lp-title {
    font-size: 1.15em;
    font-weight: 700;
    letter-spacing: -0.005em;
}
"""


# Dark -> light hex remap. Keeps the accent gradient (cobalt blue ->
# royal violet -> magenta pink) and the destructive reds untouched -
# those read well on either background. Only swaps the surfaces, text,
# borders, and the muted button/sidebar tints.
_LIGHT_REMAP = {
    # core palette
    "#0e1118": "#f6f7fb",  # window bg
    "#1c2231": "#ffffff",  # surface (cards / list rows)
    "#262d3f": "#eef0f6",  # elevated (hover / selected)
    "#3a4258": "#d4d8e2",  # border
    "#f0f3fa": "#1c2231",  # primary text
    "#b8c0d4": "#5a6378",  # muted / dim labels
    # secondary tints (buttons, sidebar, inputs)
    "#181d2a": "#ffffff",  # entry / textview body
    "#1a1f2c": "#e8ecf3",  # button gradient bottom
    "#1f2433": "#f4f6fa",  # button gradient top
    "#262c3e": "#dde1ea",  # button hover top
    "#232a3c": "#eef0f6",  # header gradient top
    "#1a1f2e": "#f4f6fa",  # header gradient bottom
    "#2c3346": "#e0e4ee",  # subtle border / row bottom
    "#161a24": "#f0f2f7",  # sidebar bg
    "#141823": "#e0e4ee",  # notebook header
    "#14171f": "#ebeef4",  # check/radio bg
    "#2a3145": "#d4d8e2",  # switch bg
    "#353c52": "#c0c5d2",  # switch border
    "#4a5266": "#a8b0c0",  # disabled text
    "#5a6378": "#7a8090",  # placeholder text
    # Pango-markup greys that several dialogs hard-code for dim/muted
    # secondary text. Inverted into a darker grey for light mode so the
    # contrast ratio stays readable (the original tones were tuned
    # against a dark surface).
    "#8a92a8": "#5a6378",
    "#9aa3b8": "#5a6378",
    "#9ba3b8": "#3a4258",
    # White-tinted highlights used for the "inset top edge" lift on
    # cards and the header bar. Invisible on a light surface (white on
    # white) - swap to a subtle dark shadow so the dimension survives.
    "rgba(255, 255, 255, 0.06)": "rgba(0, 0, 0, 0.06)",
}


def _apply_remap(css: bytes, remap: dict[str, str]) -> bytes:
    """Substitute one hex palette into the CSS in a single pass.
    Sequential .replace() would let later replacements re-hit earlier
    outputs (e.g. swapping #1c2231 to #ffffff, then #5a6378 to #7a8090
    would corrupt the first output if it shared a substring). Build a
    regex and replace by full match instead."""
    import re
    if not remap:
        return css
    text = css.decode("ascii")
    # Order keys longest-first so #abcdef wins over #abcde when both
    # appear in the map. Wrap each hex in word-boundary so partial
    # hexes inside another hex never match.
    keys = sorted(remap.keys(), key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(k) for k in keys))
    out = pattern.sub(lambda m: remap[m.group(0)], text)
    return out.encode("ascii")


def _system_prefers_dark() -> bool:
    """Best-effort: does the user's system theme look dark? Checks the
    Gtk-prefer-dark flag first, falls back to substring-match on the
    GTK theme name (Mint-Y-Dark, Adwaita-dark, etc.). Defaults to True
    when we have nothing to go on - keeps the existing premium look."""
    try:
        s = Gtk.Settings.get_default()
        if s is None:
            return True
        if s.get_property("gtk-application-prefer-dark-theme"):
            return True
        name = s.get_property("gtk-theme-name") or ""
        if "dark" in name.lower():
            return True
        return False
    except Exception:
        return True


def _resolve_mode(mode: str) -> str:
    if mode == "system":
        return "dark" if _system_prefers_dark() else "light"
    if mode in ("dark", "light"):
        return mode
    return "dark"


_active_provider: Optional[Gtk.CssProvider] = None
_active_mode: Optional[str] = None


def install_premium_theme(mode: str = "dark") -> None:
    """Install (or swap) the CSS provider on the default screen.

    `mode` is "dark", "light", or "system". Calling again with the same
    effective mode is a no-op; calling with a different one removes the
    old provider and installs the new one - so settings-side toggle can
    just call this without restarting the daemon.
    """
    global _active_provider, _active_mode

    screen = Gdk.Screen.get_default()
    if screen is None:
        return

    effective = _resolve_mode(mode)
    if _active_mode == effective and _active_provider is not None:
        return

    # On KDE/Wayland the host GTK theme is light Breeze, so any widget our CSS
    # doesn't explicitly cover (search entries, list selections, expander-row
    # headers) renders light with near-invisible light text. Force a known
    # dark base theme there so everything unstyled defaults to dark; our
    # premium CSS still layers on top at APPLICATION priority. Left untouched
    # on X11, where the user's existing GTK theme already works.
    if effective == "dark":
        try:
            from platform_backend import get_backend
            if get_backend().name == "wayland_kde":
                s = Gtk.Settings.get_default()
                if s is not None:
                    s.set_property("gtk-application-prefer-dark-theme", True)
                    s.set_property("gtk-theme-name", "Adwaita")
                    # The app's button/action icons are GNOME/Adwaita symbolic
                    # names; Breeze names many of them differently, so they'd
                    # render as the red "image-missing" glyph. Force the
                    # Adwaita icon theme so they resolve. The few names Adwaita
                    # also lacks are shipped in icons/hdy-compat (below).
                    s.set_property("gtk-icon-theme-name", "Adwaita")
                # HdyExpanderRow asks for "hdy-expander-arrow-symbolic", which
                # Breeze (and most non-Adwaita themes) don't ship, so it shows
                # the red "image-missing" glyph. Ship our own copy and add it
                # to the icon search path so the arrow renders correctly.
                icon_dir = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "icons", "hdy-compat")
                if os.path.isdir(icon_dir):
                    Gtk.IconTheme.get_default().append_search_path(icon_dir)
        except Exception:
            pass

    css = _PREMIUM_CSS if effective == "dark" else _apply_remap(
        _PREMIUM_CSS, _LIGHT_REMAP)

    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(css)
    except Exception:
        import logging
        logging.getLogger("linuxpop").exception(
            "premium theme (%s) failed to load", effective)
        return

    if _active_provider is not None:
        try:
            Gtk.StyleContext.remove_provider_for_screen(
                screen, _active_provider)
        except Exception:
            pass

    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _active_provider = provider
    _active_mode = effective
