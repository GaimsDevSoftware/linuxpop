#!/usr/bin/env python3
"""LinuxPop entry point: tray icon + selection watcher + hotkey + popup."""
from __future__ import annotations

import argparse
import fcntl
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

# XInitThreads is intentionally NOT called here. We tried it (since
# multiple threads touch X - watcher, hotkey, popup tick, clipboard
# watcher) but it interacts badly with the python-xlib + PyGObject mix
# in this process: the hotkey threads silently fail to grab keys when
# XInitThreads runs at import time. Python-xlib uses its own per-Display
# socket and Python-level Lock, so it doesn't actually need XInitThreads;
# Gdk/GTK's internal lock plus separate-Display-per-thread is the
# isolation we rely on. If "XIO: fatal IO error" crashes start showing
# up under load, revisit by giving each Xlib-using component its own
# connection (already mostly the case) rather than re-enabling
# XInitThreads.

import plugin_loader
import theme
from classifier import classify
from editable_detect import is_focus_editable, focused_selection_rect
from platform_backend import get_backend
from popup import PopupWindow
from settings import get_settings
from xdg_paths import CACHE_DIR, CONFIG_DIR

__version__ = "0.9.6"

LOG_FILE = CACHE_DIR / "linuxpop.log"
LOCK_FILE = CACHE_DIR / "linuxpop.lock"
FIRST_RUN_MARKER = CONFIG_DIR / ".first-run-done"

# Held for the lifetime of the process; the kernel releases the flock when
# the fd is closed (i.e. when we exit, even ungracefully). Storing it at
# module scope ensures it's not garbage-collected mid-run.
_lock_fd: int | None = None


def _acquire_single_instance_lock() -> None:
    """Refuse to start a second copy. Uses fcntl.flock - robust against
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
                ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-u", "normal", "-i", "linuxpop",
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


# Patterns for obvious secrets. Matched substrings are replaced with a
# placeholder before any preview is logged, so even a DEBUG-level preview
# (opt-in via debug_log_selection_content) never persists a live token.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),              # OpenAI / Anthropic-style
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),        # GitHub tokens
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"),       # Slack tokens
    re.compile(r"AKIA[0-9A-Z]{12,}"),                 # AWS access key id
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{8,}"), # Bearer auth headers
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\."              # JWT (header.payload...)
               r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # Long high-entropy run (hex/base64-ish) - catches generic API keys.
    re.compile(r"\b[A-Za-z0-9+/_-]{32,}\b"),
]


def _redact_secrets(text: str) -> str:
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _safe_preview(text: str, limit: int = 60) -> str:
    """Secret-redacted, newline-flattened snippet for DEBUG logging only."""
    return _redact_secrets(text[:limit]).replace("\n", "↵")


def _read_selection(source: str) -> str:
    return get_backend().read_selection(source)


def _pointer_position() -> tuple[int, int]:
    return get_backend().pointer_position()


def _active_window_blocked(patterns: list[str]) -> bool:
    """Return True if the focused window's title or class matches any of the
    user's block patterns (case-insensitive substring). The backend supplies
    the haystacks (X11: xdotool+xprop; Wayland: none yet)."""
    if not patterns:
        return False
    haystacks = get_backend().active_window_haystacks()
    if not haystacks:
        return False
    for p in patterns:
        p_lc = (p or "").strip().lower()
        if p_lc and any(p_lc in h for h in haystacks):
            return True
    return False


class App:
    def __init__(self, enable_tray: bool = True) -> None:
        self.settings = get_settings()
        plugin_loader.load_all()

        self.popup = PopupWindow(
            initial_grace_ms=int(self.settings.get("auto_hide_initial_ms")),
            leave_grace_ms=int(self.settings.get("auto_hide_leave_ms")),
            on_open_plugin_order=lambda: self.open_plugins(tab="order"),
        )

        self.min_len = int(self.settings.get("min_selection_length"))
        self.ignore_ws = bool(self.settings.get("ignore_whitespace_only"))

        self.watcher: SelectionWatcher | None = None
        self.hotkey = None
        self.clipboard_hotkey = None
        self.ocr_hotkey = None
        # Optional global double-click watcher. Created lazily when the
        # double_click_popup_enabled setting is on - PopClip-style
        # click-in-text-field popup.
        self.dblclick_watcher = None
        # Debounce id for plugin reloads triggered by settings saves -
        # prevents a load_all storm when many keys change in quick
        # succession (textarea editing, bulk toggles).
        self._reload_pending_id: int | None = None
        # Track what's actually grabbed right now, independent of the
        # settings singleton. The on_changed callback fires AFTER
        # settings_gui has already mutated the singleton, so comparing
        # "new setting" vs "current singleton" never sees a diff. These
        # mirrors are the source of truth for the live-rebind diff.
        self._bound_hotkey: str = ""
        self._bound_clipboard_hotkey: str = ""
        # Same mirror for the polling toggle - flipping it has to
        # rebuild both hotkey threads (poll vs grab is decided at
        # thread start, not per-event).
        self._bound_use_polling: bool = False
        self._watcher_active = False
        self.tray = None
        # Single-instance dialogs: created lazily, reused on subsequent opens
        self._settings_dialog = None
        self._plugin_dialog = None

        if bool(self.settings.get("show_on_selection")):
            self._start_watcher()

        self._start_hotkey()
        self._start_clipboard_hotkey()
        self._start_ocr_hotkey()
        self._maybe_start_dblclick_watcher()
        if enable_tray:
            self._start_tray()
        self._maybe_first_run()

    # ---- popup invocation ----------------------------------------------------

    def _show_for_text(self, text: str, x: int, y: int) -> None:
        # No min-length check here on purpose - that filter belongs to
        # the watcher (auto-popup on selection), not to this routine.
        # show_popup_now() routes empty text into the no-selection
        # paste menu, but non-empty text of *any* length should still
        # surface the selection popup when the user explicitly fires
        # the hotkey. PopClip works the same way: 'minimum size' is
        # an auto-popup filter, long-press always shows the popup.
        if not text:
            log.info("[show-for] suppressed -- empty text")
            return
        if self.ignore_ws and not text.strip():
            log.info("[show-for] suppressed -- whitespace-only")
            return
        # Skip when the active app/site is on the user's blocklist.
        # Checked here (just before the popup would appear) so plugins
        # and the watcher don't have to know about it.
        patterns = list(self.settings.get("blocklist_patterns") or [])
        if _active_window_blocked(patterns):
            log.info("[blocked] suppressed popup -- active window matches blocklist")
            return
        ctype = classify(text)
        # Never log the raw selection at INFO: it routinely contains
        # passwords, API keys and other private text, and linuxpop.log is
        # an unencrypted file on disk. Log only non-sensitive metadata.
        log.info("[%s] %d chars", ctype.value, len(text))
        # A secret-redacted preview is available at DEBUG, and only when
        # the user explicitly opts in via debug_log_selection_content.
        if log.isEnabledFor(logging.DEBUG) and \
                bool(self.settings.get("debug_log_selection_content")):
            log.debug("[%s] preview=%r", ctype.value, _safe_preview(text))
        # AT-SPI / WM_CLASS probe - drives which plugins are eligible.
        # Done here (not in popup.show_for) so the popup module stays
        # accessibility-agnostic and we can pipe extra user blocklist
        # classes in from settings without circular imports.
        extra_ro = tuple(self.settings.get("readonly_app_classes") or [])
        editable = is_focus_editable(extra_readonly_classes=extra_ro)
        # Try to anchor the popup to the selected-text rectangle (via
        # AT-SPI screen-coord extents) instead of the mouse pointer. Returns
        # None - and we fall back to (x, y) - whenever AT-SPI is off /
        # unavailable / the app exposes no selection geometry, so this is a
        # zero-regression enhancement. Gated behind popup_anchor_to_selection.
        rect = None
        if bool(self.settings.get("popup_anchor_to_selection")):
            try:
                rect = focused_selection_rect()
            except Exception as exc:  # noqa: BLE001
                log.info("[anchor] selection-rect lookup failed: %s", exc)
                rect = None
        self.popup.show_for(text, x, y, ctype, editable=editable, rect=rect)

    def show_popup_now(self) -> None:
        # Stamped so a "had to press the hotkey 3 times" report comes
        # with breadcrumbs in linuxpop.log: did the trigger reach us at
        # all? did xclip return empty? did the popup get suppressed by
        # the min-length / blocklist filters?
        import time as _t
        t0 = _t.monotonic()
        source = self.settings.get("hotkey_source") or "primary"
        text = _read_selection(source)
        log.info("[hotkey-fire] read %s: %d chars in %.0f ms",
                 source, len(text), (_t.monotonic() - t0) * 1000)
        try:
            x, y = _pointer_position()
        except Exception:
            x, y = 0, 0
        if not text:
            # PopClip-style: hotkey without a selection still shows a
            # popup, but populated with paste-oriented entry points
            # (clipboard history, snippets) instead of the usual
            # transforms-and-actions for the selected text. The popup is
            # the single portal; the dedicated clipboard hotkey stays
            # available as a power-user shortcut.
            log.info("[hotkey-fire] no selection - showing paste menu")
            self._show_no_selection_popup(x, y)
            return
        log.info("[hotkey-fire] showing popup at (%d, %d) for %d-char selection",
                 x, y, len(text))
        self._show_for_text(text, x, y)

    def _show_no_selection_popup(self, x: int, y: int) -> None:
        """Build a PopClip-style edit menu for the no-selection case.

        Shown when the popup hotkey fires while you're sitting in an
        editable field with nothing selected. Mirrors PopClip's "click
        in text field" popup: paste, select-all, backspace, plus the
        clipboard picker as the dedicated entry point for snippets and
        recent items.
        """
        if _active_window_blocked(list(self.settings.get("blocklist_patterns") or [])):
            log.info("[blocked] suppressed no-selection popup -- active window blocked")
            return

        def _send_keys(combo: str) -> "Callable[[], None]":
            def _fire() -> None:
                get_backend().send_key(combo)
            return _fire

        def _paste_then_enter() -> None:
            # Borrow the editing_actions submit-key heuristic so this
            # entry follows the same rule as the regular Paste & Enter
            # plugin: plain Return for terminals / search bars / chat
            # web; Ctrl+Return for Slack / Discord / Teams / Element /
            # Thunderbird where Return inserts a newline.
            try:
                import importlib
                ea = (importlib.import_module(
                          "linuxpop_user_editing_actions")
                      if "linuxpop_user_editing_actions" in sys.modules
                      else importlib.import_module("editing_actions"))
                submit_key = ea._submit_keystroke_for_focus()
            except Exception:
                submit_key = "Return"
            get_backend().paste()
            # Brief settle so Electron / React inputs commit the paste's
            # text state before the submit key is interpreted.
            time.sleep(0.08)
            get_backend().send_key(submit_key)

        items: list[tuple[str, str, "Callable[[], None]"]] = []
        items.append((
            "edit-paste-symbolic", "Paste", _send_keys("ctrl+v"),
        ))
        items.append((
            "mail-send-symbolic", "Paste & Enter", _paste_then_enter,
        ))
        if bool(self.settings.get("clipboard_history_enabled", True)):
            items.append((
                "linuxpop-clipboard-symbolic",
                "Paste from history",
                self._on_clipboard_hotkey,
            ))
        items.append((
            "edit-select-all-symbolic", "Select all", _send_keys("ctrl+a"),
        ))
        items.append((
            "edit-clear-symbolic", "Backspace", _send_keys("BackSpace"),
        ))
        self.popup.show_actions(items, x, y)

    def _maybe_start_dblclick_watcher(self) -> None:
        """Honour the double_click_popup_enabled setting. Idempotent.
        Called at startup and again from the settings callback."""
        enabled = bool(self.settings.get("double_click_popup_enabled", False))
        if enabled:
            if self.dblclick_watcher is None:
                self.dblclick_watcher = get_backend().make_double_click_watcher(
                    self._on_global_double_click)
            if self.dblclick_watcher is not None:
                self.dblclick_watcher.start()
        elif self.dblclick_watcher is not None:
            self.dblclick_watcher.stop()
            self.dblclick_watcher = None

    def _on_global_double_click(self, x: int, y: int) -> None:
        """PopClip-style: double-click inside an empty editable field
        pops the edit menu. The watcher has already compared PRIMARY
        before and after the second click - if a word got selected,
        the watcher dropped the call entirely, so by the time we're
        here we know it's a real "double-click in empty field" gesture.
        We only need to confirm the focused widget is editable."""
        try:
            if not is_focus_editable():
                return
        except Exception:
            return
        self._show_no_selection_popup(x, y)

    # ---- watcher -------------------------------------------------------------

    def _start_watcher(self) -> None:
        if self.watcher is not None:
            return

        def on_selection(text: str, x: int, y: int) -> None:
            # Watcher-only filter, opt-in via settings:
            # 'min_selection_length_enabled' gates whether we trim
            # very short selections at all. Default off (PopClip
            # convention - show the popup for any selection).
            # The hotkey path bypasses this entirely; see
            # _show_for_text's docstring.
            if not text:
                return
            if bool(self.settings.get("min_selection_length_enabled")) \
                    and len(text) < self.min_len:
                log.info("[watcher] skipping short selection (%d < %d)",
                         len(text), self.min_len)
                return
            GLib.idle_add(self._show_for_text, text, x, y)

        self.watcher = get_backend().make_selection_watcher(
            on_selection,
            int(self.settings.get("selection_debounce_ms")),
        )
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
        hotkey_str = (self.settings.get("hotkey") or "").strip()
        if not hotkey_str:
            log.info("hotkey disabled in settings")
            self._bound_hotkey = ""
            return
        use_polling = bool(self.settings.get("hotkey_use_polling", False))
        self.hotkey = get_backend().make_hotkey(hotkey_str, self.show_popup_now,
                                                use_polling=use_polling)
        self.hotkey.start()
        self._bound_hotkey = hotkey_str
        self._bound_use_polling = use_polling

    def _start_ocr_hotkey(self) -> None:
        hotkey_str = (self.settings.get("ocr_hotkey") or "").strip()
        if not hotkey_str:
            return
        try:
            from screen_ocr import is_supported
            ok, reason = is_supported()
            if not ok:
                log.info("ocr hotkey not bound: %s", reason)
                return
        except Exception:
            log.exception("ocr support probe failed")
            return
        use_polling = bool(self.settings.get("hotkey_use_polling", False))
        self.ocr_hotkey = get_backend().make_hotkey(hotkey_str, self._on_ocr_hotkey,
                                                    use_polling=use_polling)
        self.ocr_hotkey.start()
        log.info("[ocr] hotkey '%s' bound (polling=%s)",
                 hotkey_str, use_polling)

    def _on_ocr_hotkey(self) -> None:
        from screen_ocr import run_ocr_to_clipboard
        # Capture runs on a worker thread - the daemon's main loop must
        # stay responsive while the user is dragging the region.
        threading.Thread(
            target=run_ocr_to_clipboard,
            daemon=True, name="ocr-capture",
        ).start()

    def _start_clipboard_hotkey(self) -> None:
        if not bool(self.settings.get("clipboard_history_enabled", True)):
            log.info("clipboard plugin disabled - not binding clipboard hotkey")
            self._bound_clipboard_hotkey = ""
            return
        hotkey_str = (self.settings.get("clipboard_hotkey") or "").strip()
        if not hotkey_str:
            log.info("clipboard hotkey disabled in settings")
            self._bound_clipboard_hotkey = ""
            return
        use_polling = bool(self.settings.get("hotkey_use_polling", False))
        self.clipboard_hotkey = get_backend().make_hotkey(
            hotkey_str, self._on_clipboard_hotkey, use_polling=use_polling)
        self.clipboard_hotkey.start()
        self._bound_clipboard_hotkey = hotkey_str

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
                    ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-information", "LinuxPop",
                     "Install the Clipboard plugin to get the picker."],
                    check=False,
                )
            return
        target = mod._get_active_window()
        mod.open_picker(target)

    # ---- tray + dialogs ------------------------------------------------------

    def _poll_tray(self) -> bool:
        if self.tray is not None:
            self.tray.poll()
        return True  # keep timeout alive

    def _start_tray(self) -> None:
        from tray import Tray
        self.tray = Tray(
            on_toggle_watcher=self.toggle_watcher,
            get_watcher_active=self.watcher_active,
            on_show_popup_now=self.show_popup_now,
            on_open_settings=self.open_settings,
            on_open_plugins=self.open_plugins,
            on_open_about=self.open_about,
            on_open_support=self.open_support,
            on_quit=self.quit,
        )
        GLib.timeout_add(200, self._poll_tray)

    def open_support(self) -> None:
        try:
            from welcome import open_support_picker
            open_support_picker(self.settings)
        except Exception:
            log.exception("support picker crashed")

    def open_settings(self) -> None:
        log.info("opening settings dialog…")
        if self._settings_dialog is None:
            try:
                from settings_gui import SettingsDialog
            except Exception:
                log.exception("settings_gui import failed")
                return

            def on_changed():
                # Compare against what's currently *grabbed*, NOT the settings
                # singleton - settings_gui already mutated the singleton
                # before reaching us, so a singleton-vs-singleton diff
                # always reports "no change".
                self.settings = get_settings()
                new_hotkey = (self.settings.get("hotkey") or "").strip()
                new_clip = (self.settings.get("clipboard_hotkey") or "").strip()
                self.min_len = int(self.settings.get("min_selection_length"))
                self.ignore_ws = bool(self.settings.get("ignore_whitespace_only"))
                self.popup._initial_grace_ms = int(self.settings.get("auto_hide_initial_ms"))
                self.popup._leave_grace_ms = int(self.settings.get("auto_hide_leave_ms"))
                # Live-update the tray icon if the user changed its style.
                if self.tray is not None:
                    try:
                        self.tray.reload_icon()
                    except Exception:
                        pass
                if self.watcher is not None:
                    self.watcher.set_debounce_ms(
                        int(self.settings.get("selection_debounce_ms"))
                    )
                # Reload plugins so settings that gate plugin registration
                # (e.g. ai_services) take effect immediately. Debounced so
                # a burst of saves (textarea editing, multi-toggle bulk
                # changes) only triggers one reload after activity quiets.
                self._schedule_plugin_reload()
                new_polling = bool(self.settings.get("hotkey_use_polling", False))
                polling_changed = new_polling != self._bound_use_polling
                if polling_changed:
                    log.info("hotkey polling mode changed: %s → %s - "
                             "rebuilding both hotkey threads",
                             self._bound_use_polling, new_polling)
                if new_hotkey != self._bound_hotkey or polling_changed:
                    log.info("hotkey: %r → %r (polling=%s) - rebinding",
                             self._bound_hotkey, new_hotkey, new_polling)
                    if self.hotkey is not None:
                        self.hotkey.stop()
                        self.hotkey = None
                    self._bound_hotkey = ""
                    if new_hotkey:
                        self._start_hotkey()
                if new_clip != self._bound_clipboard_hotkey or polling_changed:
                    log.info("clipboard hotkey: %r → %r (polling=%s) - rebinding",
                             self._bound_clipboard_hotkey, new_clip, new_polling)
                    if self.clipboard_hotkey is not None:
                        self.clipboard_hotkey.stop()
                        self.clipboard_hotkey = None
                    self._bound_clipboard_hotkey = ""
                    if new_clip:
                        self._start_clipboard_hotkey()
                # Bind the OCR hotkey if it just became usable - e.g. the
                # user installed tesseract via the Settings Install button -
                # so it starts working without a daemon restart.
                if ((self.settings.get("ocr_hotkey") or "").strip()
                        and self.ocr_hotkey is None):
                    self._start_ocr_hotkey()
                # Live-apply the double-click watcher toggle.
                self._maybe_start_dblclick_watcher()
                log.info("settings reloaded")

            self._settings_dialog = SettingsDialog(on_changed=on_changed)
        try:
            # Subsequent calls just present() the existing window.
            self._settings_dialog.show()
        except Exception:
            log.exception("settings dialog crashed")

    def open_plugins(self, tab: str | None = None) -> None:
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
            self._plugin_dialog.show(tab=tab)
        except TypeError:
            # Older PluginManagerDialog signature without the tab arg.
            self._plugin_dialog.show()
        except Exception:
            log.exception("plugin manager dialog crashed")

    def open_about(self) -> None:
        about = Gtk.AboutDialog()
        about.set_program_name("LinuxPop")
        about.set_version(__version__)
        about.set_comments("A PopClip-inspired floating action popup for Linux "
                           f"({get_backend().name}).")
        about.set_license_type(Gtk.License.MIT_X11)
        about.set_logo_icon_name("linuxpop")
        about.set_icon_name("linuxpop")
        # Only set the website link if it was overridden via env var, so we
        # don't ship a 404 placeholder. Set LINUXPOP_PROJECT_URL when forking.
        project_url = os.environ.get("LINUXPOP_PROJECT_URL")
        if project_url:
            about.set_website(project_url)
            about.set_website_label("Project page")
        # Add a "Support" action button so the donation flow is one click
        # away from the canonical "About" surface.
        try:
            support_btn = about.add_button("Support LinuxPop…",
                                           Gtk.ResponseType.HELP)
            support_btn.connect("clicked",
                                lambda *_: self.open_support())
        except Exception:
            pass
        about.run()
        about.destroy()

    # ---- first-run experience ------------------------------------------------

    def _maybe_first_run(self) -> None:
        if FIRST_RUN_MARKER.is_file():
            return
        FIRST_RUN_MARKER.parent.mkdir(parents=True, exist_ok=True)
        FIRST_RUN_MARKER.touch()

        # Welcome dialog (one-time): explains usage + offers an optional
        # support link. Falls back to notify-send if GTK can't construct
        # the dialog for some reason.
        def _open_welcome():
            try:
                from onboarding import show_onboarding
                show_onboarding(
                    self.settings,
                    on_open_plugins=self.open_plugins,
                )
            except Exception:
                log.exception("onboarding failed; falling back to welcome dialog")
                try:
                    from welcome import show_welcome_dialog
                    show_welcome_dialog(
                        self.settings,
                        on_open_plugins=self.open_plugins,
                    )
                    return False
                except Exception:
                    log.exception("welcome dialog failed; falling back to notify")
                try:
                    subprocess.run(
                        ["notify-send", "--hint=byte:transient:1",  "-i", "linuxpop", "-t", "8000",
                         "LinuxPop is running",
                         "Select text anywhere to see actions. Tray icon "
                         "has settings & plugins."],
                        check=False,
                    )
                except FileNotFoundError:
                    pass
            return False

        GLib.timeout_add(800, _open_welcome)

    # ---- lifecycle -----------------------------------------------------------

    def _schedule_plugin_reload(self) -> None:
        if self._reload_pending_id is not None:
            GLib.source_remove(self._reload_pending_id)
        def _do_reload() -> bool:
            self._reload_pending_id = None
            plugin_loader.load_all()
            return False
        self._reload_pending_id = GLib.timeout_add(400, _do_reload)

    def quit(self) -> None:
        Gtk.main_quit()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="linuxpop",
        description="PopClip-inspired floating action popup for Linux (X11 + KDE Wayland).",
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
    if args.reset_first_run:
        # Wipe every first-run marker so the welcome flow AND the
        # plugin/recipe seeders fire again. Useful for testing the
        # curated default bundle from a clean state.
        for marker in (
            FIRST_RUN_MARKER,
            CONFIG_DIR / ".default-plugins-seeded",
            CONFIG_DIR / ".default-recipes-seeded",
        ):
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass

    _setup_logging(args.debug)
    get_backend().check_session()
    # Single-instance guard - refuses to start a second copy. Run BEFORE
    # any GTK init or hotkey grabs so the second copy exits before
    # interfering with the existing instance.
    _acquire_single_instance_lock()

    # NOTE: previously we set SIGCHLD = SIG_IGN here as a cheap way to
    # auto-reap subprocess zombies. Reverted because it has been observed
    # to interact badly with subprocess.run() on Python 3.12 in this
    # process layout (background watcher + many short-lived xclip/xdotool
    # calls + GTK main loop), producing intermittent UI freezes when the
    # clipboard picker is opened. Accepting some zombie PIDs over uptime
    # is the lesser evil; a proper periodic reaper can come later.

    theme.install_premium_theme(
        get_settings().get("theme", "dark") or "dark")

    app = App(enable_tray=not args.no_tray)

    # Start the Send-to-AI userscript bridge at launch so its install URL
    # (http://127.0.0.1:<port>/linuxpop.user.js) is always reachable and prompts
    # are served the moment a send fires. Best-effort; never fatal.
    try:
        import bridge_server
        _bp = bridge_server.start(
            int(get_settings().get("ai_userscript_bridge_port", 8766) or 8766))
        log.info("send-to-ai bridge: http://127.0.0.1:%d/linuxpop.user.js", _bp)
    except Exception as exc:  # noqa: BLE001
        log.info("send-to-ai bridge not started: %s", exc)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    log.info("LinuxPop %s running (tray=%s, hotkey=%s, watcher=%s)",
             __version__,
             "on" if app.tray else "off",
             app.settings.get("hotkey") or "disabled",
             "on" if app.watcher else "off")
    print("[linuxpop] running - Ctrl+C to quit. Logs at " + str(LOG_FILE))

    # Freeze the startup object graph so the GC never re-scans it. Cheap one-line
    # win for a long-running daemon - recommended by Python gc docs.
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
