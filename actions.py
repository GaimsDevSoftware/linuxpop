"""Built-in actions used by the default plugins."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import urllib.parse
from typing import Optional

from classifier import _normalize as _strip_invisible


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
        )
        print(f"[actions] copied {len(text)} chars to clipboard")
    except subprocess.CalledProcessError as exc:
        print(f"[actions] xclip failed: {exc}")


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


def _wrap_for_terminal(cmd: str) -> str:
    """Wrap a user command so the terminal echoes it first (looks like the
    user pasted it), then runs it, and optionally drops into an interactive
    shell so output stays visible."""
    echoed = shlex.quote(f"\033[1;32m$\033[0m {cmd}")
    if _terminal_keep_open():
        return f"printf '%s\\n' {echoed}; {cmd}; exec bash"
    return f"printf '%s\\n' {echoed}; {cmd}"


def _confirm_run_then_launch(cmd: str, binary: str, prefix: list[str]) -> bool:
    """GTK confirmation dialog with an editable command field. The user can
    tweak the command (or cancel) before it runs. Returns False so
    GLib.idle_add doesn't re-fire."""
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
        "You can edit the command before running. "
        "Ctrl+Enter to run, Esc to cancel."
        "</span>")
    explain.set_line_wrap(True)
    box.pack_start(explain, False, False, 0)

    # Editable command field. A multi-line TextView in monospace handles
    # any character (including '&', '<', '>', shell metacharacters) without
    # Pango-markup ambiguity. Pre-loaded with the selected text.
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_min_content_height(96)
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    text_view = Gtk.TextView()
    text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    try:
        text_view.set_monospace(True)  # GTK 3.16+
    except AttributeError:
        text_view.override_font(__import__("gi").repository.Pango.FontDescription("monospace 11"))
    text_buf = text_view.get_buffer()
    text_buf.set_text(cmd)
    scroll.add(text_view)
    box.pack_start(scroll, True, True, 0)

    # Button row
    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8,
                     margin_top=4)
    btn_box.pack_start(Gtk.Label(), True, True, 0)
    cancel = Gtk.Button(label="Cancel")
    cancel.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.CANCEL))
    btn_box.pack_start(cancel, False, False, 0)
    run_btn = Gtk.Button(label="Run")
    run_btn.get_style_context().add_class("suggested-action")
    run_btn.connect("clicked", lambda *_: dlg.response(Gtk.ResponseType.OK))
    btn_box.pack_start(run_btn, False, False, 0)
    box.pack_start(btn_box, False, False, 0)

    # Keyboard shortcuts: Ctrl+Enter runs, Esc cancels. Plain Enter inside
    # the TextView still inserts a newline (useful for multi-line commands).
    def _on_key(_w, event):
        is_enter = event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if is_enter and ctrl:
            dlg.response(Gtk.ResponseType.OK)
            return True
        if event.keyval == Gdk.KEY_Escape:
            dlg.response(Gtk.ResponseType.CANCEL)
            return True
        return False
    dlg.connect("key-press-event", _on_key)

    content.add(box)
    dlg.show_all()
    text_view.grab_focus()
    # Select all so the user can just start typing to replace.
    text_buf.select_range(text_buf.get_start_iter(), text_buf.get_end_iter())

    response = dlg.run()

    if response == Gtk.ResponseType.OK:
        start, end = text_buf.get_start_iter(), text_buf.get_end_iter()
        edited = text_buf.get_text(start, end, True).strip()
        if edited:
            wrapped = _wrap_for_terminal(edited)
            try:
                subprocess.Popen([*prefix, wrapped], start_new_session=True)
                print(f"[actions] running in terminal ({binary}): {edited[:60]}")
            except OSError as exc:
                print(f"[actions] terminal launch failed: {exc}")
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

    wrapped = _wrap_for_terminal(cmd)
    try:
        subprocess.Popen([*prefix, wrapped], start_new_session=True)
        print(f"[actions] running in terminal ({binary}): {cmd[:60]}")
    except OSError as exc:
        print(f"[actions] terminal launch failed: {exc}")


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
