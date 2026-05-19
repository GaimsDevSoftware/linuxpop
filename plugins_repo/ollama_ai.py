"""Local AI via Ollama (http://localhost:11434).

Free, offline, no API keys. Install Ollama (`curl https://ollama.com/install.sh | sh`)
and pull a model (`ollama pull llama3.2:3b`). The plugin auto-detects whether
Ollama is reachable; if not, it skips registration so the popup isn't cluttered.

Settings in ~/.config/linuxpop/settings.json:
  "ollama_model": "llama3.2:3b"   (or "qwen2.5:7b", "mistral", etc.)
  "ollama_url":   "http://localhost:11434"
"""
from __future__ import annotations

import json
import subprocess
import threading
import urllib.error
import urllib.request

from classifier import ContentType
from plugin_base import Plugin

try:
    from settings import get_settings
    _settings = get_settings()
except Exception:
    _settings = None


def _cfg(key: str, default):
    if _settings is None:
        return default
    val = _settings.get(key, None)
    return val if val is not None else default


OLLAMA_URL = _cfg("ollama_url", "http://localhost:11434")
OLLAMA_MODEL = _cfg("ollama_model", "llama3.2:3b")


_reachable_cache: dict[str, float | bool] = {"value": False, "checked_at": 0.0}


def _ollama_reachable() -> bool:
    """Cached for 60s — plugin_loader.load_all() runs on every settings
    change and was blocking GTK 0.5s per reload before this cache.
    """
    import time
    now = time.time()
    if now - float(_reachable_cache["checked_at"]) < 60.0:
        return bool(_reachable_cache["value"])
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=0.5) as resp:
            value = resp.status == 200
    except Exception:
        value = False
    _reachable_cache["value"] = value
    _reachable_cache["checked_at"] = now
    return value


def _notify(title: str, body: str, urgent: bool = False) -> None:
    args = ["notify-send", "-i", "applications-science", title, body[:600]]
    if urgent:
        args.extend(["-u", "critical"])
    subprocess.run(args, check=False)


def _call_ollama(prompt: str) -> str:
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return (data.get("response") or "").strip()


def _run_async(prompt: str, label: str, text: str) -> None:
    """Run Ollama on a thread so GTK keeps responding, then notify."""
    _notify(f"Ollama — {label}", "Thinking… (may take a few seconds)")

    def worker():
        try:
            answer = _call_ollama(prompt)
        except urllib.error.URLError as exc:
            _notify("Ollama error", f"Could not reach Ollama: {exc}", urgent=True)
            return
        except Exception as exc:  # noqa: BLE001
            _notify("Ollama error", str(exc), urgent=True)
            return
        if not answer:
            _notify(f"Ollama — {label}", "Empty response")
            return
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=answer.encode("utf-8"),
            check=False,
        )
        _notify(f"Ollama — {label} (copied)", answer)

    threading.Thread(target=worker, daemon=True, name=f"ollama-{label}").start()


def _explain(text: str) -> None:
    prompt = (
        "Briefly and clearly explain the following in 2-4 sentences. "
        "Use the same language as the input. Don't repeat the text verbatim. "
        "Text:\n\n"
        f"{text}"
    )
    _run_async(prompt, "Explain", text)


def _summarize(text: str) -> None:
    prompt = (
        "Summarize the following in 1-3 sentences, only the key points. "
        "Use the same language as the input:\n\n"
        f"{text}"
    )
    _run_async(prompt, "Summarize", text)


def _translate_to_en(text: str) -> None:
    prompt = (
        "Translate the following to English. Return only the translation, "
        "no explanation:\n\n"
        f"{text}"
    )
    _run_async(prompt, "Translate to English", text)


def _translate_to_no(text: str) -> None:
    prompt = (
        "Translate the following to Norwegian. Return only the translation, "
        "no explanation:\n\n"
        f"{text}"
    )
    _run_async(prompt, "Translate to Norwegian", text)


def _rewrite(text: str) -> None:
    prompt = (
        "Rewrite the following so it's clearer, more precise and flows better. "
        "Keep the original language and meaning. Return only the rewritten text:\n\n"
        f"{text}"
    )
    _run_async(prompt, "Rewrite", text)


def register(register_plugin) -> None:
    if not _ollama_reachable():
        print(f"[ollama_ai] {OLLAMA_URL} unreachable — skipping registration")
        return

    print(f"[ollama_ai] connecting to {OLLAMA_URL} (model: {OLLAMA_MODEL})")
    types = (ContentType.PLAIN_TEXT, ContentType.URL, ContentType.EMAIL, ContentType.PATH)

    register_plugin(Plugin(
        name="ai-explain",
        icon="help-about-symbolic",
        tooltip="AI: explain",
        handler=_explain,
        content_types=types,
        priority=200,
    ))
    register_plugin(Plugin(
        name="ai-summarize",
        icon="view-list-compact-symbolic",
        tooltip="AI: summarize",
        handler=_summarize,
        content_types=types,
        priority=201,
    ))
    register_plugin(Plugin(
        name="ai-translate-en",
        icon="preferences-desktop-locale-symbolic",
        tooltip="AI: translate to English",
        handler=_translate_to_en,
        content_types=types,
        priority=202,
    ))
    register_plugin(Plugin(
        name="ai-translate-no",
        icon="preferences-desktop-locale-symbolic",
        tooltip="AI: translate to Norwegian",
        handler=_translate_to_no,
        content_types=types,
        priority=203,
    ))
    register_plugin(Plugin(
        name="ai-rewrite",
        icon="document-edit-symbolic",
        tooltip="AI: rewrite",
        handler=_rewrite,
        content_types=types,
        priority=204,
    ))
