#!/usr/bin/env python3
"""LinuxPop entry point: tray icon + selection watcher + hotkey + popup."""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import signal
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

import plugin_loader
import theme
from classifier import classify
from popup import PopupWindow
from settings import get_settings
from watcher import SelectionWatcher

__version__ = "0.1.0"

CACHE_DIR = Path(os.path.expanduser("~/.cache/linuxpop"))
LOG_FILE = CACHE_DIR / "linuxpop.log"
LOCK_FILE = CACHE_DIR / "linuxpop.lock"
FIRST_RUN_MARKER = Path(os.path.expanduser("~/.config/linuxpop/.first-run-done"))

# Held for the lifetime of the process; the kernel releases the flock when
# the fd is closed (i.e. when we exit, even ungracefully). Storing it at
# module scope ensures it's not garbage-collected mid-run.
_lock_fd: int | None = None


def _acquire_single_instance_lock() -> None:
    """Refuse to start a second copy. Uses fcntl.flock — robust against
    crashes (kernel releases the lock automatically when the process dies,
    no stale-lockfile cleanup needed)."""
    global _lock_fd
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        os.close(fd)
        # Read the existing PID for the user-facing message
        try:
            with open(LOCK_FILE, "r", encoding="utf-8") as f:
                existing_pid = f.read().strip() or "unknown"
        except OSError:
            existing_pid = "unknown"
        message = (
            f"LinuxPop is already running (PID {existing_pid}). "
            "Open the tray icon to use it. To force-restart, kill the "
            "existing process first: pkill -f 'python3.*linuxpop/main.py'"
        )
        print(f"[linuxpop] {message}", file=sys.stderr)
        try:
            subprocess.run(
                ["notify-send", "-u", "normal", "-i", "linuxpop",
                 "LinuxPop is already running", message],
                check=False,
            )
        except FileNotFoundError:
            pass
        sys.exit(0)
    # Write our PID inside the (now-locked) file for diagnostics
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    _lock_fd = fd

log = logging.getLogger("linuxpop")


def _setup_logging(debug: bool) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    level = logging.DEBUG if debug else logging.INFO

    file_handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=3)
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    stream_handler.setLevel(level)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(stream_handler)


def _check_x11_or_exit() -> None:
    """LinuxPop relies on X11 APIs (xclip selection, Xlib pointer, X11 grabs)."""
    if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        msg = (
            "LinuxPop requires an X11 session — you appear to be running Wayland.\n"
            "Log in to an 'Xorg' session from your display manager, or run under "
            "XWayland with DISPLAY set."
        )
        print(msg, file=sys.stderr)
        try:
            subprocess.run(
                ["notify-send", "-u", "critical",
                 "LinuxPop cannot start", msg.replace("\n", " ")],
                check=False,
            )
        except FileNotFoundError:
            pass
        sys.exit(2)
    if not os.environ.get("DISPLAY"):
        print("DISPLAY is not set — LinuxPop needs an X11 display.", file=sys.stderr)
        sys.exit(2)


def _read_selection(source: str) -> str:
    sel = "primary" if source.lower() == "primary" else "clipboard"
    try:
        out = subprocess.run(
            ["xclip", "-selection", sel, "-o"],
            capture_output=True,
            timeout=0.5,
        )
        return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _pointer_position() -> tuple[int, int]:
    from Xlib import display
    dpy = display.Display()
    try:
        data = dpy.screen().root.query_pointer()
        return data.root_x, data.root_y
    finally:
        dpy.close()


class App:
    def __init__(self, enable_tray: bool = True) -> None:
        self.settings = get_settings()
        plugin_loader.load_all()

        self.popup = PopupWindow(
            initial_grace_ms=int(self.settings.get("auto_hide_initial_ms")),
            leave_grace_ms=int(self.settings.get("auto_hide_leave_ms")),
        )

        self.min_len = int(self.settings.get("min_selection_length"))
        self.ignore_ws = bool(self.settings.get("ignore_whitespace_only"))

        self.watcher: SelectionWatcher | None = None
        self.hotkey = None
        self.clipboard_hotkey = None
        self._watcher_active = False
        self.tray = None
        # Single-instance dialogs: created lazily, reused on subsequent opens
        self._settings_dialog = None
        self._plugin_dialog = None

        if bool(self.settings.get("show_on_selection")):
            self._start_watcher()

        self._start_hotkey()
        self._start_clipboard_hotkey()
        if enable_tray:
            self._start_tray()
        self._maybe_first_run()

    # ---- popup invocation ----------------------------------------------------

    def _show_for_text(self, text: str, x: int, y: int) -> None:
        if not text or len(text) < self.min_len:
            return
        if self.ignore_ws and not text.strip():
            return
        ctype = classify(text)
        preview = text[:60].replace("\n", "↵")
        log.info("[%s] %r", ctype.value, preview)
        self.popup.show_for(text, x, y, ctype)

    def show_popup_now(self) -> None:
        text = _read_selection(self.settings.get("hotkey_source") or "primary")
        if not text:
            subprocess.run(
                ["notify-send", "-i", "dialog-information",
                 "LinuxPop", "Nothing selected"],
                check=False,
            )
            return
        try:
            x, y = _pointer_position()
        except Exception:
            x, y = 0, 0
        self._show_for_text(text, x, y)

    # ---- watcher -------------------------------------------------------------

    def _start_watcher(self) -> None:
        if self.watcher is not None:
            return

        def on_selection(text: str, x: int, y: int) -> None:
            GLib.idle_add(self._show_for_text, text, x, y)

        self.watcher = SelectionWatcher(on_selection)
        self.watcher.start()
        self._watcher_active = True
        log.info("selection watcher started")

    def _stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        self._watcher_active = False
        log.info("selection watcher stopped")

    def toggle_watcher(self, active: bool) -> None:
        if active and self.watcher is None:
            self._start_watcher()
        elif not active and self.watcher is not None:
            self._stop_watcher()
        self.settings.set("show_on_selection", bool(active))
        self.settings.save()

    def watcher_active(self) -> bool:
        return self._watcher_active

    # ---- hotkey --------------------------------------------------------------

    def _start_hotkey(self) -> None:
        hotkey_str = self.settings.get("hotkey")
        if not hotkey_str:
            log.info("hotkey disabled in settings")
            return
        from hotkey import Hotkey
        self.hotkey = Hotkey(hotkey_str, self.show_popup_now)
        self.hotkey.start()

    def _start_clipboard_hotkey(self) -> None:
        hotkey_str = self.settings.get("clipboard_hotkey")
        if not hotkey_str:
            log.info("clipboard hotkey disabled in settings")
            return
        from hotkey import Hotkey
        self.clipboard_hotkey = Hotkey(hotkey_str, self._on_clipboard_hotkey)
        self.clipboard_hotkey.start()

    def _on_clipboard_hotkey(self) -> None:
        """Capture the currently focused window BEFORE the picker steals
        focus, then open the picker. Paste-on-select restores focus to
        that window so Ctrl+V lands there.

        plugin_loader gives every user plugin a sys.modules name of the
        form 'linuxpop_user_<stem>', so we look up the picker that way.
        """
        mod = sys.modules.get("linuxpop_user_clipboard_history")
        if mod is None or not hasattr(mod, "open_picker"):
            text = _read_selection("clipboard")
            if text:
                try:
                    x, y = _pointer_position()
                except Exception:
                    x, y = 0, 0
                self._show_for_text(text, x, y)
            else:
                subprocess.run(
                    ["notify-send", "-i", "dialog-information", "LinuxPop",
                     "Install the Clipboard plugin to get the picker."],
                    check=False,
                )
            return
        target = mod._get_active_window()
        mod.open_picker(target)

    # ---- tray + dialogs ------------------------------------------------------

    def _start_tray(self) -> None:
        from tray import Tray
        self.tray = Tray(
            on_toggle_watcher=self.toggle_watcher,
            get_watcher_active=self.watcher_active,
            on_show_popup_now=self.show_popup_now,
            on_open_settings=self.open_settings,
            on_open_plugins=self.open_plugins,
            on_open_about=self.open_about,
            on_quit=self.quit,
        )

    def open_settings(self) -> None:
        log.info("opening settings dialog…")
        if self._settings_dialog is None:
            try:
                from settings_gui import SettingsDialog
            except Exception:
                log.exception("settings_gui import failed")
                return

            def on_changed():
                old_hotkey = (self.settings.get("hotkey") or "").strip()
                old_clip = (self.settings.get("clipboard_hotkey") or "").strip()
                self.settings = get_settings()
                new_hotkey = (self.settings.get("hotkey") or "").strip()
                new_clip = (self.settings.get("clipboard_hotkey") or "").strip()
                self.min_len = int(self.settings.get("min_selection_length"))
                self.ignore_ws = bool(self.settings.get("ignore_whitespace_only"))
                self.popup._initial_grace_ms = int(self.settings.get("auto_hide_initial_ms"))
                self.popup._leave_grace_ms = int(self.settings.get("auto_hide_leave_ms"))
                # Reload plugins so settings that gate plugin registration
                # (e.g. ai_services) take effect immediately.
                plugin_loader.load_all()
                # Rebind hotkeys live if they changed.
                if new_hotkey != old_hotkey:
                    log.info("hotkey changed: %r → %r — rebinding", old_hotkey, new_hotkey)
                    if self.hotkey is not None:
                        self.hotkey.stop()
                        self.hotkey = None
                    if new_hotkey:
                        self._start_hotkey()
                if new_clip != old_clip:
                    log.info("clipboard hotkey changed: %r → %r — rebinding",
                             old_clip, new_clip)
                    if self.clipboard_hotkey is not None:
                        self.clipboard_hotkey.stop()
                        self.clipboard_hotkey = None
                    if new_clip:
                        self._start_clipboard_hotkey()
                log.info("settings reloaded")

            self._settings_dialog = SettingsDialog(on_changed=on_changed)
        try:
            # Subsequent calls just present() the existing window.
            self._settings_dialog.show()
        except Exception:
            log.exception("settings dialog crashed")

    def open_plugins(self) -> None:
        log.info("opening plugin manager…")
        if self._plugin_dialog is None:
            try:
                from plugin_manager import PluginManagerDialog
            except Exception:
                log.exception("plugin_manager import failed")
                return

            def on_changed():
                plugin_loader.load_all()
                log.info("plugins reloaded")

            self._plugin_dialog = PluginManagerDialog(on_changed=on_changed)
        try:
            self._plugin_dialog.show()
        except Exception:
            log.exception("plugin manager dialog crashed")

    def open_about(self) -> None:
        about = Gtk.AboutDialog()
        about.set_program_name("LinuxPop")
        about.set_version(__version__)
        about.set_comments("A PopClip-inspired floating action popup for Linux (X11).")
        about.set_license_type(Gtk.License.MIT_X11)
        about.set_logo_icon_name("linuxpop")
        about.set_icon_name("linuxpop")
        # Only set the website link if it was overridden via env var, so we
        # don't ship a 404 placeholder. Set LINUXPOP_PROJECT_URL when forking.
        project_url = os.environ.get("LINUXPOP_PROJECT_URL")
        if project_url:
            about.set_website(project_url)
            about.set_website_label("Project page")
        about.run()
        about.destroy()

    # ---- first-run experience ------------------------------------------------

    def _maybe_first_run(self) -> None:
        if FIRST_RUN_MARKER.is_file():
            return
        FIRST_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        FIRST_RUN_MARKER.touch()

        # Welcome notification + offer to open plugin manager
        try:
            subprocess.run(
                ["notify-send", "-i", "linuxpop", "-t", "8000",
                 "LinuxPop is running",
                 "Select text anywhere to see actions. Look for the tray icon "
                 "for settings & plugins."],
                check=False,
            )
        except FileNotFoundError:
            pass
        # Open plugin manager after a short delay so user sees what's installable
        GLib.timeout_add(1500, lambda: (self.open_plugins(), False)[1])

    # ---- lifecycle -----------------------------------------------------------

    def quit(self) -> None:
        Gtk.main_quit()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="linuxpop",
        description="PopClip-inspired floating action popup for Linux (X11).",
    )
    p.add_argument("--version", action="version", version=f"LinuxPop {__version__}")
    p.add_argument("--no-tray", action="store_true",
                   help="Run without the tray icon (selection + hotkey only)")
    p.add_argument("--debug", action="store_true", help="Verbose logging")
    p.add_argument("--reset-first-run", action="store_true",
                   help="Force the welcome flow on next launch")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    if args.reset_first_run and FIRST_RUN_MARKER.is_file():
        FIRST_RUN_MARKER.unlink()

    _setup_logging(args.debug)
    _check_x11_or_exit()
    # Single-instance guard — refuses to start a second copy. Run BEFORE
    # any GTK init or hotkey grabs so the second copy exits before
    # interfering with the existing instance.
    _acquire_single_instance_lock()

    theme.install_premium_theme()

    app = App(enable_tray=not args.no_tray)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    log.info("LinuxPop %s running (tray=%s, hotkey=%s, watcher=%s)",
             __version__,
             "on" if app.tray else "off",
             app.settings.get("hotkey") or "disabled",
             "on" if app.watcher else "off")
    print("[linuxpop] running — Ctrl+C to quit. Logs at " + str(LOG_FILE))

    # Freeze the startup object graph so the GC never re-scans it. Cheap one-line
    # win for a long-running daemon — recommended by Python gc docs.
    def _freeze_gc_after_startup():
        import gc
        gc.collect()
        gc.freeze()
        return False
    GLib.idle_add(_freeze_gc_after_startup)

    Gtk.main()
    if app.watcher:
        app.watcher.stop()
    if app.hotkey:
        app.hotkey.stop()
    if app.clipboard_hotkey:
        app.clipboard_hotkey.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
