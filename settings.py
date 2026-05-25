"""Simple JSON-backed settings for LinuxPop.

Lives at ~/.config/linuxpop/settings.json. Missing file or missing keys
fall back to defaults. Unknown keys in the file are preserved on save so
the user can hand-edit and add comments-as-keys.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR = Path(os.path.expanduser("~/.config/linuxpop"))
CONFIG_PATH = CONFIG_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    # Show popup automatically when the X11 PRIMARY selection changes
    "show_on_selection": True,
    # Hotkey to summon the popup with the current PRIMARY selection at the cursor.
    # Format: "<modifiers>+<key>", e.g. "super+shift+y", "ctrl+alt+y", "super+space".
    # Set to null/empty to disable. Use the recorder in Innstillinger to capture
    # a combo by pressing it.
    "hotkey": "super+shift+y",
    # Master on/off for the clipboard plugin. When False, the background
    # selection-watcher thread is NOT started, the picker hotkey does
    # nothing, and the popup button is hidden. Use this if you'd rather
    # not have LinuxPop track your clipboard at all.
    "clipboard_history_enabled": True,
    # Hotkey to open the clipboard / snippets picker. Press, type to filter,
    # Enter to paste at the cursor. Ignored if clipboard_history_enabled
    # is False.
    "clipboard_hotkey": "super+v",
    # Which selection the hotkey reads: "primary" (highlight) or "clipboard"
    "hotkey_source": "primary",
    # Default ON. Poll the keyboard state every 50 ms instead of
    # registering an XGrabKey. CPU cost measured at <0.1 % per hotkey
    # in `top` (the calls complete in microseconds; theoretical worst-
    # case is 0.4 %). Bypasses WM-level grab conflicts — Cinnamon's
    # muffin defers Super-key dispatching to detect tap-vs-hold and
    # eats the first press of Shift+Super combos on its way through
    # the compositor event filter (see linuxmint/cinnamon #549). The
    # XGrabKey path is still wired up for power users who want pure
    # event-driven behaviour: flip this off in Settings.
    "hotkey_use_polling": True,
    # Milliseconds before the popup auto-hides if the mouse never enters
    # it. 6.5 s leaves enough time to read the buttons without overstaying
    # — 8 s felt sluggish in practice. Tunable via Settings → Timing.
    "auto_hide_initial_ms": 6500,
    # Milliseconds before hide after the mouse leaves the popup's safe
    # zone. 4 s is forgiving for re-entry; 1.5 s felt twitchy.
    "auto_hide_leave_ms": 4000,
    # Minimum text length to trigger the popup on selection. 2 chars
    # filters most accidental single-letter selections (drag-overshoot,
    # stray double-click) without blocking real one-syllable words —
    # those are typically 3+ chars anyway.
    "min_selection_length": 2,
    # How long to wait (ms) after the last selection change before showing
    # the popup. Suppresses popup churn while the user is still dragging
    # to extend a selection. ~250-400 ms feels natural; lower = snappier
    # but more likely to interrupt; higher = calmer but feels laggy.
    "selection_debounce_ms": 300,
    # If True, ignore selections that contain only whitespace
    "ignore_whitespace_only": True,
    # Substrings that, if any matches the active window's title or
    # WM_CLASS (case-insensitive), suppress the popup entirely. Useful
    # for password managers, banking sites, etc. One entry per pattern.
    # Examples: "KeePassXC", "DNB - Mozilla Firefox", "1Password".
    "blocklist_patterns": [],
    # Extra WM_CLASS substrings to treat as read-only contexts. The
    # built-in list already covers Evince, Okular, image viewers, file
    # managers, etc.; add app classes here if their windows are mostly
    # for reading (Cut/Paste/Backspace buttons get hidden in them).
    # Only consulted when AT-SPI didn't return a definite answer.
    "readonly_app_classes": [],
    # Hard cap on how many action buttons the popup will draw. Plugins
    # are ranked by priority + your custom plugin_order; anything past
    # the cap is silently dropped (NOT moved to an overflow menu —
    # raise this if you want everything visible). Stops the popup from
    # being a 25-icon bar when many plugins are installed.
    "max_popup_buttons": 10,
    # If True (default): after the command, drop into an interactive shell so
    #   output stays visible. Close with exit/Ctrl-D/X.
    # If False: terminal closes immediately after the command exits (output lost).
    "terminal_keep_open": True,
    # User-defined ordering of plugin buttons in the popup. List of plugin
    # names (the `name` field, e.g. "copy", "clipboard-history"). Plugins
    # listed here appear first in this order; unlisted plugins fall back
    # to their built-in priority. Edit via Plugin Manager → Order tab.
    "plugin_order": [],
    # Which chat-AI services the send_to_ai plugin should expose as buttons.
    # Recognised: "google_ai", "claude", "chatgpt", "gemini", "perplexity".
    # Default is Google AI Search alone — it works without login or any
    # subscription, opens in any browser, and auto-submits via URL. Add
    # the others in Settings if you have accounts and want one-click
    # routing to them.
    "ai_services": ["google_ai"],
    # Per-service strategy override. "url" prefills via ?q= (fast, but most
    # services auto-submit). "paste" opens the page and pastes via xdotool
    # (slower, lets you review before sending). Unset = use the service's
    # default (see plugins_repo/send_to_ai.py _SERVICES table).
    #   "ai_chatgpt_mode": "paste",
    #   "ai_perplexity_mode": "paste",
    # If True, "Run in terminal" pops a confirmation dialog showing the exact
    # command before launching. Recommended — protects against highlighting
    # a malicious-looking string and clicking the wrong button.
    "terminal_confirm_run": True,
    # Which search engine the "Search the web" popup button uses. Recognised
    # values: "google", "duckduckgo", "bing", "brave", "startpage", "ecosia",
    # "kagi", "qwant", "yandex", "wikipedia", "youtube", or "custom".
    # See actions.SEARCH_ENGINES for the full table.
    "search_engine": "google",
    # Used when search_engine == "custom". Must contain '{q}' which gets
    # replaced with the URL-encoded selection. Example for searx:
    #   "https://searx.example.com/search?q={q}"
    "search_engine_custom_url": "",
    # Support / donation URLs surfaced in the welcome dialog, the About
    # dialog, and the tray menu. Leave empty to hide that button entirely
    # (each is independent — show only the ones you've actually set up).
    # Conventional defaults are pre-filled assuming the GitHub org name
    # matches the upstream repo; update or blank out as needed.
    "support_paypal_url":   "https://paypal.me/linuxpop",
    # 'Skip short auto-popup selections' filter. Off by default to
    # match PopClip out of the box (no minimum-size knob there). When
    # on, the watcher silently drops selections shorter than
    # min_selection_length — useful if a flaky app keeps firing
    # X selection events for accidental clicks. The hotkey always
    # ignores this filter; see main.py _start_watcher.
    "min_selection_length_enabled": False,
    # If True, show the one-time welcome dialog on first run. Set to False
    # to skip it (mostly useful for screencasts / CI testing).
    "show_welcome_dialog": True,
}


class Settings:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._data: dict[str, Any] = {}
        self.reload()

    def reload(self) -> None:
        self._data = {}
        if self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data = loaded
                else:
                    print(f"[settings] {self.path} is not a JSON object, ignoring")
            except (OSError, json.JSONDecodeError) as exc:
                print(f"[settings] failed to read {self.path}: {exc}")

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        merged = {**DEFAULTS, **self._data}
        # Atomic write: write to tmp, fsync, then rename. Avoids losing
        # all settings if the process dies (OOM, power) mid-write.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, self.path)

    def ensure_written(self) -> None:
        """Write defaults to disk if the file doesn't exist yet."""
        if not self.path.is_file():
            self.save()
            print(f"[settings] wrote defaults to {self.path}")


# Module-level singleton for convenience
_singleton: Settings | None = None


def get_settings() -> Settings:
    global _singleton
    if _singleton is None:
        _singleton = Settings()
        _singleton.ensure_written()
    return _singleton
