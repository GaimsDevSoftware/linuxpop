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

from xdg_paths import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    # Show popup automatically when the X11 PRIMARY selection changes
    # UI theme: "dark", "light", or "system" (auto-detect from the
    # GTK theme name + prefer-dark-theme flag). Dark by default - the
    # premium palette was designed against the cobalt + violet scheme.
    "theme": "dark",
    # Popup button (action chip) size in pixels - the min-width / min-
    # height of each button in the floating selection popup. Icon
    # scales proportionally. Clamped to [14, 48] by popup.py.
    "popup_button_size": 22,
    "show_on_selection": True,
    # PopClip-style: modifier+double-click anywhere pops the edit menu
    # (Paste / Select all / Backspace) at the cursor. The modifier
    # is required so the gesture doesn't collide with the app's own
    # double-click-to-select-a-word behaviour.
    # Off by default because turning it on means LinuxPop watches all
    # mouse clicks globally via the X11 RECORD extension. Nothing is
    # logged or transmitted - we only look for the chosen chord.
    "double_click_popup_enabled": False,
    # Which modifier key has to be held for the double-click chord.
    # One of "ctrl", "shift", "alt", "super". Read fresh each click
    # so a setting change takes effect without restarting the daemon.
    "double_click_modifier": "ctrl",
    # Modifier the user holds while making a selection to force the
    # popup to show EVERY installed plugin, bypassing the classifier's
    # content-type filter. Useful when the classifier got it wrong and
    # the action you actually want isn't in the filtered set (e.g. the
    # selection was misclassified as 'command' so Send-to-AI buttons
    # disappeared). One of "alt", "ctrl", "shift", "super", or empty
    # to disable.
    #
    # Off by default because every option conflicts with something:
    #   alt   - Cinnamon/GNOME mouse-button-modifier (Alt+drag moves
    #           windows on default installs)
    #   super - Same on some setups; often the WM's primary modifier
    #   ctrl  - Text editors / browsers use Ctrl+drag for word-by-word
    #           selection or multi-cursor
    #   shift - Extends an existing selection in every text widget
    # The user picks the lesser evil in Settings > Activation.
    "popup_force_all_modifier": "",
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
    # Default avoids Meta+V, which KDE Klipper / clipboard managers grab.
    "clipboard_hotkey": "super+shift+v",
    # Hotkey to capture a screen region and OCR it. Requires `maim` and
    # `tesseract-ocr` on PATH. Empty string disables. Useful for
    # reaching text inside PDF viewers, video frames, OS chrome, and
    # custom editors that don't propagate to X11 PRIMARY selection.
    "ocr_hotkey": "shift+super+o",
    # Snippet triggers - when ON, typing a snippet's shortcode followed
    # by space/tab/enter/punctuation auto-expands it in place. Requires
    # global keystroke monitoring via the X11 RECORD extension. Off by
    # default because of the privacy implication; keystrokes are matched
    # against your snippet triggers locally and never logged or sent.
    "snippet_triggers_enabled": False,
    # Per-app/site blocklist for trigger expansion only. Case-insensitive
    # substring match against the focused window's title AND WM_CLASS.
    # Use it to silence expansion in password managers, terminals, your
    # bank's website, etc. without disabling triggers globally.
    "trigger_blocklist_patterns": [],
    # Shell extension in snippets - when ON, {shell:CMD} tokens execute
    # bash and inject stdout. Off by default because importing a snippet
    # from elsewhere with a hostile {shell:...} would run code on your
    # machine; treat this like enabling macros in a document. 5 s timeout.
    "snippet_shell_enabled": False,
    # After paste-mode AI services (Claude, Gemini, ChatGPT with paste
    # fallback) drop the prompt in, also send Return so the chat sends
    # immediately. Off by default - lets you tweak the prompt before
    # hitting Enter. Has no effect on URL-mode services that already
    # auto-submit (Google AI, Perplexity, ChatGPT URL mode).
    "ai_paste_auto_submit": False,
    # How long _send_via_paste waits for the chat website to open and
    # focus. The browser-tab dance has phases: window-spawn, page-load,
    # editor-mount. We poll for each phase with these timeouts.
    "ai_window_timeout_seconds": 10.0,
    "ai_focus_timeout_seconds": 3.0,
    "ai_focus_stability_seconds": 0.25,
    "ai_paste_settle_seconds": 0.2,
    # Clipboard history depth. 25 is enough that you can scroll back
    # to anything you copied within the last day of work; bigger
    # values just make the picker harder to search.
    "clipboard_history_size": 25,
    # Capture image content too (not just text). PNG copies from
    # screenshot tools or browser drag-out land here as well.
    "clipboard_history_images": True,
    # Fallback poll interval when the XFixes selection-event path
    # isn't available. Most modern X servers do support XFixes so
    # this rarely fires.
    "clipboard_poll_interval": 0.5,
    # How the Send-to-AI buttons deliver the selection to the chat AI.
    # "userscript" : DEFAULT. Open the chat website in your browser; a
    #                Tampermonkey / Violentmonkey userscript talking to
    #                a local HTTP bridge (127.0.0.1:ai_userscript_bridge_
    #                port) fills the editor via document.execCommand(
    #                "insertText"). Reliable on Claude / Gemini / ChatGPT
    #                where paste-via-xdotool fights React contentEditable.
    #                Auto-falls-back to plain "browser" mode per-service
    #                when the userscript isn't installed yet, so the
    #                buttons still do something useful on first launch.
    # "browser"    : open the chat website with the prompt prefilled in
    #                the URL where supported (ChatGPT / Perplexity /
    #                Google AI Search), or paste-via-xdotool otherwise
    #                (Claude / Gemini - fragile against Electron / React).
    #                No setup needed but unreliable on the paste path.
    # "api"        : send via REST with your own API key. Most reliable
    #                but pay-as-you-go pricing. Requires the key set
    #                below; falls back to browser mode without it.
    # CLI mode was dropped 2026-05-29: it routed to vendor coding agents
    # (Claude Code, Codex, Antigravity) rather than the chat assistants
    # users expected from "Ask Claude". Anthropic banned OAuth-token
    # reuse for subscription chat in Jan 2026, closing the only viable
    # workaround.
    "ai_send_method": "userscript",
    # Per-service API keys for "api" send method. Stored as plain
    # text in settings.json - the user is told this in the GUI; the
    # alternative would be a system-keyring dependency we don't want
    # to introduce just for this.
    "ai_anthropic_api_key": "",
    "ai_openai_api_key": "",
    # Local HTTP bridge port for the userscript mode. The daemon binds
    # 127.0.0.1:<port> only; the userscript fetches the queued prompt
    # by UUID and inserts it into the editor. Default 8766 because the
    # historic 8765 collides with a uvicorn install on Robert's box and
    # likely with other dev tooling for users too. If the port is taken,
    # the bridge tries the next 10 ports and saves whichever it bound.
    "ai_userscript_bridge_port": 8766,
    # Shared snippet variables. Reusable values that snippets can pull
    # in via {var:NAME}. Define once (your email, signature, phone,
    # company name) and reference everywhere - change it here, every
    # snippet picks up the new value next paste. Stored as a dict of
    # {name: value} string pairs.
    "snippet_variables": {},
    # Which selection the hotkey reads: "primary" (highlight) or "clipboard"
    "hotkey_source": "primary",
    # Default ON. Poll the keyboard state every 50 ms instead of
    # registering an XGrabKey. CPU cost measured at <0.1 % per hotkey
    # in `top` (the calls complete in microseconds; theoretical worst-
    # case is 0.4 %). Bypasses WM-level grab conflicts - Cinnamon's
    # muffin defers Super-key dispatching to detect tap-vs-hold and
    # eats the first press of Shift+Super combos on its way through
    # the compositor event filter (see linuxmint/cinnamon #549). The
    # XGrabKey path is still wired up for power users who want pure
    # event-driven behaviour: flip this off in Settings.
    "hotkey_use_polling": True,
    # Target language for the Translate plugin (ISO code, e.g. "en", "no",
    # "de"). Changeable from the dropdown in the translation bubble itself.
    "translate_target_lang": "en",
    # Milliseconds before the popup auto-hides if the mouse never enters
    # it. 6.5 s leaves enough time to read the buttons without overstaying
    # - 8 s felt sluggish in practice. Tunable via Settings → Timing.
    "auto_hide_initial_ms": 6500,
    # Milliseconds before hide after the mouse leaves the popup's safe
    # zone. 4 s is forgiving for re-entry; 1.5 s felt twitchy.
    "auto_hide_leave_ms": 4000,
    # Minimum text length to trigger the popup on selection. 2 chars
    # filters most accidental single-letter selections (drag-overshoot,
    # stray double-click) without blocking real one-syllable words -
    # those are typically 3+ chars anyway.
    "min_selection_length": 2,
    # How long to wait (ms) after the last selection change before showing
    # the popup. Suppresses popup churn while the user is still dragging
    # to extend a selection. ~250-400 ms feels natural; lower = snappier
    # but more likely to interrupt; higher = calmer but feels laggy.
    # How long to wait after the last selection event before showing
    # the popup. The watcher fires an XFixes event each time the user
    # extends the highlight, so we need *some* debounce to let a
    # drag-to-extend gesture settle - but the cost of being too high
    # is that the popup feels laggy after a fast highlight + release.
    # 150 ms is in the sweet spot: humans rarely re-extend within
    # 150 ms of the last move, and 150 ms is below the "this feels
    # delayed" threshold.
    "selection_debounce_ms": 150,
    # If True, ignore selections that contain only whitespace
    "ignore_whitespace_only": True,
    # If True, log a short, secret-redacted preview of selection text at
    # DEBUG level (needs --debug to surface). Default False: the log
    # records only content type + length, never the raw selection, so
    # passwords/tokens/private text are not written to linuxpop.log.
    "debug_log_selection_content": False,
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
    # the cap is silently dropped (NOT moved to an overflow menu -
    # raise this if you want everything visible). Stops the popup from
    # being a 25-icon bar when many plugins are installed.
    # Cap before the popup either wraps to two rows or appends a "+N"
    # overflow chip. Default sized to fit the typical engaged-user
    # plugin set (12-20) across two rows without truncation. Bump it
    # if you want every action visible regardless of how many you
    # enable; drop it for a tighter single-row look.
    "max_popup_buttons": 24,
    # What the popup does when more actions match than fit on one line:
    #   "wrap"   -> spill onto a second row (classic two-line look),
    #   "expand" -> keep one tidy line, with a chevron that reveals the rest
    #               on click (recommended: compact, nothing dropped),
    #   "cap"    -> one row only; the remainder sit behind a "+N" chip.
    # All modes are still bounded by max_popup_buttons above.
    "popup_overflow_mode": "expand",
    # Collapse plugins that share a category (Formatting, Markdown, ...) behind
    # a single chip in the popup; clicking the chip expands its members onto the
    # second row. Declutters the bar when families like the markdown actions are
    # installed. Turn off to show every action inline.
    "popup_group_categories": True,
    # A category only collapses into a chip once it has at least this many
    # members in the popup; smaller groups stay inline (no point hiding one
    # button behind a chip).
    "popup_category_min": 2,
    # Icon style for branded/utility plugins:
    #   "color" -> vibrant gradient tiles (default)
    #   "glyph" -> uniform mono glyphs that match the plain text-edit icons
    "icon_style": "color",
    # System-tray icon appearance. Auto-recolouring isn't reliable on KDE
    # (plasmashell won't recolour a custom symbolic icon, and the app's own
    # colour scheme can differ from the panel theme), so the user picks:
    #   "color" -> the coloured brand badge; visible on any panel (default)
    #   "light" -> light monochrome glyph; for DARK panels
    #   "dark"  -> dark monochrome glyph; for LIGHT panels
    "tray_icon_style": "color",
    # If True (default): after the command, drop into an interactive shell so
    #   output stays visible. Close with exit/Ctrl-D/X.
    # If False: terminal closes immediately after the command exits (output lost).
    "terminal_keep_open": True,
    # User-defined ordering of plugin buttons in the popup. List of plugin
    # names (the `name` field, e.g. "copy", "clipboard-history"). Plugins
    # listed here appear first in this order; unlisted plugins fall back
    # to their built-in priority. Edit via Plugin Manager → Order tab.
    "plugin_order": [],
    # Plugin names pinned to the top of the popup. Pinned plugins always sort
    # before everything else, in this order, and can't be pushed down by
    # reordering the rest. Toggle via Plugin Manager → Order tab (right-click).
    "pinned_plugins": [],
    # Plugin names (Plugin.name) the user has switched off inside a
    # bundled .py file. The plugin_loader silently drops these at
    # register time. Lets users keep "Paste" but hide "Paste & Enter"
    # without removing the editing_actions bundle, etc.
    "disabled_plugins": [],
    # Which chat-AI services the send_to_ai plugin should expose as buttons.
    # Recognised: "google_ai", "claude", "chatgpt", "gemini", "perplexity".
    # Default is Google AI Search alone - it works without login or any
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
    # command before launching. Recommended - protects against highlighting
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
    # (each is independent - show only the ones you've actually set up).
    # Conventional defaults are pre-filled assuming the GitHub org name
    # matches the upstream repo; update or blank out as needed.
    "support_paypal_url":   "https://paypal.me/gaimsdev",
    # 'Skip short auto-popup selections' filter. Off by default to
    # match PopClip out of the box (no minimum-size knob there). When
    # on, the watcher silently drops selections shorter than
    # min_selection_length - useful if a flaky app keeps firing
    # X selection events for accidental clicks. The hotkey always
    # ignores this filter; see main.py _start_watcher.
    "min_selection_length_enabled": False,
    # AT-SPI focus-event listener. Lets us tell apart 'cursor in chat
    # input' from 'cursor in chat history' inside Electron apps where
    # the synchronous AT-SPI tree-walk dead-ends. Removed from the
    # Settings UI 2026-05-29 because the modifier+double-click path
    # covers the main use case and AT-SPI was correlated with a
    # Cinnamon panel segfault on 2026-05-25 (xapp-sn-watcher threw
    # ATK_IS_STATE_SET assertions, cinnamon dereferenced a freed
    # GObject and crashed the panel - see knowledge/linuxpop.md).
    # Stays here as a hidden settings.json knob for the rare user who
    # explicitly wants AT-SPI back on. Default off.
    "editable_atspi_listener_enabled": False,
    # Anchor the popup to the SELECTED-TEXT rectangle (via AT-SPI screen
    # extents) instead of the mouse pointer. On by default, but it only has
    # any effect when editable_atspi_listener_enabled is also True AND the
    # focused app exposes accessibility - otherwise the popup silently falls
    # back to the mouse pointer (so leaving this on costs nothing).
    "popup_anchor_to_selection": True,
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
