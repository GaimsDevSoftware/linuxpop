"""Send selected text to a web-based chat AI.

Two strategies per service:

- mode="url"   — append the text as a URL parameter so the chat box is
                 prefilled. Most services that support this auto-submit on
                 page load (we can't suppress that). Fast and reliable.
- mode="paste" — open the chat page, wait for the browser window to focus,
                 then simulate Ctrl+V via xdotool to paste the clipboard
                 into the chat box. Slower but lets the user edit before
                 submitting. Used for services with no URL-prefill support.

As of late 2025:
  Claude     — `?q=` was disabled. paste only.
  ChatGPT    — `?q=` works, auto-submits. URL mode.
  Gemini     — no URL prefill exists. paste only.
  Perplexity — `?q=` works, auto-submits. URL mode.
  Google AI  — `?q=&udm=50` opens AI Mode in Search, auto-submits. URL mode.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import urllib.parse

from classifier import ContentType
from plugin_base import Plugin

# Tunable via settings (only used in paste mode):
try:
    from settings import get_settings
    _settings = get_settings()
    _WINDOW_TIMEOUT = float(_settings.get("ai_window_timeout_seconds", 10.0) or 10.0)
    _FOCUS_TIMEOUT  = float(_settings.get("ai_focus_timeout_seconds", 3.0) or 3.0)
    _FOCUS_STABLE   = float(_settings.get("ai_focus_stability_seconds", 0.25) or 0.25)
    _SETTLE         = float(_settings.get("ai_paste_settle_seconds", 0.2) or 0.2)
except Exception:
    _WINDOW_TIMEOUT = 10.0
    _FOCUS_TIMEOUT  = 3.0
    _FOCUS_STABLE   = 0.25
    _SETTLE         = 0.2

# Browsers truncate URLs around 8 KB and UX degrades earlier. Past this,
# fall back to paste mode for that single click instead of opening a
# broken URL.
_URL_MAX_CHARS = 6000


# ---- url mode -----------------------------------------------------------

def _send_via_url(service: str, url_template: str, text: str,
                  auto_submits: bool = True) -> None:
    encoded = urllib.parse.quote(text, safe="")
    url = url_template.format(text_url=encoded)
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return
    body = ("Prefilled — press Enter in the chat to send."
            if not auto_submits
            else "Sent. (URL-mode auto-submits on this service.)")
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "applications-internet",
         f"Opened {service}", body],
        check=False,
    )


# ---- paste mode (xdotool window-detection) ------------------------------
#
# Reliability fixes from 2026 research (~60% → ~95% success on this stack):
#  1. Match windows by WM_CLASS = browser intersected with title = service.
#     Stops VSCode tabs / Slack channels named "claude" from hijacking.
#  2. Focus via `wmctrl -ia` (cooperates with most WMs), not xdotool's
#     windowactivate which many WMs silently ignore.
#  3. Paste via Shift+Insert + PRIMARY selection. Many Electron/Chromium
#     builds drop synthetic Ctrl+V; Shift+Insert is more reliable.
#  4. Poll for actual focus before pasting, not a fixed sleep.

_BROWSER_CLASS_RE = (
    "^(Google-chrome|chromium|chromium-browser|firefox|firefox-esr|"
    "brave-browser|vivaldi-stable|microsoft-edge)$"
)


def _intersect(a: set[str], b: set[str]) -> set[str]:
    return a & b


def _find_browser_window(name_term: str, timeout: float) -> str | None:
    """Return the newest visible browser window whose title contains name_term."""
    if not shutil.which("xdotool"):
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Visible browsers — anchor on WM_CLASS so "Claude" inside VSCode
        # or a Slack channel name doesn't match.
        browsers = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--class", _BROWSER_CLASS_RE],
            capture_output=True, text=True,
        )
        browser_ids = {x for x in browsers.stdout.strip().splitlines() if x}
        # Visible windows whose title contains the service name
        named = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", name_term],
            capture_output=True, text=True,
        )
        named_ids = {x for x in named.stdout.strip().splitlines() if x}
        candidates = _intersect(browser_ids, named_ids)
        if candidates:
            # Newest wid first — that's usually the freshly-opened tab/window
            return sorted(candidates, key=int)[-1]
        time.sleep(0.2)
    return None


def _focus_via_wmctrl(window_id_decimal: str) -> bool:
    """Activate a window using wmctrl, which most WMs honour even when
    xdotool's windowactivate is refused. Returns True if the command ran."""
    if not shutil.which("wmctrl"):
        return False
    try:
        hex_id = f"0x{int(window_id_decimal):08x}"
        result = subprocess.run(
            ["wmctrl", "-ia", hex_id],
            capture_output=True, text=True, timeout=1.0,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _wait_until_active(window_id: str, timeout: float, stability: float) -> bool:
    if not shutil.which("xdotool"):
        return False
    deadline = time.time() + timeout
    stable_since: float | None = None
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True,
        )
        active = result.stdout.strip()
        if active == window_id:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stability:
                return True
        else:
            stable_since = None
        time.sleep(0.05)
    return False


def _stuff_text(text: str) -> None:
    """Put text on BOTH X11 selections so any paste shortcut works."""
    payload = text.encode("utf-8")
    for sel in ("clipboard", "primary"):
        subprocess.run(
            ["xclip", "-selection", sel],
            input=payload, check=False,
        )


def _paste_keystroke(key: str = "ctrl+v") -> None:
    """Send the configured paste shortcut. Default is Ctrl+V which is the
    most universal — modern contenteditable inputs (ProseMirror, Lexical,
    Slate, Draft, TipTap) all wire it. Shift+Insert is offered as a
    per-service alternative for legacy Electron textareas that swallow
    synthetic Ctrl+V."""
    subprocess.run(
        ["xdotool", "key", "--clearmodifiers", "--delay", "12", key],
        check=False,
    )


def _diagnose_window(window_id: str) -> str:
    """Return a short string describing what window we matched, for logs.
    Helps catch cases where 'Claude' matches Claude Desktop or a VSCode
    panel instead of the browser tab we just opened."""
    try:
        name = subprocess.run(
            ["xdotool", "getwindowname", window_id],
            capture_output=True, text=True, timeout=0.5,
        ).stdout.strip()
        wclass = subprocess.run(
            ["xdotool", "getwindowclassname", window_id],
            capture_output=True, text=True, timeout=0.5,
        ).stdout.strip()
        return f"wid={window_id} class={wclass!r} name={name!r}"
    except (OSError, subprocess.SubprocessError):
        return f"wid={window_id}"


def _send_via_paste(service: str, url: str, window_terms: list[str],
                    text: str, paste_key: str = "ctrl+v",
                    settle_extra: float = 0.0) -> None:
    _stuff_text(text)
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return

    def worker():
        # Try each search term until one finds a real browser window
        window_id = None
        for term in window_terms:
            window_id = _find_browser_window(term, _WINDOW_TIMEOUT / max(1, len(window_terms)))
            if window_id is not None:
                break
        if window_id is None or not shutil.which("xdotool"):
            print(f"[send_to_ai] {service}: no browser window matched within "
                  f"{_WINDOW_TIMEOUT}s — terms={window_terms}")
            subprocess.run(
                ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-warning", service,
                 "Browser window didn't appear in time. "
                 "Paste manually with Ctrl+V."],
                check=False,
            )
            return

        print(f"[send_to_ai] {service} matched: {_diagnose_window(window_id)}")

        # wmctrl is the reliable focus path; xdotool windowactivate is a fallback
        if not _focus_via_wmctrl(window_id):
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", window_id], check=False,
            )
            subprocess.run(["xdotool", "windowfocus", window_id], check=False)

        _wait_until_active(window_id, timeout=_FOCUS_TIMEOUT,
                            stability=_FOCUS_STABLE)
        # Per-service extra settle on top of the global _SETTLE. Claude's
        # input takes longer to mount than Gemini's, for example.
        total_settle = _SETTLE + settle_extra
        if total_settle > 0:
            time.sleep(total_settle)
        _paste_keystroke(paste_key)

    threading.Thread(target=worker, daemon=True, name=f"ai-paste-{service}").start()

    body = ("Pasting once the tab loads. Review and press Enter to send."
            if shutil.which("xdotool")
            else "Text copied — paste manually with Ctrl+V or Shift+Insert")
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "applications-internet",
         f"Opened {service}", body],
        check=False,
    )


# ---- service registry ---------------------------------------------------

_SERVICES = {
    "claude": dict(
        name="send-to-claude",
        icon="linuxpop-claude",
        tooltip="Ask Claude",
        service="Claude",
        mode="paste",
        url="https://claude.ai/new",
        # Narrow window match so Claude Desktop / Claude Code don't hijack.
        # The /new path forces the chat-input page, with title "Claude".
        window_terms=["claude.ai", "Claude"],
        paste_key="ctrl+v",      # ProseMirror-style editor; Ctrl+V is what it expects
        settle_extra=0.8,        # Claude's input mounts noticeably slower than Gemini's
        priority=100,
    ),
    "chatgpt": dict(
        name="send-to-chatgpt",
        icon="linuxpop-chatgpt",
        tooltip="Ask ChatGPT",
        service="ChatGPT",
        mode="url",
        # &autoSubmit=0 was added by OpenAI after a security disclosure
        # (Tenable TRA-2025-22) — it prefills the chat input WITHOUT
        # sending. The user presses Enter when ready. Best of both worlds.
        url_template="https://chatgpt.com/?q={text_url}&autoSubmit=0",
        url="https://chat.openai.com/",
        window_terms=["ChatGPT", "chat.openai.com", "chatgpt.com"],
        auto_submits=False,
        priority=101,
    ),
    "gemini": dict(
        name="send-to-gemini",
        icon="linuxpop-gemini",
        tooltip="Ask Gemini",
        service="Gemini",
        mode="paste",
        url="https://gemini.google.com/app",
        window_terms=["Gemini", "gemini.google.com"],
        priority=102,
    ),
    "perplexity": dict(
        name="send-to-perplexity",
        icon="linuxpop-perplexity",
        tooltip="Ask Perplexity",
        service="Perplexity",
        mode="url",
        url_template="https://www.perplexity.ai/?q={text_url}",
        url="https://www.perplexity.ai/",
        window_terms=["Perplexity", "perplexity.ai"],
        priority=103,
    ),
    "google_ai": dict(
        name="send-to-google-ai",
        icon="linuxpop-google-ai",
        tooltip="Google AI Search",
        service="Google AI Search",
        mode="url",
        url_template="https://www.google.com/search?q={text_url}&udm=50",
        url="https://www.google.com/",
        window_terms=["Google"],
        priority=104,
    ),
}


def _send(spec: dict):
    """Build a handler that dispatches based on the per-service mode.
    Settings can override mode via ai_<service_key>_mode."""
    service_key = spec["name"].replace("send-to-", "").replace("-", "_")

    def handler(text: str) -> None:
        try:
            from settings import get_settings
            mode_override = get_settings().get(f"ai_{service_key}_mode", None)
        except Exception:
            mode_override = None
        mode = mode_override or spec.get("mode", "paste")

        # Auto-fallback: url mode but prompt too long → paste mode
        if mode == "url":
            if len(text) > _URL_MAX_CHARS:
                mode = "paste"
            elif "url_template" not in spec:
                mode = "paste"

        if mode == "url":
            _send_via_url(
                spec["service"], spec["url_template"], text,
                auto_submits=spec.get("auto_submits", True),
            )
        else:
            _send_via_paste(
                spec["service"], spec["url"],
                spec.get("window_terms", []), text,
                paste_key=spec.get("paste_key", "ctrl+v"),
                settle_extra=spec.get("settle_extra", 0.0),
            )

    return handler


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT, ContentType.URL,
             ContentType.EMAIL, ContentType.PATH)

    try:
        from settings import get_settings
        enabled = get_settings().get("ai_services", list(_SERVICES.keys())) or []
    except Exception:
        enabled = list(_SERVICES.keys())

    for key in enabled:
        spec = _SERVICES.get(key)
        if spec is None:
            continue
        register_plugin(Plugin(
            name=spec["name"],
            icon=spec["icon"],
            tooltip=spec["tooltip"],
            handler=_send(spec),
            content_types=types,
            priority=spec["priority"],
        ))
