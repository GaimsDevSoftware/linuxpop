"""Built-in actions used by the default plugins."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import urllib.parse
from typing import Optional

from classifier import _normalize as _strip_invisible


# Thread-local 'force copy only' flag. When set, replace_selection
# writes to the clipboard but skips the Ctrl+V step — the popup uses
# this to honour Shift-click on a transform action: result lands on the
# clipboard for manual paste elsewhere, never overwrites the original
# selection. Mirrors PopClip's Shift-modifier behaviour where Shift
# forces a copy instead of a paste.
_local = threading.local()


def force_copy_active() -> bool:
    return bool(getattr(_local, "force_copy", False))


class force_copy_mode:
    """Context manager: while active, replace_selection skips the paste
    step and leaves the result on the clipboard only. Thread-local so
    concurrent plugin executions don't interfere."""

    def __enter__(self) -> None:
        _local.force_copy = True

    def __exit__(self, *_exc) -> None:
        _local.force_copy = False


def copy_to_clipboard(text: str) -> None:
    """Copy text to the X11 clipboard via xclip."""
    if not shutil.which("xclip"):
        print("[actions] xclip not installed, cannot copy")
        return
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
            timeout=2.0,
        )
        print(f"[actions] copied {len(text)} chars to clipboard")
    except subprocess.CalledProcessError as exc:
        print(f"[actions] xclip failed: {exc}")


def replace_selection(new_text: str) -> None:
    """Put new_text on the clipboard AND paste it over the current
    selection — the in-place transform behaviour PopClip has on macOS.

    Sequence:
      1. xclip writes the new text to CLIPBOARD
      2. ~50 ms settle so the X selection-owner change propagates
      3. xdotool sends Ctrl+V, which the focused app interprets as
         'paste over the current selection'

    If the focused widget is read-only the Ctrl+V is silently
    discarded, but the result is still on the clipboard so the user
    can paste it elsewhere — same fallback as a manual Copy.
    """
    if not shutil.which("xclip"):
        print("[actions] xclip not installed, cannot replace selection")
        return
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=new_text.encode("utf-8"),
            check=False,
            timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        print("[actions] xclip write timed out — selection not replaced")
        return
    if force_copy_active():
        # Shift-modifier on the popup button: result goes to the
        # clipboard, but we deliberately skip the Ctrl+V so the
        # original selection stays put.
        print("[actions] force-copy mode — clipboard only, no paste")
        return
    if not shutil.which("xdotool"):
        # No xdotool: leave the result on the clipboard so the user can
        # paste manually with Ctrl+V.
        print("[actions] xdotool missing — text on clipboard only")
        return
    import time as _t
    _t.sleep(0.05)
    try:
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            check=False,
            timeout=2.0,
        )
    except subprocess.TimeoutExpired:
        print("[actions] xdotool paste timed out — text on clipboard only")


def open_url(text: str) -> None:
    url = _strip_invisible(text)
    # Naked URLs (no scheme) get https:// so xdg-open routes them to the browser
    if not url.lower().startswith(("http://", "https://", "ftp://", "file://", "mailto:")):
        url = "https://" + url
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
        print(f"[actions] opened URL: {url}")
    except FileNotFoundError:
        print("[actions] xdg-open not available")


# General-purpose web search engines. The value is a URL template — '{q}'
# gets replaced with the URL-encoded selection. Add to the dict to extend.
#
# Site-specific destinations (Wikipedia, YouTube, MDN, Stack Overflow, etc.)
# live as their own popup buttons via the recipe system — see
# plugins_repo/recipes/. Keep this dict scoped to "general search".
SEARCH_ENGINES: dict[str, tuple[str, str]] = {
    # key:            (display name,     URL template)
    "google":         ("Google",         "https://www.google.com/search?q={q}"),
    "duckduckgo":     ("DuckDuckGo",     "https://duckduckgo.com/?q={q}"),
    "bing":           ("Bing",           "https://www.bing.com/search?q={q}"),
    "brave":          ("Brave Search",   "https://search.brave.com/search?q={q}"),
    "startpage":      ("Startpage",      "https://www.startpage.com/do/search?q={q}"),
    "ecosia":         ("Ecosia",         "https://www.ecosia.org/search?q={q}"),
    "kagi":           ("Kagi",           "https://kagi.com/search?q={q}"),
    "qwant":          ("Qwant",          "https://www.qwant.com/?q={q}"),
    "yandex":         ("Yandex",         "https://yandex.com/search/?text={q}"),
}


def _search_template() -> str:
    """Return the URL template for the user's chosen search engine.
    Falls back to Google if the configured engine is unknown."""
    try:
        from settings import get_settings
        s = get_settings()
        engine = (s.get("search_engine") or "google").strip().lower()
        if engine == "custom":
            tmpl = (s.get("search_engine_custom_url") or "").strip()
            if "{q}" in tmpl:
                return tmpl
            # Empty/invalid custom URL — fall through to default.
        if engine in SEARCH_ENGINES:
            return SEARCH_ENGINES[engine][1]
    except Exception:
        pass
    return SEARCH_ENGINES["google"][1]


def search_web(text: str) -> None:
    query = urllib.parse.quote_plus(_strip_invisible(text))
    url = _search_template().replace("{q}", query)
    subprocess.Popen(["xdg-open", url], start_new_session=True)


def _find_terminal() -> Optional[tuple[str, list[str]]]:
    """Return (binary, argv-prefix) for the first installed terminal emulator."""
    candidates = [
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c"]),
        ("konsole", ["konsole", "-e", "bash", "-c"]),
        ("xfce4-terminal", ["xfce4-terminal", "-e"]),
        ("alacritty", ["alacritty", "-e", "bash", "-c"]),
        ("kitty", ["kitty", "bash", "-c"]),
        ("x-terminal-emulator", ["x-terminal-emulator", "-e", "bash", "-c"]),
        ("xterm", ["xterm", "-hold", "-e", "bash", "-c"]),  # xterm has -hold
    ]
    for binary, argv in candidates:
        if shutil.which(binary):
            return binary, argv
    return None


def _terminal_keep_open() -> bool:
    try:
        from settings import get_settings
        return bool(get_settings().get("terminal_keep_open", True))
    except Exception:
        return True


def _should_confirm_terminal() -> bool:
    try:
        from settings import get_settings
        return bool(get_settings().get("terminal_confirm_run", True))
    except Exception:
        return True


def _spawn_terminal(argv: list[str], log) -> None:
    """Launch a terminal emulator with hardening that makes the D-Bus
    flavours (gnome-terminal, konsole) actually open a window:

      * stdin=/dev/null -- the daemon's stdin is a socket, and inheriting
        that into the gnome-terminal CLI confuses its D-Bus handshake.
      * env=os.environ.copy() -- the posix_spawn fast path in Python 3.12
        with env=None has been observed to drop some env vars that the
        terminal's D-Bus client needs (DBUS_SESSION_BUS_ADDRESS, GIO*).
        Explicit copy forces the fork+exec path.
      * start_new_session=True -- terminal outlives LinuxPop if we exit.
    """
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            env=os.environ.copy(),
            start_new_session=True,
        )
    except OSError as exc:
        log.error("[terminal] launch failed: %s", exc)
        raise


def _wrap_for_terminal(cmd: str) -> str:
    """Wrap a user command so the terminal echoes it first (looks like the
    user pasted it), then runs it, and optionally drops into an interactive
    shell so output stays visible."""
    echoed = shlex.quote(f"\033[1;32m$\033[0m {cmd}")
    if _terminal_keep_open():
        return f"printf '%s\\n' {echoed}; {cmd}; exec bash"
    return f"printf '%s\\n' {echoed}; {cmd}"


def _confirm_run_then_launch(cmd: str, binary: str, prefix: list[str]) -> bool:
    """GTK confirmation dialog. Default state is read-only preview --
    Cancel / Edit / Run. Clicking Edit unlocks the command field for
    in-place tweaking. Returns False so GLib.idle_add doesn't re-fire."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gdk, Gtk

    dlg = Gtk.Dialog(title="Run in terminal", modal=True)
    dlg.set_default_size(580, 260)
    dlg.set_icon_name("linuxpop")
    dlg.set_keep_above(True)
    dlg.set_position(Gtk.WindowPosition.CENTER)

    content = dlg.get_content_area()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                  margin_top=14, margin_bottom=12,
                  margin_start=18, margin_end=18)

    head = Gtk.Label(xalign=0)
    head.set_markup("<span size='large' weight='bold'>Run this command?</span>")
    box.pack_start(head, False, False, 0)

    explain = Gtk.Label(xalign=0)
    explain.set_markup(
        "<span foreground='#b8c0d4' size='small'>"
        "Press <b>Run</b> to launch it as-is, <b>Edit</b> to tweak it first."
        "</span>")
    explain.set_line_wrap(True)
    box.pack_start(explain, False, False, 0)

    # Command preview/editor. Monospace TextView holds any character
    # (including '&', '<', '>', shell metacharacters) without the
    # Pango-markup ambiguity that the old MessageDialog had.
    #
    # Two visual states, toggled by the Edit button:
    #   .lp-cmd-preview -> read-only, flat on the dialog background,
    #                      looks like a label (the "old preview" look)
    #   .lp-cmd-edit    -> editable, picks up the input-field styling
    #                      (border + slightly lighter bg + caret)
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(96)
    # No shadow frame in the read-only state -- the IN shadow gives the
    # white-box look the user disliked. Re-enabled on entering edit mode.
    scroll.set_shadow_type(Gtk.ShadowType.NONE)
    text_view = Gtk.TextView()
    text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    try:
        text_view.set_monospace(True)  # GTK 3.16+
    except AttributeError:
        text_view.override_font(__import__("gi").repository.Pango.FontDescription("monospace 11"))
    text_view.set_editable(False)
    text_view.set_cursor_visible(False)
    text_view.get_style_context().add_class("lp-cmd-preview")
    text_buf = text_view.get_buffer()
    text_buf.set_text(cmd)
    scroll.add(text_view)
    box.pack_start(scroll, True, True, 0)

    # Button row: Cancel · Edit · Run
    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                     margin_top=4)
    btn_box.pack_start(Gtk.Label(), True, True, 0)

    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
    btn_box.pack_start(cancel, False, False, 0)

    edit_btn = Gtk.Button(label="Edit")
    edit_btn.set_tooltip_text("Unlock the command for editing")

    def _enter_edit_mode(*_):
        text_view.set_editable(True)
        text_view.set_cursor_visible(True)
        # Swap visual state: drop the flat preview look, pick up the
        # input-field look (border + slight bg + caret).
        ctx = text_view.get_style_context()
        ctx.remove_class("lp-cmd-preview")
        ctx.add_class("lp-cmd-edit")
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        text_view.grab_focus()
        # Select all so the user can just start typing to replace, or
        # press End/Arrow to position the caret to tweak.
        text_buf.select_range(text_buf.get_start_iter(),
                              text_buf.get_end_iter())
        # The button has done its job — disable so it visually confirms
        # "you're in edit mode now".
        edit_btn.set_sensitive(False)
        edit_btn.set_label("Editing…")
        explain.set_markup(
            "<span foreground='#b8c0d4' size='small'>"
            "<b>Editing</b> -- Ctrl+Enter to run, Esc to cancel."
            "</span>")
    edit_btn.connect("clicked", _enter_edit_mode)
    btn_box.pack_start(edit_btn, False, False, 0)

    run_btn = Gtk.Button(label="Run")
    run_btn.get_style_context().add_class("suggested-action")
    run_btn.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
    btn_box.pack_start(run_btn, False, False, 0)

    box.pack_start(btn_box, False, False, 0)

    # Keyboard shortcuts. Two separate handlers because GTK key events
    # are dispatched to the focused widget FIRST -- if GtkTextView's
    # default handler consumes Enter (to insert a newline), the dialog's
    # key-press-event never fires. So Ctrl+Enter has to be intercepted
    # ON the TextView itself, before the default handler runs.
    def _on_textview_key(_w, event):
        is_enter = event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if is_enter and ctrl:
            dlg.response(Gtk.ResponseType.OK)
            return True  # consume, don't insert a newline
        return False  # let TextView handle plain Enter etc. normally
    text_view.connect("key-press-event", _on_textview_key)

    def _on_dialog_key(_w, event):
        is_enter = event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if event.keyval == Gdk.KEY_Escape:
            dlg.response(Gtk.ResponseType.CANCEL)
            return True
        if is_enter and (ctrl or not text_view.get_editable()):
            dlg.response(Gtk.ResponseType.OK)
            return True
        return False
    dlg.connect("key-press-event", _on_dialog_key)

    content.add(box)
    dlg.show_all()
    # Focus the Run button by default so plain Enter confirms the as-is
    # command (the common path). Edit button is one Tab away.
    run_btn.grab_focus()

    response = dlg.run()
    import logging
    log = logging.getLogger("linuxpop")

    if response == Gtk.ResponseType.OK:
        start, end = text_buf.get_start_iter(), text_buf.get_end_iter()
        edited = text_buf.get_text(start, end, True).strip()
        if not edited:
            log.info("[terminal] OK pressed but command was empty -- skipping")
        else:
            wrapped = _wrap_for_terminal(edited)
            try:
                _spawn_terminal([*prefix, wrapped], log)
                log.info("[terminal] launched (%s): %s", binary, edited[:120])
            except OSError:
                pass  # already logged inside _spawn_terminal
    else:
        log.info("[terminal] cancelled (response=%s)", response)
    dlg.destroy()
    return False


def run_in_terminal(text: str) -> None:
    cmd = _strip_invisible(text)
    found = _find_terminal()
    if not found:
        print("[actions] no terminal emulator found")
        return
    binary, prefix = found

    if _should_confirm_terminal():
        # Marshal to the GTK main thread for the modal dialog. Setting
        # terminal_confirm_run=false in settings.json skips this prompt.
        # The dialog rebuilds the wrapped command after the user edits.
        from gi.repository import GLib
        GLib.idle_add(_confirm_run_then_launch, cmd, binary, prefix)
        return

    import logging
    log = logging.getLogger("linuxpop")
    wrapped = _wrap_for_terminal(cmd)
    try:
        _spawn_terminal([*prefix, wrapped], log)
        log.info("[terminal] launched (%s): %s", binary, cmd[:120])
    except OSError:
        pass  # already logged


def open_path(text: str) -> None:
    path = os.path.expanduser(_strip_invisible(text))
    try:
        subprocess.Popen(["xdg-open", path], start_new_session=True)
        print(f"[actions] opened path: {path}")
    except FileNotFoundError:
        print("[actions] xdg-open not available")


def compose_email(text: str) -> None:
    addr = _strip_invisible(text)
    subprocess.Popen(["xdg-open", f"mailto:{addr}"], start_new_session=True)
