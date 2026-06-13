"""Local HTTP bridge for the Send-to-AI userscript flow.

The flow:

  1. send_to_ai.py POSTs the user's prompt to /prompt and gets back a UUID.
  2. send_to_ai.py launches the AI service URL with `#linuxpop=<uuid>`.
  3. The Tampermonkey/Violentmonkey userscript loaded in the browser reads
     window.location.hash, fetches /prompt/<uuid> via GM_xmlhttpRequest,
     then inserts the text into the page's editor with
     document.execCommand("insertText", ...).
  4. The bridge expires unclaimed prompts after PROMPT_TTL seconds.

Only listens on 127.0.0.1 - never on a public interface. Stdlib only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

log = logging.getLogger("linuxpop")

PROMPT_TTL = 60.0  # seconds; userscript should consume within this window
MAX_PROMPT_BYTES = 256 * 1024  # 256 KB cap on a single prompt
MAX_QUEUE = 32  # prevent unbounded growth if the userscript never consumes

# Each entry: uuid -> (text, expires_at, service, submit)
_queue: dict[str, tuple[str, float, str, bool]] = {}
_queue_lock = threading.Lock()
# Monotonic timestamp of the most recent userscript ping. Set by the
# userscript on first load after install via GET /installed; consumed
# by Settings to flip the install row to "installed".
_userscript_installed_at: float | None = None
# Marker file - lets Settings see "installed" across daemon restarts
# without having to wait for the userscript to fire again.
_INSTALL_MARKER = Path(os.path.expanduser(
    "~/.config/linuxpop/.userscript-installed"))


def _mark_userscript_installed() -> None:
    try:
        _INSTALL_MARKER.parent.mkdir(parents=True, exist_ok=True)
        _INSTALL_MARKER.write_text(str(int(time.time())))
    except OSError:
        pass


def userscript_marker_exists() -> bool:
    return _INSTALL_MARKER.is_file()


def clear_userscript_marker() -> None:
    """Removed only by the user via Settings (no UI for that today)
    or by the reset-to-defaults flow. The marker is set-once."""
    try:
        _INSTALL_MARKER.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _gc_queue() -> None:
    """Drop expired and over-cap entries. Caller must hold the lock."""
    now = time.monotonic()
    expired = [u for u, item in _queue.items() if item[1] < now]
    for u in expired:
        _queue.pop(u, None)
    while len(_queue) > MAX_QUEUE:
        oldest = min(_queue.items(), key=lambda kv: kv[1][1])[0]
        _queue.pop(oldest, None)


def enqueue_prompt(text: str, service: str = "", submit: bool = True) -> str:
    """Stash a prompt for the userscript to fetch. Returns the lookup UUID."""
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_BYTES} bytes")
    token = uuid.uuid4().hex
    with _queue_lock:
        _gc_queue()
        _queue[token] = (text, time.monotonic() + PROMPT_TTL, service, bool(submit))
    return token


def _pop_prompt(token: str) -> Optional[tuple[str, str, bool]]:
    with _queue_lock:
        _gc_queue()
        item = _queue.pop(token, None)
    if item is None:
        return None
    text, _, service, submit = item
    return text, service, submit


# ---- HTTP handler --------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    # Silence the default per-request stderr noise; we log explicitly.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The userscript runs from a different origin (claude.ai etc.) so
        # the response needs to be readable across the origin boundary.
        # GM_xmlhttpRequest also works without CORS, but we add it so a
        # plain fetch() from a console or test page works too.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, body: str,
              content_type: str = "text/plain; charset=utf-8") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"ok": True, "version": 1})
            return
        # Tampermonkey / Violentmonkey only intercept URLs ending in
        # `.user.js` (with the literal dot before "user"). Serve the
        # canonical path at /linuxpop.user.js so the install prompt
        # actually fires. The old /userscript.js path remains as a
        # convenience-redirect for anyone copy-pasting from older docs.
        if self.path == "/linuxpop.user.js" or self.path == "/userscript.js":
            body = _build_userscript(self.server.server_address[1])
            self._text(HTTPStatus.OK, body,
                       content_type="text/javascript; charset=utf-8")
            return
        if self.path == "/installed":
            # Userscript pings this once on every load after install so
            # Settings can flip from "Install userscript" -> ✓. Also
            # writes a marker file so the "installed" state survives
            # daemon restarts and the user doesn't reinstall what's
            # already there.
            global _userscript_installed_at
            _userscript_installed_at = time.monotonic()
            _mark_userscript_installed()
            self._json(HTTPStatus.OK, {"ok": True})
            return
        if self.path == "/installed/status":
            ts = _userscript_installed_at
            marker = userscript_marker_exists()
            self._json(HTTPStatus.OK, {
                "installed": ts is not None or marker,
                "seconds_ago": (time.monotonic() - ts) if ts else None,
                "marker_present": marker,
            })
            return
        if self.path.startswith("/prompt/"):
            token = self.path[len("/prompt/"):]
            result = _pop_prompt(token)
            if result is None:
                self._json(HTTPStatus.NOT_FOUND,
                           {"error": "not found or expired"})
                return
            text, service, submit = result
            self._json(HTTPStatus.OK,
                       {"text": text, "service": service, "submit": submit})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "unknown path"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/prompt":
            self._json(HTTPStatus.NOT_FOUND, {"error": "unknown path"})
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_PROMPT_BYTES + 1024:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "missing or oversized body"})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid JSON"})
            return
        text = payload.get("text", "") if isinstance(payload, dict) else ""
        service = payload.get("service", "") if isinstance(payload, dict) else ""
        submit = payload.get("submit", True) if isinstance(payload, dict) else True
        if not isinstance(text, str) or not text:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "text required"})
            return
        try:
            token = enqueue_prompt(text, str(service or ""), submit=bool(submit))
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._json(HTTPStatus.OK, {"uuid": token, "ttl": PROMPT_TTL})


# ---- server lifecycle ----------------------------------------------------

_server: Optional[ThreadingHTTPServer] = None
_server_thread: Optional[threading.Thread] = None
_server_lock = threading.Lock()
_USERSCRIPT_PATH = Path(__file__).parent / "userscript" / "linuxpop-send-to-ai.user.js"


def _try_bind(start_port: int, attempts: int = 10) -> ThreadingHTTPServer:
    last_err: Optional[OSError] = None
    for offset in range(attempts):
        port = start_port + offset
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        except OSError as exc:
            last_err = exc
            continue
    raise RuntimeError(
        f"bridge: no free port in {start_port}-{start_port + attempts - 1}"
    ) from last_err


def start(start_port: int = 8766) -> int:
    """Start the bridge if it isn't already. Returns the bound port."""
    global _server, _server_thread
    with _server_lock:
        if _server is not None:
            return _server.server_address[1]
        srv = _try_bind(start_port)
        t = threading.Thread(
            target=srv.serve_forever, name="linuxpop-bridge", daemon=True,
        )
        t.start()
        _server = srv
        _server_thread = t
        port = srv.server_address[1]
        log.info("[bridge] listening on 127.0.0.1:%d", port)
        return port


def stop() -> None:
    global _server, _server_thread
    with _server_lock:
        if _server is None:
            return
        try:
            _server.shutdown()
            _server.server_close()
        finally:
            _server = None
            _server_thread = None
            log.info("[bridge] stopped")


def is_running() -> bool:
    with _server_lock:
        return _server is not None


def current_port() -> Optional[int]:
    with _server_lock:
        return _server.server_address[1] if _server else None


# ---- userscript serving --------------------------------------------------

def _build_userscript(port: int) -> str:
    """Serve the userscript file with the bridge port baked into the
    @connect directive. Tampermonkey treats userscript.js with the correct
    headers as an install prompt."""
    try:
        body = _USERSCRIPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "// LinuxPop userscript file missing on bridge host\n"
    # The userscript needs to know which port to fetch from; rewrite the
    # placeholder. The @connect directive is added in the manifest header.
    return body.replace("__LINUXPOP_BRIDGE_PORT__", str(port))
