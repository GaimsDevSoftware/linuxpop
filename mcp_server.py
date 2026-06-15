"""LinuxPop MCP (Model Context Protocol) server.

Exposes LinuxPop's selection / clipboard / snippets data to MCP-aware
AI clients like Claude Desktop, Cursor, and the Anthropic SDK. The
client spawns this process as a subprocess and communicates over its
stdin/stdout with newline-delimited JSON-RPC 2.0 messages.

Tools exposed (initial set):
  - get_current_selection : read X11 PRIMARY selection
  - get_clipboard         : read X11 CLIPBOARD selection
  - get_clipboard_history : read LinuxPop's saved clipboard history
  - list_snippets         : list the user's saved snippets
  - expand_snippet        : render a snippet by name (placeholders too)
  - list_recipes          : list installed recipes
  - put_on_clipboard      : write text to X11 CLIPBOARD

Why this matters: an agent using Claude Desktop can ask "what's the
user looking at right now?" and instantly get the selection - LinuxPop
becomes the bridge between the AI agent and the user's *active
context*, not just the chat thread.

Wire it into Claude Desktop's config like:

  {
    "mcpServers": {
      "linuxpop": {
        "command": "/home/robert/Dokumenter/Kode-prosjekter/linuxpop/linuxpop-mcp"
      }
    }
  }

Implementation note: stdlib only - no pip dependency on the `mcp`
package. We hand-roll the JSON-RPC framing because it's tiny and we
want LinuxPop's Flathub manifest to stay lean.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "linuxpop"
SERVER_VERSION = "0.9.3"

# Log to a file so the user can debug without stdout-noise corrupting
# the JSON-RPC stream the MCP client is reading.
_LOG_DIR = Path(os.path.expanduser("~/.cache/linuxpop"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_LOG_DIR / "mcp.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("linuxpop.mcp")


# ---- Tool implementations -----------------------------------------------

def _xclip_read(selection: str) -> str:
    try:
        res = subprocess.run(
            ["xclip", "-selection", selection, "-o"],
            capture_output=True, text=True, timeout=1.5,
        )
        if res.returncode != 0:
            return ""
        return res.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def tool_get_current_selection(_args: dict) -> dict:
    text = _xclip_read("primary")
    return {"text": text, "length": len(text)}


def tool_get_clipboard(_args: dict) -> dict:
    text = _xclip_read("clipboard")
    return {"text": text, "length": len(text)}


def tool_put_on_clipboard(args: dict) -> dict:
    text = (args or {}).get("text", "")
    if not isinstance(text, str):
        raise ValueError("'text' must be a string")
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"), check=False, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"xclip failed: {exc}") from exc
    return {"length": len(text), "ok": True}


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def tool_get_clipboard_history(args: dict) -> dict:
    limit = int((args or {}).get("limit", 25))
    limit = max(1, min(limit, 200))
    history_path = Path(os.path.expanduser(
        "~/.cache/linuxpop/clipboard/history.json"))
    raw = _read_json(history_path) or []
    items = []
    for entry in raw[:limit]:
        if not isinstance(entry, dict):
            continue
        items.append({
            "id":         entry.get("id"),
            "timestamp":  entry.get("timestamp"),
            "kind":       entry.get("kind", "text"),
            "text":       entry.get("text", ""),
            "image_path": entry.get("image_path"),
            "name":       entry.get("name"),
        })
    return {"count": len(items), "items": items}


def tool_list_snippets(_args: dict) -> dict:
    snippets_path = Path(os.path.expanduser(
        "~/.config/linuxpop/snippets.json"))
    raw = _read_json(snippets_path) or []
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        items.append({
            "name":     entry.get("name"),
            "trigger":  entry.get("trigger"),
            "category": entry.get("category"),
            "text":     entry.get("text", ""),
            "kind":     entry.get("kind", "text"),
        })
    return {"count": len(items), "items": items}


def tool_expand_snippet(args: dict) -> dict:
    name = (args or {}).get("name", "")
    if not name:
        raise ValueError("'name' is required")
    snippets_path = Path(os.path.expanduser(
        "~/.config/linuxpop/snippets.json"))
    snippets = _read_json(snippets_path) or []
    match = None
    for s in snippets:
        if isinstance(s, dict) and s.get("name") == name:
            match = s
            break
    if match is None:
        raise ValueError(f"no snippet named {name!r}")
    text = match.get("text", "")
    # Placeholder rendering happens in clipboard_history.py via the
    # snippet engine; we'd need to import that to get full {date} etc.
    # support. For an MCP-callable expand we just return the raw text -
    # the agent can prefill its own placeholders.
    return {"name": name, "text": text}


def tool_list_recipes(_args: dict) -> dict:
    recipes_dir = Path(os.path.expanduser("~/.config/linuxpop/recipes"))
    items = []
    if recipes_dir.is_dir():
        for f in sorted(recipes_dir.glob("*.json")):
            data = _read_json(f)
            if not isinstance(data, dict):
                continue
            items.append({
                "name":     data.get("name") or f.stem,
                "tooltip":  data.get("tooltip"),
                "action":   (data.get("action") or {}).get("type"),
            })
    return {"count": len(items), "items": items}


TOOLS = {
    "get_current_selection": {
        "fn": tool_get_current_selection,
        "schema": {
            "description": "Read whatever text the user currently has "
                           "highlighted via X11 PRIMARY selection.",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
    },
    "get_clipboard": {
        "fn": tool_get_clipboard,
        "schema": {
            "description": "Read the user's X11 CLIPBOARD content "
                           "(what Ctrl+V would paste).",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
    },
    "put_on_clipboard": {
        "fn": tool_put_on_clipboard,
        "schema": {
            "description": "Write text to the user's X11 CLIPBOARD so "
                           "they can paste it elsewhere.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        },
    },
    "get_clipboard_history": {
        "fn": tool_get_clipboard_history,
        "schema": {
            "description": "Read LinuxPop's saved clipboard history "
                           "(recent copies, in newest-first order).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer",
                              "minimum": 1, "maximum": 200,
                              "default": 25},
                },
                "additionalProperties": False,
            },
        },
    },
    "list_snippets": {
        "fn": tool_list_snippets,
        "schema": {
            "description": "List the user's saved snippets (name, "
                           "trigger, category, text).",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
    },
    "expand_snippet": {
        "fn": tool_expand_snippet,
        "schema": {
            "description": "Return the raw text of a snippet by name. "
                           "Placeholder rendering ({date}, {ask:...}) is "
                           "left to the caller.",
            "inputSchema": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    "list_recipes": {
        "fn": tool_list_recipes,
        "schema": {
            "description": "List the user's installed recipes (no-code "
                           "plugin definitions).",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
    },
}


# ---- JSON-RPC plumbing --------------------------------------------------

def _send(message: dict) -> None:
    """Write one JSON-RPC message as a single newline-terminated line.
    Uses sys.stdout.buffer to keep the framing byte-precise even when
    the client locale is funky."""
    payload = json.dumps(message, ensure_ascii=False)
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def _send_result(req_id, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": req_id, "result": result})


def _send_error(req_id, code: int, msg: str) -> None:
    _send({"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": msg}})


def _handle_initialize(req_id, _params: dict) -> None:
    _send_result(req_id, {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
    })


def _handle_tools_list(req_id, _params: dict) -> None:
    tools = []
    for name, spec in TOOLS.items():
        schema = spec["schema"]
        tools.append({
            "name": name,
            "description": schema.get("description", ""),
            "inputSchema": schema.get("inputSchema", {"type": "object"}),
        })
    _send_result(req_id, {"tools": tools})


def _handle_tools_call(req_id, params: dict) -> None:
    name = params.get("name", "")
    args = params.get("arguments", {}) or {}
    spec = TOOLS.get(name)
    if spec is None:
        _send_error(req_id, -32601, f"unknown tool {name!r}")
        return
    try:
        result = spec["fn"](args)
    except ValueError as exc:
        _send_error(req_id, -32602, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        log.exception("tool %s crashed", name)
        _send_error(req_id, -32000, f"{type(exc).__name__}: {exc}")
        return
    # MCP wraps tool output in a content array; we use one text block
    # with the JSON-encoded payload so clients can parse if they want.
    _send_result(req_id, {
        "content": [
            {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)},
        ],
        "isError": False,
    })


HANDLERS = {
    "initialize":  _handle_initialize,
    "tools/list":  _handle_tools_list,
    "tools/call":  _handle_tools_call,
}


def serve() -> None:
    """Read JSON-RPC requests from stdin until EOF, dispatching one
    line at a time. Notifications (no id) are processed silently;
    errors during dispatch are returned as JSON-RPC error responses."""
    log.info("linuxpop-mcp serve loop start")
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            log.warning("malformed JSON: %s", exc)
            continue
        method = msg.get("method", "")
        params = msg.get("params") or {}
        req_id = msg.get("id")
        # Notifications have no id - we ack them with nothing.
        if method in ("notifications/initialized",
                      "notifications/cancelled",
                      "initialized"):
            log.info("notification: %s", method)
            continue
        handler = HANDLERS.get(method)
        if handler is None:
            if req_id is not None:
                _send_error(req_id, -32601, f"unknown method {method!r}")
            continue
        try:
            handler(req_id, params)
        except Exception:  # noqa: BLE001
            log.exception("handler for %s crashed", method)
            if req_id is not None:
                _send_error(req_id, -32603, "internal server error")
    log.info("linuxpop-mcp serve loop end (stdin EOF)")


if __name__ == "__main__":
    serve()
