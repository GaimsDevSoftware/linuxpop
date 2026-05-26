"""Premium dark theme for LinuxPop -- one CSS provider for every window.

Loaded once at app startup. Targets libhandy widget classes (.boxed-list,
HdyActionRow, HdyPreferencesWindow) plus standard GTK widgets so all
dialogs (Settings, Plugin Manager, Clipboard Picker, Recipe Wizard, Icon
Picker, About) inherit a single, coherent look.

Palette is tuned to match the LinuxPop app icon: a deep cobalt base with
a blue->violet->magenta accent gradient.
"""
from __future__ import annotations

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


_loaded = False


def install_premium_theme() -> None:
    """Install the premium CSS provider on the default screen. Idempotent --
    safe to call more than once (the second call is a no-op)."""
    global _loaded
    if _loaded:
        return

    screen = Gdk.Screen.get_default()
    if screen is None:
        return

    provider = Gtk.CssProvider()
    try:
        provider.load_from_data(_PREMIUM_CSS)
    except Exception:
        import logging
        logging.getLogger("linuxpop").exception("premium theme failed to load")
        return

    Gtk.StyleContext.add_provider_for_screen(
        screen,
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _loaded = True
