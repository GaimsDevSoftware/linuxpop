"""Send selected text to a web-based chat AI.

Two strategies per service:

- mode="url"   - append the text as a URL parameter so the chat box is
                 prefilled. Most services that support this auto-submit on
                 page load (we can't suppress that). Fast and reliable.
- mode="paste" - open the chat page, wait for the browser window to focus,
                 then simulate Ctrl+V via xdotool to paste the clipboard
                 into the chat box. Slower but lets the user edit before
                 submitting. Used for services with no URL-prefill support.

As of late 2025:
  Claude     - `?q=` was disabled. paste only.
  ChatGPT    - `?q=` works, auto-submits. URL mode.
  Gemini     - no URL prefill exists. paste only.
  Perplexity - `?q=` works, auto-submits. URL mode.
  Google AI  - `?q=&udm=50` opens AI Mode in Search, auto-submits. URL mode.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import urllib.parse

from classifier import ContentType
from plugin_base import Plugin

# Reads happen per call so live settings edits (slider in Settings, or
# direct settings.json edit) take effect without a daemon restart. The
# old module-level snapshot froze these at import time.
try:
    from settings import get_settings
except Exception:
    get_settings = None  # type: ignore[assignment]


def _cfg(key: str, default):
    if get_settings is None:
        return default
    try:
        val = get_settings().get(key, default)
        return val if val is not None else default
    except Exception:
        return default


def _window_timeout() -> float:
    return float(_cfg("ai_window_timeout_seconds", 10.0))


def _focus_timeout() -> float:
    return float(_cfg("ai_focus_timeout_seconds", 3.0))


def _focus_stability() -> float:
    return float(_cfg("ai_focus_stability_seconds", 0.25))


def _paste_settle() -> float:
    return float(_cfg("ai_paste_settle_seconds", 0.2))


def _auto_submit_enabled() -> bool:
    """Read fresh each call so the toggle takes effect without a restart."""
    return bool(_cfg("ai_paste_auto_submit", False))

# Browsers truncate URLs around 8 KB and UX degrades earlier. Past this,
# fall back to paste mode for that single click instead of opening a
# broken URL.
_URL_MAX_CHARS = 6000


def _desktop_env() -> dict:
    """Build an env that lets browsers / xdg-open talk to the running
    desktop session.

    If LinuxPop was started without inheriting the user's session
    environment (e.g. launched from a barebones shell), Firefox's
    "is there an instance running?" probe fails because DBus is the
    transport it uses to find the existing process. With no DBus
    address the new firefox-bin starts, sees the profile lock from
    the *real* running Firefox, can't reach it, and pops the
    "already running but not responding" dialog every single time.

    Best-effort patch: copy os.environ and, if DBUS_SESSION_BUS_ADDRESS
    or XDG_RUNTIME_DIR are missing, fill them in with the canonical
    values from /run/user/$UID. Same trick for DISPLAY/XAUTHORITY -
    cheap insurance for systemd-user daemons.
    """
    import os
    env = dict(os.environ)
    uid = os.getuid()
    runtime_dir = f"/run/user/{uid}"
    if not env.get("XDG_RUNTIME_DIR"):
        env["XDG_RUNTIME_DIR"] = runtime_dir
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    if not env.get("DISPLAY"):
        env["DISPLAY"] = ":0"
    return env


# ---- url mode -----------------------------------------------------------

def _send_via_url(service: str, url_template: str, text: str,
                  auto_submits: bool = True) -> None:
    encoded = urllib.parse.quote(text, safe="")
    url = url_template.format(text_url=encoded)
    try:
        # Route through the platform backend so the browser is raised to the
        # foreground on Wayland/KDE (focus-stealing prevention otherwise leaves
        # it in the background). The except-FileNotFoundError below is dead now
        # but harmless.
        __import__("platform_backend").get_backend().open_url(url)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return
    body = ("Prefilled - press Enter in the chat to send."
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
        # Visible browsers - anchor on WM_CLASS so "Claude" inside VSCode
        # or a Slack channel name doesn't match.
        try:
            browsers = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--class", _BROWSER_CLASS_RE],
                capture_output=True, text=True, timeout=1.5,
            )
            browser_ids = {x for x in browsers.stdout.strip().splitlines() if x}
            # Visible windows whose title contains the service name
            named = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--name", name_term],
                capture_output=True, text=True, timeout=1.5,
            )
        except subprocess.TimeoutExpired:
            # xdotool hung - bail out of the wait loop so the worker
            # thread doesn't sit forever holding the saved clipboard.
            print("[send_to_ai] xdotool search timed out; giving up")
            return None
        named_ids = {x for x in named.stdout.strip().splitlines() if x}
        candidates = _intersect(browser_ids, named_ids)
        if candidates:
            # Newest wid first - that's usually the freshly-opened tab/window
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
        try:
            result = subprocess.run(
                ["xdotool", "getactivewindow"],
                capture_output=True, text=True, timeout=1.0,
            )
        except subprocess.TimeoutExpired:
            return False
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


def _read_selection(sel: str) -> bytes:
    """Snapshot a selection so _stuff_text can restore it afterwards."""
    try:
        out = subprocess.run(
            ["xclip", "-selection", sel, "-o"],
            capture_output=True, timeout=0.8,
        )
        return out.stdout
    except (OSError, subprocess.SubprocessError):
        return b""


def _stuff_text(text: str) -> None:
    """Put text on BOTH X11 selections so any paste shortcut works."""
    payload = text.encode("utf-8")
    for sel in ("clipboard", "primary"):
        subprocess.run(
            ["xclip", "-selection", sel],
            input=payload, check=False,
            timeout=2.0,
        )


def _restore_selections(saved: dict[str, bytes]) -> None:
    """Put the user's pre-stuffed clipboard back. Scheduled after the
    paste keystroke lands so the chat box already has the prompt."""
    for sel, data in saved.items():
        if not data:
            continue
        subprocess.run(
            ["xclip", "-selection", sel],
            input=data, check=False, timeout=2.0,
        )


def _paste_keystroke(key: str = "ctrl+v") -> None:
    """Send the configured paste shortcut. Default is Ctrl+V which is the
    most universal - modern contenteditable inputs (ProseMirror, Lexical,
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
        # xprop, not `xdotool getwindowclassname` - the latter is missing
        # from xdotool on Mint/Debian and silently returns ''.
        wclass = subprocess.run(
            ["xprop", "-id", window_id, "WM_CLASS"],
            capture_output=True, text=True, timeout=0.5,
        ).stdout.strip()
        return f"wid={window_id} class={wclass!r} name={name!r}"
    except (OSError, subprocess.SubprocessError):
        return f"wid={window_id}"


def _is_wayland_backend() -> bool:
    """True on the native Wayland/KDE backend, where xdotool/wmctrl don't work
    and we must paste via wl-clipboard + wtype + KWin instead."""
    try:
        return getattr(
            __import__("platform_backend").get_backend(), "name", "") == "wayland_kde"
    except Exception:
        return False


def _send_via_paste_wayland(service: str, url: str, window_terms: list[str],
                            text: str, paste_key: str = "ctrl+v",
                            settle_extra: float = 0.0) -> None:
    """Wayland/KDE paste path (xdotool/wmctrl are X11-only and silently no-op
    under Wayland, which is why Claude never received the text on Fedora).

    Flow: stash the prompt on the clipboard -> open the chat page (the platform
    backend force-raises the browser via a KWin script) -> poll KWin's
    active-window class+title until the *browser* is focused AND showing the
    service -> inject the paste keystroke with wtype. Focus is confirmed before
    pasting so we never blindly paste into whatever window happened to be in
    front (e.g. a terminal); if it can't be confirmed we leave the prompt on the
    clipboard and ask the user to press Ctrl+V."""
    backend = __import__("platform_backend").get_backend()
    saved_clip = backend.read_selection("clipboard")

    def _restore():
        try:
            if saved_clip:
                backend.set_clipboard(saved_clip)
        except Exception:
            pass

    backend.set_clipboard(text)
    try:
        backend.open_url(url)
    except FileNotFoundError:
        _restore()
        subprocess.run(["notify-send", "--hint=byte:transient:1", "-t", "3000",
                        "-i", "dialog-error", f"Could not open {service}",
                        "xdg-open is missing"], check=False)
        return

    try:
        browser_kw = (backend._browser_keyword() or "").lower()
    except Exception:
        browser_kw = ""
    service_terms = [t.lower() for t in (window_terms or []) if t] + [service.lower()]

    def worker():
        window_timeout = max(4.0, _window_timeout())
        deadline = time.monotonic() + window_timeout
        ready = False
        while time.monotonic() < deadline:
            try:
                hay = backend.active_window_haystacks()  # [class, title] lowercased
            except Exception:
                hay = []
            if hay:
                has_browser = (not browser_kw) or any(browser_kw in h for h in hay)
                has_service = any(term in h for h in hay for term in service_terms)
                if has_browser and has_service:
                    ready = True
                    break
            time.sleep(0.25)

        # Keystroke injection (wtype/ydotool) cannot deliver Ctrl+V on this KWin
        # Wayland session — the compositor doesn't pass injected modifier chords
        # to clients (verified: even Ctrl+A doesn't register). So we never fake a
        # paste (which could land in the wrong window). The prompt is on the
        # clipboard and the chat is focused; the user presses Ctrl+V — their real
        # keyboard works fine. (Install the browser userscript for hands-free
        # insertion; this path is the no-setup fallback.)
        print(f"[send_to_ai] wl-paste {service}: ready={ready} "
              f"active={hay if ready else '?'}; prompt on clipboard for Ctrl+V",
              flush=True)
        subprocess.run(["notify-send", "--hint=byte:transient:1", "-t", "6000",
                        "-i", "applications-internet", f"{service}: press Ctrl+V",
                        "Your selection is on the clipboard — press Ctrl+V in the "
                        "chat box, then Enter to send."], check=False)
        time.sleep(45.0)   # keep the prompt on the clipboard while they paste
        _restore()

    threading.Thread(target=worker, daemon=True,
                     name=f"ai-wlpaste-{service}").start()


def _send_via_paste(service: str, url: str, window_terms: list[str],
                    text: str, paste_key: str = "ctrl+v",
                    settle_extra: float = 0.0) -> None:
    if _is_wayland_backend():
        _send_via_paste_wayland(service, url, window_terms, text,
                                paste_key=paste_key, settle_extra=settle_extra)
        return
    # Snapshot the user's clipboard + primary BEFORE we clobber them,
    # so we can restore once paste lands. Without this, asking Claude
    # silently overwrites whatever was on the clipboard with the
    # question prompt - a real annoyance for anyone juggling notes.
    saved_selections = {
        "clipboard": _read_selection("clipboard"),
        "primary":   _read_selection("primary"),
    }
    _stuff_text(text)
    try:
        # Route through the platform backend so the browser is raised to the
        # foreground on Wayland/KDE (focus-stealing prevention otherwise leaves
        # it in the background). The except-FileNotFoundError below is dead now
        # but harmless.
        __import__("platform_backend").get_backend().open_url(url)
    except FileNotFoundError:
        # Restore immediately on failure - we never paste, so the user's
        # clipboard shouldn't stay overwritten.
        _restore_selections(saved_selections)
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error",
             f"Could not open {service}", "xdg-open is missing"],
            check=False,
        )
        return

    def worker():
        # Re-read live settings on each invocation (replacing an old
        # import-time snapshot) so slider changes in Settings apply on
        # the next click without a daemon restart.
        window_timeout = _window_timeout()
        focus_timeout = _focus_timeout()
        focus_stability = _focus_stability()
        paste_settle = _paste_settle()
        # Try each search term until one finds a real browser window
        window_id = None
        for term in window_terms:
            window_id = _find_browser_window(term, window_timeout / max(1, len(window_terms)))
            if window_id is not None:
                break
        if window_id is None or not shutil.which("xdotool"):
            print(f"[send_to_ai] {service}: no browser window matched within "
                  f"{window_timeout}s - terms={window_terms}")
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

        _wait_until_active(window_id, timeout=focus_timeout,
                            stability=focus_stability)
        # Per-service extra settle on top of the global paste_settle.
        # Claude's input takes longer to mount than Gemini's.
        total_settle = paste_settle + settle_extra
        if total_settle > 0:
            time.sleep(total_settle)
        _paste_keystroke(paste_key)
        # Give the chat box a beat to actually receive the paste before
        # we restore the user's clipboard - pasting and restoring in
        # the same tick has a small race where the destination sees the
        # old clipboard content.
        time.sleep(0.4)
        # Optional auto-submit: hit Return so the user doesn't have to.
        # Off by default - some services lose drafts, and shift+enter for
        # newline is harder once the prompt has been sent. Read fresh so
        # the toggle takes effect without a daemon restart.
        if _auto_submit_enabled():
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "Return"],
                check=False,
            )
        _restore_selections(saved_selections)

    threading.Thread(target=worker, daemon=True, name=f"ai-paste-{service}").start()

    body = ("Pasting once the tab loads. Review and press Enter to send."
            if shutil.which("xdotool")
            else "Text copied - paste manually with Ctrl+V or Shift+Insert")
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "applications-internet",
         f"Opened {service}", body],
        check=False,
    )


# ---- service registry ---------------------------------------------------

_SERVICES = {
    # Google AI Search is the lowest-friction default: no login, no
    # subscription, URL-mode auto-submits, works in any browser. Bumped
    # to the lowest priority number (=highest popup priority) so it
    # appears first if multiple services are enabled.
    "google_ai": dict(
        name="send-to-google-ai",
        icon="linuxpop-google-ai",
        tooltip="Google AI Search",
        service="Google AI Search",
        mode="url",
        url_template="https://www.google.com/search?q={text_url}&udm=50",
        url="https://www.google.com/",
        window_terms=["Google"],
        priority=100,
    ),
    "claude": dict(
        name="send-to-claude",
        icon="linuxpop-claude",
        tooltip="Ask Claude",
        service="Claude",
        mode="paste",
        url="https://claude.ai/new",
        userscript_url="https://claude.ai/new",
        userscript_supported=True,
        # Narrow window match so Claude Desktop / Claude Code don't hijack.
        # The /new path forces the chat-input page, with title "Claude".
        window_terms=["claude.ai", "Claude"],
        paste_key="ctrl+v",      # ProseMirror-style editor; Ctrl+V is what it expects
        settle_extra=0.8,        # Claude's input mounts noticeably slower than Gemini's
        priority=101,
        api_key_env="ANTHROPIC_API_KEY",
    ),
    "chatgpt": dict(
        name="send-to-chatgpt",
        icon="linuxpop-chatgpt",
        tooltip="Ask ChatGPT",
        service="ChatGPT",
        mode="url",
        # &autoSubmit=0 was added by OpenAI after a security disclosure
        # (Tenable TRA-2025-22) - it prefills the chat input WITHOUT
        # sending. The user presses Enter when ready. Best of both worlds.
        url_template="https://chatgpt.com/?q={text_url}&autoSubmit=0",
        url="https://chat.openai.com/",
        userscript_url="https://chatgpt.com/",
        userscript_supported=True,
        window_terms=["ChatGPT", "chat.openai.com", "chatgpt.com"],
        auto_submits=False,
        priority=102,
        api_key_env="OPENAI_API_KEY",
    ),
    "gemini": dict(
        name="send-to-gemini",
        icon="linuxpop-gemini",
        tooltip="Ask Gemini",
        service="Gemini",
        mode="paste",
        url="https://gemini.google.com/app",
        userscript_url="https://gemini.google.com/app",
        userscript_supported=True,
        window_terms=["Gemini", "gemini.google.com"],
        priority=103,
        api_key_env="GEMINI_API_KEY",
    ),
    "perplexity": dict(
        name="send-to-perplexity",
        icon="linuxpop-perplexity",
        tooltip="Ask Perplexity",
        service="Perplexity",
        mode="url",
        url_template="https://www.perplexity.ai/?q={text_url}",
        url="https://www.perplexity.ai/",
        userscript_url="https://www.perplexity.ai/",
        userscript_supported=True,
        window_terms=["Perplexity", "perplexity.ai"],
        priority=104,
    ),
}



def _send_via_api(service: str, spec: dict, text: str) -> None:
    """Use a REST API call with the user's own key. Cheap and reliable
    but pay-as-you-go pricing - users explicitly opt in. Currently
    implemented for Claude (Anthropic) and ChatGPT (OpenAI). Gemini
    API falls back to browser since the Google Cloud setup is heavy."""
    try:
        from settings import get_settings
        s = get_settings()
    except Exception:
        s = None
    handlers = {
        "Claude":  ("ai_anthropic_api_key", _call_anthropic_api),
        "ChatGPT": ("ai_openai_api_key",    _call_openai_api),
    }
    handler_pair = handlers.get(service)
    if handler_pair is None or s is None:
        # API not supported for this service - fall back to URL/paste
        if "url_template" in spec:
            _send_via_url(service, spec["url_template"], text,
                          auto_submits=spec.get("auto_submits", True))
        else:
            _send_via_paste(
                service, spec["url"], spec.get("window_terms", []), text,
                paste_key=spec.get("paste_key", "ctrl+v"),
                settle_extra=spec.get("settle_extra", 0.0),
            )
        return
    key_setting, api_call = handler_pair
    api_key = (s.get(key_setting) or "").strip()
    if not api_key:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3500",
             "-i", "dialog-warning", f"{service}: no API key set",
             "Falling back to browser. Add your API key from "
             "Settings > AI services > API mode."],
            check=False,
        )
        if "url_template" in spec:
            _send_via_url(service, spec["url_template"], text,
                          auto_submits=spec.get("auto_submits", True))
        else:
            _send_via_paste(
                service, spec["url"], spec.get("window_terms", []), text,
                paste_key=spec.get("paste_key", "ctrl+v"),
                settle_extra=spec.get("settle_extra", 0.0),
            )
        return
    _show_api_response_dialog_async(service, api_key, text, api_call)


def _call_anthropic_api(api_key: str, prompt: str) -> str:
    """Single-turn message to Claude via Anthropic Messages API."""
    import json
    import urllib.request
    body = json.dumps({
        "model": "claude-sonnet-4-5",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    parts = [c.get("text", "") for c in data.get("content", [])
             if c.get("type") == "text"]
    return "".join(parts).strip() or "(empty reply)"


def _call_openai_api(api_key: str, prompt: str) -> str:
    """Single-turn message to ChatGPT via OpenAI Chat Completions API."""
    import json
    import urllib.request
    body = json.dumps({
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        return "(empty reply)"
    return (choices[0].get("message") or {}).get("content", "").strip() or "(empty reply)"
def _show_api_response_dialog_async(
    service: str, api_key: str, prompt: str, api_call,
) -> None:
    """Same shape as the CLI dialog but the worker calls a Python
    function (REST) instead of running a subprocess."""
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    def _open_dialog() -> None:
        dlg = Gtk.Dialog(title=f"{service} - reply", flags=0)
        dlg.set_default_size(640, 480)
        dlg.set_icon_name("linuxpop")
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.set_default_response(Gtk.ResponseType.CLOSE)
        content = dlg.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(10)
        content.set_margin_bottom(10)
        content.set_margin_start(12)
        content.set_margin_end(12)

        spinner = Gtk.Spinner()
        spinner.start()
        spinner_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spinner_row.pack_start(spinner, False, False, 0)
        wait_lbl = Gtk.Label(label=f"Waiting for {service}...", xalign=0)
        wait_lbl.get_style_context().add_class("dim-label")
        spinner_row.pack_start(wait_lbl, True, True, 0)
        content.add(spinner_row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_shadow_type(Gtk.ShadowType.IN)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.get_style_context().add_class("lp-cmd-edit")
        buf = view.get_buffer()
        scroll.add(view)
        content.pack_start(scroll, True, True, 0)

        copy_btn = Gtk.Button(label="Copy reply")
        copy_btn.connect(
            "clicked",
            lambda _b: subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=buf.get_text(buf.get_start_iter(),
                                    buf.get_end_iter(),
                                    True).encode("utf-8"),
                check=False, timeout=2.0,
            ),
        )
        copy_btn.set_sensitive(False)
        dlg.get_action_area().pack_start(copy_btn, False, False, 0)
        dlg.get_action_area().reorder_child(copy_btn, 0)

        dlg.show_all()

        def worker() -> None:
            try:
                reply = api_call(api_key, prompt)
            except Exception as exc:
                reply = f"[API call failed: {exc}]"
            GLib.idle_add(_set_reply, reply)

        def _set_reply(reply: str) -> bool:
            try:
                spinner.stop()
                spinner_row.hide()
                buf.set_text(reply)
                copy_btn.set_sensitive(True)
            except Exception:
                pass
            return False

        threading.Thread(
            target=worker, daemon=True, name=f"ai-api-{service}").start()

        dlg.run()
        dlg.destroy()

    GLib.idle_add(_open_dialog)


# ---- userscript mode (browser bridge) -----------------------------------

def _send_via_userscript(service: str, spec: dict, text: str) -> None:
    """Hand the prompt to the local HTTP bridge, then open the service's
    chat page with `#linuxpop=<uuid>` appended. The userscript installed
    in the user's browser reads the hash, fetches the prompt, and calls
    document.execCommand("insertText", ...) - the only path that works
    against React/ProseMirror editors like Claude and Gemini."""
    base_url = spec.get("userscript_url") or spec.get("url")
    if not base_url:
        # No browser URL on this spec; fall back to whatever paste mode
        # would have done.
        if "url_template" in spec:
            _send_via_url(service, spec["url_template"], text,
                          auto_submits=spec.get("auto_submits", True))
        else:
            _send_via_paste(
                service, spec["url"], spec.get("window_terms", []), text,
                paste_key=spec.get("paste_key", "ctrl+v"),
                settle_extra=spec.get("settle_extra", 0.0),
            )
        return

    # Bridge import is lazy so the rest of the plugin still works if the
    # daemon's working directory is set up oddly.
    try:
        import bridge_server  # type: ignore
    except Exception as exc:
        print(f"[send_to_ai] bridge import failed: {exc}; falling back to paste")
        _send_via_paste(
            service, spec["url"], spec.get("window_terms", []), text,
            paste_key=spec.get("paste_key", "ctrl+v"),
            settle_extra=spec.get("settle_extra", 0.0),
        )
        return

    # If the browser userscript has never registered (no install marker), the
    # chat page would open but nothing would inject the text — exactly the
    # "nothing reaches Claude's box" symptom. Fall back to the paste path, which
    # needs no browser extension. Auto-upgrades to true userscript injection the
    # moment the user installs it and loads a matched site (which sets the marker).
    if not bridge_server.userscript_marker_exists():
        _send_via_paste(
            service, spec.get("url") or base_url, spec.get("window_terms", []),
            text, paste_key=spec.get("paste_key", "ctrl+v"),
            settle_extra=spec.get("settle_extra", 0.0),
        )
        return

    try:
        from settings import get_settings
        start_port = int(get_settings().get("ai_userscript_bridge_port", 8766) or 8766)
    except Exception:
        start_port = 8766

    try:
        port = bridge_server.start(start_port)
    except Exception as exc:
        print(f"[send_to_ai] bridge failed to start: {exc}; falling back to paste")
        _send_via_paste(
            service, spec["url"], spec.get("window_terms", []), text,
            paste_key=spec.get("paste_key", "ctrl+v"),
            settle_extra=spec.get("settle_extra", 0.0),
        )
        return

    # Persist the actually-bound port so the Settings UI can show it and
    # so the next launch reuses the same number.
    try:
        from settings import get_settings
        s = get_settings()
        if int(s.get("ai_userscript_bridge_port", 0) or 0) != port:
            s.set("ai_userscript_bridge_port", port)
            s.save()
    except Exception:
        pass

    try:
        token = bridge_server.enqueue_prompt(
            text, service=service, submit=_auto_submit_enabled())
    except ValueError as exc:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",
             "-i", "dialog-error", f"Could not send to {service}", str(exc)],
            check=False,
        )
        return

    sep = "&" if "#" in base_url else "#"
    url = f"{base_url}{sep}linuxpop={token}"

    try:
        # Route through the platform backend so the browser is raised to the
        # foreground on Wayland/KDE (focus-stealing prevention otherwise leaves
        # it in the background). The except-FileNotFoundError below is dead now
        # but harmless.
        __import__("platform_backend").get_backend().open_url(url)
    except FileNotFoundError:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",
             "-i", "dialog-error", f"Could not open {service}",
             "xdg-open is missing"],
            check=False,
        )
        return

    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "2500",
         "-i", "applications-internet", f"Opened {service}",
         "Userscript will insert the prompt when the page loads."],
        check=False,
    )


# ---- dispatch -----------------------------------------------------------

def _send(spec: dict):
    """Build a handler that dispatches based on the global ai_send_method
    setting and the service's per-method capability. Per-service mode
    overrides via ai_<service_key>_mode are still honoured for legacy
    setups."""
    service_key = spec["name"].replace("send-to-", "").replace("-", "_")

    def handler(text: str) -> None:
        try:
            from settings import get_settings
            settings_obj = get_settings()
            send_method = (settings_obj.get("ai_send_method") or "userscript").lower()
            mode_override = settings_obj.get(f"ai_{service_key}_mode", None)
        except Exception:
            send_method = "browser"
            mode_override = None

        # Per-service override still wins (legacy setting). The "cli"
        # mode was dropped 2026-05-29 - if a stale override is on disk
        # we silently fall through to the spec's native mode.
        if mode_override and mode_override != "cli":
            mode = mode_override
        elif send_method == "api":
            mode = "api"
        elif send_method == "userscript":
            mode = "userscript"
        else:
            mode = spec.get("mode", "paste")

        # Auto-fallback for url: too-long or no template → paste.
        if mode == "url":
            if len(text) > _URL_MAX_CHARS or "url_template" not in spec:
                mode = "paste"

        # Userscript mode is only meaningful for services the userscript's
        # @match patterns cover (Claude, ChatGPT, Gemini, Perplexity). For
        # services without explicit support (Google AI Search), fall back
        # to their native mode so the button still does something useful.
        if mode == "userscript" and not spec.get("userscript_supported"):
            mode = spec.get("mode", "paste")

        if mode == "api":
            _send_via_api(spec["service"], spec, text)
        elif mode == "userscript":
            _send_via_userscript(spec["service"], spec, text)
        elif mode == "url":
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

    # Backfill plugin_order with currently-enabled AI services that the
    # user toggled on AFTER customising their order. Without this, those
    # services land past max_popup_buttons and the user wonders why their
    # ChatGPT/Gemini/Perplexity button never shows up. Idempotent: only
    # touches services missing from the order.
    try:
        from settings import get_settings as _gs
        s = _gs()
        order = list(s.get("plugin_order") or [])
        if order:
            last_ai = -1
            for i, n in enumerate(order):
                if n.startswith("send-to-"):
                    last_ai = i
            changed = False
            for k in enabled:
                spec = _SERVICES.get(k)
                if not spec:
                    continue
                if spec["name"] not in order:
                    if last_ai >= 0:
                        last_ai += 1
                        order.insert(last_ai, spec["name"])
                    else:
                        order.append(spec["name"])
                        last_ai = len(order) - 1
                    changed = True
            if changed:
                s.set("plugin_order", order)
                s.save()
    except Exception:
        pass

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
