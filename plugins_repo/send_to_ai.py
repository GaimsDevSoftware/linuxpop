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

def _send_via_url(service: str, url_template: str, text: str) -> None:
    encoded = urllib.parse.quote(text, safe="")
    url = url_template.format(text_url=encoded)
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return
    subprocess.run(
        ["notify-send", "-i", "applications-internet",
         f"Sent to {service}",
         "URL prefill (auto-submits on load). Edit history in your chat to revise."],
        check=False,
    )


# ---- paste mode (xdotool window-detection) ------------------------------

def _find_window(search_terms: list[str], timeout: float) -> str | None:
    if not shutil.which("xdotool"):
        return None
    deadline = time.time() + timeout
    while time.time() < deadline:
        for term in search_terms:
            result = subprocess.run(
                ["xdotool", "search", "--name", term],
                capture_output=True, text=True,
            )
            ids = [x for x in result.stdout.strip().splitlines() if x]
            if ids:
                return sorted(ids, key=int)[-1]
        time.sleep(0.25)
    return None


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


def _send_via_paste(service: str, url: str, window_terms: list[str], text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"), check=False,
    )
    try:
        subprocess.Popen(["xdg-open", url], start_new_session=True)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return

    def worker():
        window_id = _find_window(window_terms, _WINDOW_TIMEOUT)
        if window_id is not None and shutil.which("xdotool"):
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", window_id], check=False,
            )
            subprocess.run(["xdotool", "windowfocus", window_id], check=False)
            _wait_until_active(window_id, timeout=_FOCUS_TIMEOUT,
                                stability=_FOCUS_STABLE)
            if _SETTLE > 0:
                time.sleep(_SETTLE)
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=False,
            )
        else:
            subprocess.run(
                ["notify-send", "-i", "dialog-warning", service,
                 "Window didn't appear in time. Paste manually with Ctrl+V."],
                check=False,
            )

    threading.Thread(target=worker, daemon=True, name=f"ai-paste-{service}").start()

    body = ("Pasting once the tab loads. Review and press Enter to send."
            if shutil.which("xdotool")
            else "Text copied — paste manually with Ctrl+V (xdotool missing)")
    subprocess.run(
        ["notify-send", "-i", "applications-internet",
         f"Sent to {service}", body],
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
        window_terms=["Claude", "claude.ai"],
        priority=100,
    ),
    "chatgpt": dict(
        name="send-to-chatgpt",
        icon="linuxpop-chatgpt",
        tooltip="Ask ChatGPT",
        service="ChatGPT",
        mode="url",
        # Auto-submits, but no other native option. Settings can flip to "paste"
        # via ai_chatgpt_mode if the user wants review-before-send.
        url_template="https://chatgpt.com/?q={text_url}",
        url="https://chat.openai.com/",
        window_terms=["ChatGPT", "chat.openai.com", "chatgpt.com"],
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
        icon="emoji-objects-symbolic",
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
        icon="system-search-symbolic",
        tooltip="Google AI Mode",
        service="Google AI",
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
            _send_via_url(spec["service"], spec["url_template"], text)
        else:
            _send_via_paste(
                spec["service"], spec["url"],
                spec.get("window_terms", []), text,
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
