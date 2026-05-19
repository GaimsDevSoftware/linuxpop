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


def search_web(text: str) -> None:
    query = urllib.parse.quote_plus(_strip_invisible(text))
    subprocess.Popen(["xdg-open", f"https://duckduckgo.com/?q={query}"], start_new_session=True)


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


def _confirm_run_then_launch(cmd: str, binary: str, prefix: list[str], wrapped: str) -> bool:
    """GTK confirmation dialog. Returns False so GLib.idle_add doesn't re-fire."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    preview = cmd if len(cmd) <= 400 else cmd[:400] + "…"
    dlg = Gtk.MessageDialog(
        transient_for=None,
        flags=0,
        message_type=Gtk.MessageType.QUESTION,
        buttons=Gtk.ButtonsType.NONE,
        text="Run this command in a terminal?",
    )
    dlg.format_secondary_markup(
        f"<tt>{Gtk.glib_markup_escape_text(preview) if hasattr(Gtk, 'glib_markup_escape_text') else preview}</tt>"
    )
    dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
    run_btn = dlg.add_button("Run", Gtk.ResponseType.OK)
    run_btn.get_style_context().add_class("suggested-action")
    dlg.set_default_response(Gtk.ResponseType.CANCEL)
    dlg.set_icon_name("linuxpop")
    dlg.set_keep_above(True)

    response = dlg.run()
    dlg.destroy()
    if response == Gtk.ResponseType.OK:
        try:
            subprocess.Popen([*prefix, wrapped], start_new_session=True)
            print(f"[actions] running in terminal ({binary}): {cmd[:60]}")
        except OSError as exc:
            print(f"[actions] terminal launch failed: {exc}")
    return False


def run_in_terminal(text: str) -> None:
    cmd = _strip_invisible(text)
    found = _find_terminal()
    if not found:
        print("[actions] no terminal emulator found")
        return
    binary, prefix = found

    # Show "$ <cmd>" first so it looks like the command was pasted in,
    # then run it. Use shlex.quote to safely embed any chars in printf.
    echoed = shlex.quote(f"\033[1;32m$\033[0m {cmd}")
    if _terminal_keep_open():
        # Echo, run, then drop into an interactive shell. Close via exit/Ctrl-D/X.
        wrapped = f"printf '%s\\n' {echoed}; {cmd}; exec bash"
    else:
        # Echo, run, close immediately (output lost)
        wrapped = f"printf '%s\\n' {echoed}; {cmd}"

    if _should_confirm_terminal():
        # Marshal to the GTK main thread for the modal dialog. Setting
        # terminal_confirm_run=false in settings.json skips this prompt.
        from gi.repository import GLib
        GLib.idle_add(_confirm_run_then_launch, cmd, binary, prefix, wrapped)
        return

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
