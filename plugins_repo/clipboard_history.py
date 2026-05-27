"""Clipboard history + snippets picker.

Two backing stores:
  - history.json   : rolling buffer of the last N clipboard entries
  - snippets.json  : pinned/named entries that never expire

The picker dialog has Recent + Snippets tabs, a search box, and pastes
the chosen entry at the cursor of whichever app had focus when the
picker was summoned (via xdotool windowactivate + ctrl+v).

Activated either from the popup ("Clipboard…" plugin button) or from
the global clipboard_hotkey (default Super+V).

Settings (~/.config/linuxpop/settings.json):
  "clipboard_history_size":   25
  "clipboard_history_images": true
  "clipboard_hotkey":         "super+v"   (handled by main.py)
"""
from __future__ import annotations

import json
import os
import re
import select as _select
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: F401, E402
except (ImportError, ValueError):
    pass


def _force_to_front(window) -> None:
    """Raise the picker above every other window on X11, including any
    Settings / Plugin Manager dialog already open. Cinnamon's WM ranks
    windows by focus-history first, keep_above second; without an
    explicit X11 raise the picker can be marked keep_above yet still
    appear behind a more-recently-focused LinuxPop dialog.

    The fix is to call GdkWindow.raise_() AND a final window.present()
    after the WM hint flips, which together force a proper restack.
    """
    try:
        window.deiconify()
        window.set_accept_focus(True)
        window.set_focus_on_map(True)
        # Permanent keep-above while the picker is visible. Cleared
        # implicitly on _on_destroy.
        window.set_keep_above(True)
        gdk_win = window.get_window()
        if gdk_win is not None:
            try:
                ts = Gdk.X11.get_server_time(gdk_win)
            except Exception:
                ts = Gtk.get_current_event_time() or 0
            window.present_with_time(ts)
            # Explicit X11 raise -- doesn't go through focus arbitration,
            # so it survives focus-stealing prevention.
            try:
                gdk_win.raise_()
            except Exception:
                pass
            # And demand keyboard focus on top of that.
            try:
                gdk_win.focus(ts)
            except Exception:
                pass
        # Final present() nudge after keep_above + raise so the WM
        # re-evaluates stack order with the new flags in place.
        window.present()
    except Exception:
        try:
            window.present()
        except Exception:
            pass


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


HISTORY_SIZE = max(1, int(_cfg("clipboard_history_size", 25)))
CAPTURE_IMAGES = bool(_cfg("clipboard_history_images", True))
POLL_INTERVAL = max(0.1, float(_cfg("clipboard_poll_interval", 0.6)))

CACHE_DIR = Path(os.path.expanduser("~/.cache/linuxpop/clipboard"))
HISTORY_FILE = CACHE_DIR / "history.json"
SNIPPETS_FILE = Path(os.path.expanduser("~/.config/linuxpop/snippets.json"))
IMAGES_DIR = CACHE_DIR / "images"


# ----- data --------------------------------------------------------------

@dataclass
class Entry:
    id: str
    timestamp: float
    kind: str         # "text" | "image"
    text: str = ""
    image_path: str = ""
    name: str = ""    # only used for snippets
    # Snippet auto-expansion shortcode(s). Comma-separated to allow more
    # than one trigger per snippet ("rraak, rb, email" all → same text).
    # Single-trigger entries from older saves load as-is because the
    # parser strips whitespace and ignores empty parts.
    trigger: str = ""

    def trigger_list(self) -> List[str]:
        """Parse `trigger` into a list of clean shortcodes."""
        return [t.strip() for t in self.trigger.split(",") if t.strip()]

    def preview(self, max_len: int = 80) -> str:
        if self.name:
            return self.name
        if self.kind == "image":
            return f"🖼  Image - {Path(self.image_path).name}"
        s = self.text.replace("\n", "↵").replace("\t", "  ")
        return s[:max_len] + "…" if len(s) > max_len else s

    def search_haystack(self) -> str:
        return f"{self.name}\n{self.trigger}\n{self.text}\n{self.image_path}".lower()


_history: List[Entry] = []
_snippets: List[Entry] = []
_history_lock = threading.Lock()
_snippets_lock = threading.Lock()
_watcher_thread: Optional[threading.Thread] = None
_watcher_stop = threading.Event()


# ----- persistence -------------------------------------------------------

def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _load_list(path: Path, target: List[Entry]) -> bool:
    """Return True on a successful load (including empty list), False if
    the file existed but couldn't be parsed. Callers use this to decide
    whether subsequent reference-tracking operations (like orphan-image
    sweeps) are safe - sweeping when the load failed would treat every
    image as unreferenced and delete the lot."""
    if not path.is_file():
        return True
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            try:
                target.append(Entry(**item))
            except TypeError:
                continue
        return True
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[clipboard] could not load {path}: {exc}")
        return False


def _save_history() -> None:
    with _history_lock:
        data = [asdict(e) for e in _history]
    try:
        _atomic_write_json(HISTORY_FILE, data)
    except OSError as exc:
        print(f"[clipboard] could not save history: {exc}")


def _save_snippets() -> None:
    with _snippets_lock:
        data = [asdict(e) for e in _snippets]
    try:
        _atomic_write_json(SNIPPETS_FILE, data)
    except OSError as exc:
        print(f"[clipboard] could not save snippets: {exc}")


def _add_entry(entry: Entry) -> None:
    with _history_lock:
        if _history and _history[0].kind == entry.kind:
            if entry.kind == "text" and _history[0].text == entry.text:
                return
            if entry.kind == "image" and _history[0].image_path == entry.image_path:
                return
        _history.insert(0, entry)
        while len(_history) > HISTORY_SIZE:
            old = _history.pop()
            if old.kind == "image" and old.image_path:
                try:
                    Path(old.image_path).unlink(missing_ok=True)
                except OSError:
                    pass
    _save_history()


def _pin_entry(entry: Entry, name: str = "") -> None:
    """Copy a history entry into the persistent snippets store."""
    new_id = uuid.uuid4().hex[:12]
    snippet = Entry(
        id=new_id,
        timestamp=time.time(),
        kind=entry.kind,
        text=entry.text,
        image_path=entry.image_path,
        name=name,
    )
    with _snippets_lock:
        _snippets.insert(0, snippet)
    _save_snippets()
    _rebuild_trigger_index()


def _unpin_snippet(snippet_id: str) -> None:
    with _snippets_lock:
        _snippets[:] = [s for s in _snippets if s.id != snippet_id]
    _save_snippets()
    _rebuild_trigger_index()


def _create_snippet(name: str, text: str, trigger: str = "") -> None:
    """Make a brand-new snippet from scratch (not derived from history)."""
    snippet = Entry(
        id=uuid.uuid4().hex[:12],
        timestamp=time.time(),
        kind="text",
        text=text,
        name=name,
        trigger=trigger.strip(),
    )
    with _snippets_lock:
        _snippets.insert(0, snippet)
    _save_snippets()
    _rebuild_trigger_index()


def _set_snippet_trigger(snippet_id: str, trigger: str) -> None:
    with _snippets_lock:
        for s in _snippets:
            if s.id == snippet_id:
                s.trigger = trigger.strip()
                break
    _save_snippets()
    _rebuild_trigger_index()


# ----- trigger index -----------------------------------------------------

# Built from snippets every time the list changes. Keyed by trigger
# string (case-sensitive). Read by the XRecord watcher thread; built
# on the GLib thread via _rebuild_trigger_index().
_trigger_index: dict[str, Entry] = {}
_trigger_index_lock = threading.Lock()


def _rebuild_trigger_index() -> None:
    new_index: dict[str, Entry] = {}
    with _snippets_lock:
        for s in _snippets:
            if s.kind != "text":
                continue
            # Each snippet can register multiple triggers via comma
            # separation. setdefault keeps first-seen-wins; snippets
            # are stored newest-first, so the most-recently-edited
            # snippet wins on collision (intentional).
            for trig in s.trigger_list():
                new_index.setdefault(trig, s)
    with _trigger_index_lock:
        _trigger_index.clear()
        _trigger_index.update(new_index)


def _rename_snippet(snippet_id: str, new_name: str) -> None:
    with _snippets_lock:
        for s in _snippets:
            if s.id == snippet_id:
                s.name = new_name
                break
    _save_snippets()
    _rebuild_trigger_index()


# ----- trigger watcher (XRecord) -----------------------------------------

# Characters that, when typed, cause us to check whether the buffer ends
# with a known trigger. Whitespace only - espanso / AutoKey use the same
# rule. Including punctuation here breaks snippets that *start* with a
# punctuation prefix (e.g. ";mvh"), so we keep the boundary set minimal.
_TRIGGER_CHARS = set(" \t\n")
_TRIGGER_BUFFER_MAX = 64

_trigger_watcher: Optional["_TriggerWatcher"] = None


class _TriggerWatcher:
    """Background XRecord listener. When the user types a snippet trigger
    followed by a word-boundary character (space, tab, enter, punctuation),
    expand it in place: backspace the shortcode + the boundary char,
    paste the rendered snippet, optionally reposition the caret.

    Privacy: this thread sees every keystroke, but stores at most the
    last 64 characters in memory and never writes them to disk. Disabled
    by default; opt-in via the 'snippet_triggers_enabled' setting.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._buffer: List[str] = []
        self._lock = threading.Lock()
        # Two Display handles - XRecord needs a dedicated one for the
        # passive context; the other is used for keycode→char lookups.
        self._record_display = None
        self._local_display = None
        self._ctx = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-triggers",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # disable_context returns control of the recording display so
        # the blocking enable_context() call in _run() unwinds.
        try:
            if self._local_display is not None and self._ctx is not None:
                self._local_display.record_disable_context(self._ctx)
                self._local_display.flush()
        except Exception:
            pass

    # -- internals --

    def _run(self) -> None:
        try:
            from Xlib import display as Xdisplay, X, XK
            from Xlib.ext import record
            from Xlib.protocol import rq
        except ImportError:
            print("[triggers] python-xlib missing record extension - disabled")
            return
        try:
            self._record_display = Xdisplay.Display()
            self._local_display = Xdisplay.Display()
            v = self._local_display.record_get_version(0, 0)
            print(f"[triggers] XRecord {v.major_version}.{v.minor_version} ready")
        except Exception as exc:
            print(f"[triggers] could not open X displays: {exc}")
            return

        self._ctx = self._local_display.record_create_context(
            0,
            [record.AllClients],
            [{
                "core_requests": (0, 0),
                "core_replies": (0, 0),
                "ext_requests": (0, 0, 0, 0),
                "ext_replies": (0, 0, 0, 0),
                "delivered_events": (0, 0),
                "device_events": (X.KeyPress, X.KeyRelease),
                "errors": (0, 0),
                "client_started": False,
                "client_died": False,
            }],
        )

        def handler(reply):
            if reply.category != record.FromServer:
                return
            if reply.client_swapped:
                return
            data = reply.data
            while len(data):
                event, data = rq.EventField(None).parse_binary_value(
                    data, self._record_display.display, None, None,
                )
                if event.type == X.KeyPress:
                    self._on_keypress(event, X, XK)

        try:
            self._local_display.record_enable_context(self._ctx, handler)
        except Exception as exc:
            print(f"[triggers] record_enable_context exited: {exc}")
        finally:
            try:
                self._local_display.record_free_context(self._ctx)
            except Exception:
                pass
            self._ctx = None

    def _on_keypress(self, event, X, XK) -> None:
        # Drop chord modifiers (Ctrl/Alt) so Ctrl-S doesn't pollute the
        # buffer with 's'. Pure shift is fine - that's just capitalisation.
        state = event.state
        if state & (X.ControlMask | X.Mod1Mask | X.Mod4Mask):
            # Ctrl, Alt, Super → reset buffer so a chord can't be mistaken
            # for a partial trigger.
            with self._lock:
                self._buffer.clear()
            return

        shift = bool(state & X.ShiftMask)
        caps = bool(state & X.LockMask)
        index = 1 if shift else 0

        keysym = self._local_display.keycode_to_keysym(event.detail, index)
        if keysym == 0:
            return

        # Special non-printable handling
        if keysym == XK.XK_BackSpace:
            with self._lock:
                if self._buffer:
                    self._buffer.pop()
            return
        if keysym in (XK.XK_Return, XK.XK_KP_Enter):
            self._handle_char("\n")
            return
        if keysym == XK.XK_Tab:
            self._handle_char("\t")
            return
        if keysym == XK.XK_Escape:
            with self._lock:
                self._buffer.clear()
            return
        # Ignore pure modifier keys, arrow keys, F-keys, etc.
        if 0xff00 <= keysym <= 0xffff and keysym not in (
            XK.XK_space,
        ):
            return

        ch = self._keysym_to_char(keysym, caps, shift, XK)
        if not ch:
            return
        self._handle_char(ch)

    @staticmethod
    def _keysym_to_char(keysym, caps_lock: bool, shift: bool, XK) -> Optional[str]:
        # XK.keysym_to_string covers Latin-1 and common Unicode aliases.
        s = XK.keysym_to_string(keysym)
        if s is None or len(s) > 1:
            # Latin-1 fallback for codes XK doesn't have a name for.
            if 0x20 <= keysym <= 0xff:
                s = chr(keysym)
            else:
                return None
        # Caps Lock without Shift uppercases alphabetic chars; with Shift
        # the two cancel out. (Approximation - non-Latin caps behavior
        # varies by layout.)
        if caps_lock and not shift and s.isalpha():
            return s.upper()
        if caps_lock and shift and s.isalpha():
            return s.lower()
        return s

    def _handle_char(self, ch: str) -> None:
        is_boundary = ch in _TRIGGER_CHARS
        with self._lock:
            if is_boundary:
                # Check whether buffer ends with a known trigger. Try the
                # longest matches first so "abcd" wins over "cd" if both
                # are registered. Case-insensitive; the actual typed
                # form is captured separately so case-propagation can
                # apply later.
                buf = "".join(self._buffer)
                buf_lc = buf.lower()
                with _trigger_index_lock:
                    triggers = sorted(_trigger_index.keys(), key=len,
                                       reverse=True)
                    matched = None
                    for trig in triggers:
                        trig_lc = trig.lower()
                        if not buf_lc.endswith(trig_lc):
                            continue
                        if not _trigger_word_boundary_ok(buf, trig_lc):
                            continue
                        typed = buf[-len(trig_lc):]
                        matched = (trig, typed, _trigger_index[trig])
                        break
                self._buffer.clear()
                if matched is not None:
                    trig, typed, snippet = matched
                    GLib.idle_add(_fire_trigger_expansion,
                                  trig, typed, ch, snippet)
                    return
                # Boundary char that isn't part of a trigger: start fresh.
                return
            self._buffer.append(ch)
            if len(self._buffer) > _TRIGGER_BUFFER_MAX:
                # Trim oldest. Keep the tail; trigger matches are
                # always at the end.
                del self._buffer[:len(self._buffer) - _TRIGGER_BUFFER_MAX]


def _trigger_word_boundary_ok(buf: str, trig_lc: str) -> bool:
    """The trigger has already been confirmed to be a suffix of buf
    (case-insensitive). Reject the match unless the trigger occupies a
    word boundary on its leading side:
      - it's at the very start of the buffer, OR
      - the char immediately before it is NOT a word char (letters /
        digits / underscore), OR
      - the trigger itself starts with a non-word char (e.g. ';mvh') -
        the punctuation is its own boundary.
    Trailing boundary is implicit because we're called from a boundary
    handler (space/tab/enter just triggered the check).
    """
    if len(buf) == len(trig_lc):
        return True
    before = buf[-(len(trig_lc) + 1)]
    if not (before.isalnum() or before == "_"):
        return True
    if not (trig_lc[0].isalnum() or trig_lc[0] == "_"):
        return True
    return False


def _detect_case_style(typed: str) -> str:
    """Classify the case pattern of what the user actually typed.
    Returns one of: 'lower' / 'title' / 'upper' / 'mixed'. Triggers
    with only digits/symbols return 'lower' (no transformation)."""
    letters = [c for c in typed if c.isalpha()]
    if not letters:
        return "lower"
    if all(c.islower() for c in letters):
        return "lower"
    if all(c.isupper() for c in letters):
        # Need at least 2 letters to call it intentional UPPER - a
        # single capital letter is ambiguous with title-case.
        return "upper" if len(letters) >= 2 else "title"
    # First letter capital, rest lower → title
    if letters[0].isupper() and all(c.islower() for c in letters[1:]):
        return "title"
    return "mixed"


def _propagate_case(typed: str, output: str) -> str:
    """Apply the case pattern from `typed` (what the user actually
    typed) to `output` (the rendered snippet text). Mixed/lower leaves
    output alone; title capitalises the first alpha char; upper
    uppercases everything."""
    style = _detect_case_style(typed)
    if style in ("lower", "mixed"):
        return output
    if style == "upper":
        return output.upper()
    if style == "title":
        for i, c in enumerate(output):
            if c.isalpha():
                return output[:i] + c.upper() + output[i+1:]
    return output


def _active_window_haystacks() -> list[str]:
    """Lower-cased window title + WM_CLASS of the focused window. Used
    by the trigger blocklist to decide whether to expand here. xprop
    rather than xdotool getwindowclassname because the latter is missing
    on Mint/Debian builds."""
    out: list[str] = []
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=0.3,
        ).stdout.strip()
        if not wid:
            return out
        try:
            name = subprocess.run(
                ["xdotool", "getwindowname", wid],
                capture_output=True, text=True, timeout=0.3,
            ).stdout.strip()
            if name:
                out.append(name.lower())
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            klass = subprocess.run(
                ["xprop", "-id", wid, "WM_CLASS"],
                capture_output=True, text=True, timeout=0.3,
            ).stdout.strip()
            if klass:
                out.append(klass.lower())
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        pass
    return out


def _trigger_blocked() -> bool:
    """Return True if the active window matches any pattern in the
    user's trigger_blocklist_patterns setting."""
    patterns = list(_cfg("trigger_blocklist_patterns", []) or [])
    if not patterns:
        return False
    haystacks = _active_window_haystacks()
    if not haystacks:
        return False
    for p in patterns:
        p_lc = (p or "").strip().lower()
        if p_lc and any(p_lc in h for h in haystacks):
            return True
    return False


def _fire_trigger_expansion(
    trigger: str, typed: str, boundary_char: str, snippet: Entry,
) -> bool:
    """GLib-main-thread callback. Backspace over the typed shortcode +
    boundary, then paste the rendered snippet. `typed` is what the user
    actually typed (used for case-propagation; may differ in case from
    the canonical `trigger`). Returns False so idle_add fires only
    once."""
    if snippet.kind != "text":
        return False
    # Snippets with {ask:} can't run inline from a trigger - a blocking
    # dialog mid-keystroke is jarring. Skip silently; the user can still
    # invoke them via the picker.
    if "{ask:" in snippet.text:
        print(f"[triggers] '{trigger}' has {{ask:}} - skipping (use picker)")
        return False
    # Per-app/site blocklist: skip expansion in user-configured apps
    # (password managers, terminals, banking sites, etc.).
    if _trigger_blocked():
        print(f"[triggers] '{trigger}' skipped - active window is in blocklist")
        return False
    rendered, cursor_left, _ = render_placeholders(
        snippet.text, lambda _label: None,
    )
    # Case-propagation: typed 'Rraak' → Title-case output;
    # typed 'RRAAK' → UPPER output.
    rendered = _propagate_case(typed, rendered)

    def worker():
        if not shutil.which("xdotool"):
            return
        # Wait for the user to physically release the boundary key
        # (space/tab/enter) before we start synthesising keystrokes.
        # Without this, xdotool's --clearmodifiers reads the live key
        # state mid-press and leaves modifiers like Ctrl in a "stuck"
        # logical state in the receiving app (scroll-zooms instead of
        # scroll, etc.). 60 ms is generous; humans don't tap-release
        # that fast.
        time.sleep(0.06)
        # Delete the typed shortcode + the boundary char.
        # --delay 0 drops events in Electron/Chrome targets that filter
        # for human-rate key timing; 16 ms (~1 frame at 60 Hz) is fast
        # enough that the user never sees the typed chars, but slow
        # enough that every BackSpace lands. --clearmodifiers is
        # unnecessary here - Backspace doesn't care about modifier
        # state, and the modifier dance occasionally leaves Ctrl wedged.
        n_delete = len(trigger) + 1
        subprocess.run(
            ["xdotool", "key",
             "--repeat", str(n_delete), "--delay", "16", "BackSpace"],
            check=False,
        )
        # Give the receiving app a moment to settle before we move on
        # to clipboard staging + paste.
        time.sleep(0.08)
        # Stage the rendered text on the clipboard.
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=rendered.encode("utf-8"), check=False, timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return
        subprocess.run(
            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
            check=False,
        )
        if cursor_left > 0:
            time.sleep(0.05)
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers",
                 "--repeat", str(cursor_left), "--delay", "0", "Left"],
                check=False,
            )
        # Defensive: force-release every modifier we might have nudged.
        # xdotool's --clearmodifiers is supposed to restore modifier
        # state, but the restoration races with the user's own key-up
        # event for the boundary key and sometimes leaves Ctrl/Shift
        # logically stuck (Firefox starts zooming on scroll, etc.).
        # Cheap insurance: explicit keyups on the usual suspects.
        time.sleep(0.02)
        subprocess.run(
            ["xdotool", "keyup", "ctrl", "shift", "alt",
             "super", "Control_L", "Control_R"],
            check=False,
        )

    threading.Thread(target=worker, daemon=True,
                     name="trigger-expansion").start()
    return False


def _maybe_start_trigger_watcher() -> None:
    """Honour the snippet_triggers_enabled setting. Idempotent."""
    global _trigger_watcher
    enabled = bool(_cfg("snippet_triggers_enabled", False))
    if enabled:
        if _trigger_watcher is None:
            _trigger_watcher = _TriggerWatcher()
        _trigger_watcher.start()
    else:
        if _trigger_watcher is not None:
            _trigger_watcher.stop()


# ----- clipboard reading -------------------------------------------------

def _read_clipboard_targets() -> list[str]:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, timeout=0.5,
        )
        return out.stdout.decode("utf-8", errors="replace").splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def _read_clipboard_text() -> str:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, timeout=0.5,
        )
        return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_clipboard_image() -> Optional[bytes]:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            capture_output=True, timeout=1.0,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


_state = {"last_text": "", "last_image_hash": b""}

# Persistent dedup-hash file. Without this, every daemon restart with
# an image still on the clipboard re-saves it as a "new" entry.
_DEDUP_STATE_FILE = CACHE_DIR / "last_image.sha1"


def _load_dedup_state() -> None:
    """Restore the last seen clipboard image hash from disk so we don't
    re-capture the same image just because the daemon was restarted."""
    try:
        _state["last_image_hash"] = _DEDUP_STATE_FILE.read_bytes()
    except OSError:
        pass  # first run or missing -- fine


def _save_dedup_state(h: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _DEDUP_STATE_FILE.write_bytes(h)
    except OSError:
        pass


def _sweep_orphan_images() -> None:
    """Delete image files in the cache that are not referenced by any
    history or snippet entry. Cleans up files left behind by older
    daemon runs whose entries have since been bumped out of history."""
    if not IMAGES_DIR.is_dir():
        return
    referenced: set[str] = set()
    with _history_lock:
        for e in _history:
            if e.kind == "image" and e.image_path:
                referenced.add(Path(e.image_path).name)
    with _snippets_lock:
        for e in _snippets:
            if e.kind == "image" and e.image_path:
                referenced.add(Path(e.image_path).name)
    removed = 0
    for p in IMAGES_DIR.glob("*.png"):
        if p.name not in referenced:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[clipboard] swept {removed} orphan image(s) from cache")


def _capture_current_clipboard() -> None:
    try:
        targets = _read_clipboard_targets()
        has_image = CAPTURE_IMAGES and any(t.startswith("image/") for t in targets)
        has_text = any(t in ("UTF8_STRING", "TEXT", "STRING", "text/plain",
                              "text/plain;charset=utf-8") for t in targets)
        if has_image:
            data = _read_clipboard_image()
            if data:
                import hashlib
                h = hashlib.sha1(data).digest()
                if h != _state["last_image_hash"]:
                    _state["last_image_hash"] = h
                    _save_dedup_state(h)
                    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    eid = uuid.uuid4().hex[:12]
                    path = IMAGES_DIR / f"{eid}.png"
                    try:
                        path.write_bytes(data)
                        _add_entry(Entry(
                            id=eid, timestamp=time.time(), kind="image",
                            image_path=str(path),
                        ))
                    except OSError as exc:
                        print(f"[clipboard] could not save image: {exc}")
        elif has_text:
            text = _read_clipboard_text()
            if text and text != _state["last_text"] and text.strip():
                _state["last_text"] = text
                _add_entry(Entry(
                    id=uuid.uuid4().hex[:12], timestamp=time.time(),
                    kind="text", text=text,
                ))
    except Exception as exc:  # noqa: BLE001
        print(f"[clipboard] capture error: {exc}")


def _watcher_loop() -> None:
    try:
        from Xlib import display
        from Xlib.ext import xfixes
    except ImportError:
        _poll_only_loop()
        return
    try:
        dpy = display.Display()
        dpy.xfixes_query_version()
        root = dpy.screen().root
        clipboard_atom = dpy.intern_atom("CLIPBOARD")
        dpy.xfixes_select_selection_input(
            root, clipboard_atom, xfixes.XFixesSetSelectionOwnerNotifyMask,
        )
        dpy.flush()
    except Exception as exc:  # noqa: BLE001
        print(f"[clipboard] XFixes unavailable ({exc}); polling")
        _poll_only_loop()
        return

    print("[clipboard] using XFixes selection events (CLIPBOARD)")
    fd = dpy.fileno()
    try:
        _capture_current_clipboard()
        while not _watcher_stop.is_set():
            try:
                readable, _, _ = _select.select([fd], [], [], 5.0)
            except (OSError, ValueError):
                break
            if not readable:
                continue
            had_event = False
            while dpy.pending_events():
                _ = dpy.next_event()
                had_event = True
            if had_event:
                time.sleep(0.05)
                _capture_current_clipboard()
    finally:
        try:
            dpy.close()
        except Exception:
            pass


def _poll_only_loop() -> None:
    while not _watcher_stop.is_set():
        _capture_current_clipboard()
        time.sleep(POLL_INTERVAL)


def _start_watcher_once() -> None:
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    history_ok = _load_list(HISTORY_FILE, _history)
    snippets_ok = _load_list(SNIPPETS_FILE, _snippets)
    _load_dedup_state()       # keep dedup hash across daemon restarts
    # Only sweep when both reference sources loaded cleanly. A failed
    # load returns an empty list, which would mark every cached image
    # as orphan and wipe the cache - silently destroying history that
    # might be recoverable by hand-editing history.json.
    if history_ok and snippets_ok:
        _sweep_orphan_images()
    else:
        print("[clipboard] skipping orphan-image sweep "
              "- a reference file failed to load")
    _watcher_stop.clear()
    _watcher_thread = threading.Thread(
        target=_watcher_loop, daemon=True, name="linuxpop-clipboard",
    )
    _watcher_thread.start()
    _rebuild_trigger_index()
    _maybe_start_trigger_watcher()
    print(f"[clipboard] watcher started (size={HISTORY_SIZE}, "
          f"images={CAPTURE_IMAGES}, snippets={len(_snippets)}, "
          f"triggers={len(_trigger_index)})")


# ----- paste-at-cursor ---------------------------------------------------

def _get_active_window() -> str | None:
    if not shutil.which("xdotool"):
        return None
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=0.5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _paste_to_window(
    entry: Entry,
    target_window: str | None,
    cursor_left: int = 0,
) -> None:
    """Put the entry on the clipboard, focus the target window, send Ctrl+V.

    If cursor_left > 0, send that many Left arrows after the paste so the
    caret lands where a {cursor} placeholder was rendered out.
    """
    if entry.kind == "text":
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=entry.text.encode("utf-8"), check=False,
            timeout=2.0,
        )
    elif entry.kind == "image":
        if not Path(entry.image_path).is_file():
            return
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png",
             "-i", entry.image_path], check=False,
        )

    def worker():
        if target_window and shutil.which("xdotool"):
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", target_window],
                check=False,
            )
            time.sleep(0.1)  # let focus settle
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                check=False,
            )
            if cursor_left > 0:
                # Give the paste a tick to land before we move the caret.
                time.sleep(0.05)
                subprocess.run(
                    ["xdotool", "key", "--clearmodifiers",
                     "--repeat", str(cursor_left), "--delay", "0", "Left"],
                    check=False,
                )

    threading.Thread(target=worker, daemon=True, name="clipboard-paste").start()


# ----- placeholders ------------------------------------------------------

# Recognises:
#   {date} {time} {datetime} {weekday} {clipboard} {cursor} {name}
#   {date:FORMAT}  - strftime; FORMAT runs up to the next "}"
#   {ask:Label}    - label runs up to the next "}"
_PLACEHOLDER_RE = re.compile(
    r"\{("
    r"date(?::[^}]+)?|time|datetime|weekday|clipboard|cursor|name"
    r"|ask:[^}]+"
    r"|shell:[^}]+"
    r")\}"
)


def _render_shell_token(cmd: str) -> str:
    """Run `cmd` through bash, return stdout (trailing newline stripped).
    Gated behind snippet_shell_enabled - when off, returns the literal
    {shell:...} token so the user sees that it didn't run. Timeout 5 s
    so a runaway snippet can't freeze the paste.
    """
    if not bool(_cfg("snippet_shell_enabled", False)):
        return f"{{shell:{cmd}}}"
    cmd = cmd.strip()
    if not cmd:
        return ""
    try:
        out = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=5.0,
        )
        return out.stdout.rstrip("\n")
    except subprocess.TimeoutExpired:
        return f"[shell timeout: {cmd}]"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"[shell error: {exc}]"

# Sentinel for the cursor position. U+E000 is in the BMP private-use area
# and will never appear in real clipboard text; safer than NUL which some
# pipelines drop.
_CURSOR_SENTINEL = "CURSOR"


_DATE_MATH_RE = re.compile(r"^([+-]\d+)([dwmy])(?::(.+))?$")


def _render_date_token(spec: str, now_struct) -> str:
    """Implements {date:...} variants.

    `spec` is what comes after 'date:' in the token. `now_struct` is
    a time.struct_time so callers (and tests) can pin the clock.

    - Pure strftime: `%A`, `%Y-%m-%d %H:%M`, ...
    - Math: `+7d`, `-1w`, `+3m`, `-2y`. m=30 days, y=365 days (good
      enough for everyday writing - calendar-correct month math is a
      surprise gift no one asked for).
    - Math + format: `+7d:%A`.
    """
    m = _DATE_MATH_RE.match(spec)
    if m is None:
        # No leading +/- → treat as strftime against now.
        return time.strftime(spec, now_struct)
    offset_num = int(m.group(1))
    unit = m.group(2)
    fmt = m.group(3) or "%Y-%m-%d"
    import datetime as _dt
    base = _dt.datetime.fromtimestamp(time.mktime(now_struct))
    days = {"d": 1, "w": 7, "m": 30, "y": 365}[unit]
    shifted = base + _dt.timedelta(days=offset_num * days)
    return shifted.strftime(fmt)


def _full_user_name() -> str:
    """Best-effort: full name from /etc/passwd GECOS, falling back to
    $USER, then "User". GECOS is comma-separated; field 0 is the name."""
    try:
        import pwd
        gecos = pwd.getpwuid(os.getuid()).pw_gecos or ""
        first = gecos.split(",", 1)[0].strip()
        if first:
            return first
    except (KeyError, OSError, ImportError):
        pass
    return os.environ.get("USER") or "User"


def render_placeholders(
    text: str,
    ask_callback: Callable[[List[Tuple[str, Optional[List[str]]]]], Optional[dict]],
) -> Tuple[str, int, bool]:
    """Expand placeholder tokens in a snippet body.

    Supported: {date} {time} {datetime} {weekday} {date:...} {name}
    {clipboard} {cursor} {ask:Label} {ask:Label|Opt1|Opt2|...} {shell:CMD}.

    `ask_callback` is called ONCE with a list of (label, options) pairs
    found in the snippet. `options` is None for free-text fields, or a
    list of strings for dropdown fields ({ask:Status|Open|Closed}).
    Callback returns {label: answer} or None on cancel. Multiple
    {ask:Label} for the same Label share one field - three sequential
    prompts collapse into one dialog with three fields.

    Returns (rendered_text, cursor_left_count, cancelled).
      - cursor_left_count is the number of Left arrows to send after paste
        so the caret lands where the first {cursor} token was.
      - cancelled=True if the user dismissed the {ask:} dialog - caller
        should abort the paste.

    {clipboard} is captured BEFORE we mutate the clipboard ourselves.
    Only the first {cursor} is honoured; additional ones are stripped.
    """
    if "{" not in text:
        return text, 0, False

    # Pre-pass: collect every unique {ask:Label} so a single dialog
    # gathers all answers. Order = first appearance for predictability.
    # Each field is (label, options-or-None) where options=None means
    # free-text and a list of strings means dropdown.
    ask_fields: List[Tuple[str, Optional[List[str]]]] = []
    seen_labels: set[str] = set()
    for m in _PLACEHOLDER_RE.finditer(text):
        tok = m.group(1)
        if tok.startswith("ask:"):
            spec = tok[4:]
            if "|" in spec:
                parts = spec.split("|")
                label = parts[0].strip() or "Value"
                options = [p.strip() for p in parts[1:] if p.strip()] or None
            else:
                label = spec.strip() or "Value"
                options = None
            if label not in seen_labels:
                seen_labels.add(label)
                ask_fields.append((label, options))
    ask_answers: Optional[dict] = None
    if ask_fields:
        ask_answers = ask_callback(ask_fields)
        if ask_answers is None:
            return text, 0, True

    clipboard_value: Optional[str] = None
    cancelled = False

    def repl(m: "re.Match[str]") -> str:
        nonlocal clipboard_value, cancelled
        if cancelled:
            return ""
        token = m.group(1)
        now = time.localtime()
        if token == "date":
            return time.strftime("%Y-%m-%d", now)
        if token.startswith("date:"):
            # Three shapes:
            #   {date:%A}        - strftime now
            #   {date:+7d}       - now + 7d, default %Y-%m-%d
            #   {date:+7d:%A}    - now + 7d, formatted
            # Math units: d (day), w (week), m (≈30 days), y (≈365 days).
            spec = token[5:]
            try:
                return _render_date_token(spec, now)
            except (ValueError, TypeError):
                return m.group(0)
        if token == "time":
            return time.strftime("%H:%M", now)
        if token == "datetime":
            return time.strftime("%Y-%m-%d %H:%M", now)
        if token == "weekday":
            # %A is locale-aware: "Tirsdag" on nb_NO, "Tuesday" on en_US.
            return time.strftime("%A", now)
        if token == "name":
            return _full_user_name()
        if token == "clipboard":
            if clipboard_value is None:
                clipboard_value = _read_clipboard_text() or ""
            return clipboard_value
        if token == "cursor":
            return _CURSOR_SENTINEL
        if token.startswith("ask:"):
            spec = token[4:]
            # Strip off |options to get just the label key used in the
            # answers dict.
            label = spec.split("|", 1)[0].strip() or "Value"
            if ask_answers is not None and label in ask_answers:
                return ask_answers[label]
            return ""
        if token.startswith("shell:"):
            return _render_shell_token(token[6:])
        return m.group(0)

    rendered = _PLACEHOLDER_RE.sub(repl, text)
    if cancelled:
        return text, 0, True

    cursor_left = 0
    if _CURSOR_SENTINEL in rendered:
        idx = rendered.find(_CURSOR_SENTINEL)
        before = rendered[:idx].replace(_CURSOR_SENTINEL, "")
        after = rendered[idx + len(_CURSOR_SENTINEL):].replace(_CURSOR_SENTINEL, "")
        rendered = before + after
        cursor_left = len(after)
    return rendered, cursor_left, False


# ----- picker dialog -----------------------------------------------------

class _PickerDialog:
    def __init__(self) -> None:
        self.dialog: Optional[Gtk.Window] = None
        self.target_window: str | None = None
        self.search_entry: Optional[Gtk.SearchEntry] = None
        self.notebook: Optional[Gtk.Notebook] = None
        self.recent_listbox: Optional[Gtk.ListBox] = None
        self.snippets_listbox: Optional[Gtk.ListBox] = None
        self._filter_text = ""
        # Set while a transient child (rename/pin name prompt) is up so
        # focus-out on the picker doesn't blow away the parent the prompt
        # is anchored to. Cleared when the prompt is dismissed.
        self._modal_child_open = False

    def show(self, target_window: str | None = None) -> None:
        # Stamp each phase via the real logger (not print) so a UI freeze
        # leaves breadcrumbs in ~/.cache/linuxpop/linuxpop.log. Times
        # > 100 ms at any phase would explain a perceived freeze.
        import logging
        _log = logging.getLogger("linuxpop")
        t0 = time.monotonic()
        def _stamp(label):
            _log.info("[clipboard.show] %s: %.0f ms",
                      label, (time.monotonic()-t0)*1000)
        _stamp("entered show()")

        if self.dialog is not None and self.dialog.get_visible():
            _force_to_front(self.dialog)
            _stamp("reused existing")
            return
        self.target_window = target_window

        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("LinuxPop - Clipboard & Snippets")
        win.set_default_size(580, 540)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_skip_taskbar_hint(True)
        win.set_skip_pager_hint(True)
        win.set_keep_above(True)
        # Borderless, ephemeral picker - no title bar, no min/max/close
        # decorations. Dismissed by Esc, click-outside, or focus-out.
        win.set_decorated(False)
        win.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        win.set_resizable(False)
        win.connect("destroy", self._on_destroy)
        win.connect("key-press-event", self._on_key_press)
        win.connect("focus-out-event", self._on_focus_out)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)

        # Search
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type to filter…")
        self.search_entry.connect("search-changed", self._on_search_changed)
        outer.pack_start(self.search_entry, False, False, 0)

        # Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_margin_top(6)

        # Recent tab
        recent_scroll = Gtk.ScrolledWindow()
        recent_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.recent_listbox = Gtk.ListBox()
        self.recent_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.recent_listbox.set_filter_func(self._row_filter)
        self.recent_listbox.connect("row-activated", self._on_row_activated)
        recent_scroll.add(self.recent_listbox)
        self.notebook.append_page(recent_scroll, Gtk.Label(label="Recent"))

        # Snippets tab - listbox plus a "+ New snippet" button under it.
        snippets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        snippets_scroll = Gtk.ScrolledWindow()
        snippets_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.snippets_listbox = Gtk.ListBox()
        self.snippets_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.snippets_listbox.set_filter_func(self._row_filter)
        self.snippets_listbox.connect("row-activated", self._on_row_activated)
        snippets_scroll.add(self.snippets_listbox)
        snippets_box.pack_start(snippets_scroll, True, True, 0)
        new_snippet_btn = Gtk.Button(label="＋ New snippet…")
        new_snippet_btn.set_halign(Gtk.Align.START)
        new_snippet_btn.set_margin_top(4)
        new_snippet_btn.connect("clicked", self._on_new_snippet_clicked)
        snippets_box.pack_start(new_snippet_btn, False, False, 0)
        self.notebook.append_page(snippets_box, Gtk.Label(label="Snippets"))

        outer.pack_start(self.notebook, True, True, 0)

        # Hint
        hint = Gtk.Label(xalign=0, margin_top=6)
        hint.set_markup(
            "<small>Enter to paste · Ctrl+P pin · Ctrl+R rename · "
            "Snippets support <tt>{date} {time} {datetime} {clipboard} "
            "{cursor} {ask:Label}</tt> · Esc to close</small>"
        )
        hint.set_line_wrap(True)
        outer.pack_start(hint, False, False, 0)

        win.add(outer)
        _stamp("built widgets")
        self._populate_recent()
        _stamp("populated recent")
        self._populate_snippets()
        _stamp("populated snippets")
        win.show_all()
        _stamp("show_all")
        _force_to_front(win)
        _stamp("force_to_front")
        # Defer search-entry focus until AFTER the WM has had a chance to
        # process our focus request - calling grab_focus immediately after
        # show_all races the FocusIn event and silently fails.
        if self.search_entry is not None:
            GLib.idle_add(self._grab_search_focus)
        self.dialog = win

    def _grab_search_focus(self) -> bool:
        if self.search_entry is not None and self.dialog is not None:
            self.search_entry.grab_focus()
        return False  # one-shot

    def _on_focus_out(self, _widget, _event) -> bool:
        # Don't close while a rename / pin prompt is showing - that
        # dialog is transient_for=self.dialog and tearing it down would
        # orphan the prompt.
        if self._modal_child_open:
            return False
        # IBus/Cinnamon occasionally bounce focus away from the picker
        # for a single frame (input-method window opens, system tray
        # ping, etc.) and immediately give it back. Destroying on the
        # first focus-out makes the picker vanish mid-search. Defer the
        # check: if focus has actually moved elsewhere 120 ms from now,
        # close. Otherwise the picker is still focused - keep it.
        if self.dialog is None:
            return False
        def _confirm_focus_lost() -> bool:
            if self.dialog is None:
                return False
            if self._modal_child_open:
                return False
            try:
                still_active = self.dialog.is_active()
            except Exception:
                still_active = False
            if not still_active:
                self.dialog.destroy()
            return False  # one-shot
        GLib.timeout_add(120, _confirm_focus_lost)
        return False

    def _on_destroy(self, *_):
        import logging
        logging.getLogger("linuxpop").info(
            "[clipboard.show] window destroyed -- state cleared")
        self.dialog = None
        self.search_entry = None
        self.notebook = None
        self.recent_listbox = None
        self.snippets_listbox = None
        self._filter_text = ""

    # ---- population ----

    def _populate_recent(self) -> None:
        if self.recent_listbox is None:
            return
        for child in list(self.recent_listbox.get_children()):
            self.recent_listbox.remove(child)
        with _history_lock:
            entries = list(_history)
        if not entries:
            self.recent_listbox.add(self._empty_row("History is empty",
                                                   "Copy something - it appears here."))
        else:
            for entry in entries:
                self.recent_listbox.add(self._make_row(entry, is_snippet=False))
        self.recent_listbox.show_all()

    def _populate_snippets(self) -> None:
        if self.snippets_listbox is None:
            return
        for child in list(self.snippets_listbox.get_children()):
            self.snippets_listbox.remove(child)
        with _snippets_lock:
            entries = list(_snippets)
        if not entries:
            self.snippets_listbox.add(self._empty_row(
                "No snippets yet",
                "Pin (★) an entry from Recent to save it here permanently."
            ))
        else:
            for entry in entries:
                self.snippets_listbox.add(self._make_row(entry, is_snippet=True))
        self.snippets_listbox.show_all()

    def _empty_row(self, title: str, subtitle: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        t = Gtk.Label(xalign=0.5)
        t.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        s = Gtk.Label(label=subtitle, xalign=0.5, wrap=True)
        s.get_style_context().add_class("dim-label")
        box.pack_start(t, False, False, 0)
        box.pack_start(s, False, False, 0)
        row.add(box)
        return row

    def _make_row(self, entry: Entry, is_snippet: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.entry = entry  # type: ignore[attr-defined]
        row.is_snippet = is_snippet  # type: ignore[attr-defined]

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin=6)

        # Thumbnail / icon
        if entry.kind == "image" and Path(entry.image_path).is_file():
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    entry.image_path, 48, 48, True,
                )
                img = Gtk.Image.new_from_pixbuf(pix)
            except Exception:
                img = Gtk.Image.new_from_icon_name(
                    "image-x-generic-symbolic", Gtk.IconSize.LARGE_TOOLBAR,
                )
        else:
            icon_name = "starred-symbolic" if is_snippet else "edit-paste-symbolic"
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        hbox.pack_start(img, False, False, 0)

        # Text
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        title_markup = f"<b>{GLib.markup_escape_text(entry.preview())}</b>"
        if is_snippet and entry.trigger:
            triggers_display = ", ".join(entry.trigger_list())
            title_markup += (
                f"  <small><span foreground='#5B7DF5'>"
                f"⇥ {GLib.markup_escape_text(triggers_display)}</span></small>"
            )
        title.set_markup(title_markup)
        title.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        title.set_max_width_chars(60)
        vbox.pack_start(title, False, False, 0)

        if is_snippet and entry.name:
            # Show a preview of contents under the name
            preview = entry.text[:80] if entry.kind == "text" else entry.image_path
            sub = Gtk.Label(xalign=0)
            sub.set_markup(f"<small><span foreground='#888'>"
                            f"{GLib.markup_escape_text(preview)}</span></small>")
            sub.set_ellipsize(3)
            sub.set_max_width_chars(70)
            vbox.pack_start(sub, False, False, 0)
        else:
            meta = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            if entry.kind == "text":
                meta += f"  ·  {len(entry.text)} chars"
            sub = Gtk.Label(xalign=0)
            sub.set_markup(f"<small><span foreground='#888'>{meta}</span></small>")
            vbox.pack_start(sub, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)

        # Action buttons
        if is_snippet:
            rename_btn = Gtk.Button.new_from_icon_name(
                "document-edit-symbolic", Gtk.IconSize.BUTTON,
            )
            rename_btn.set_valign(Gtk.Align.CENTER)
            rename_btn.set_tooltip_text("Rename")
            rename_btn.connect("clicked", self._on_rename_clicked, entry)
            hbox.pack_end(rename_btn, False, False, 0)

            unpin_btn = Gtk.Button.new_from_icon_name(
                "starred-symbolic", Gtk.IconSize.BUTTON,
            )
            unpin_btn.set_valign(Gtk.Align.CENTER)
            unpin_btn.set_tooltip_text("Unpin")
            unpin_btn.connect("clicked", self._on_unpin_clicked, entry)
            hbox.pack_end(unpin_btn, False, False, 0)
        else:
            pin_btn = Gtk.Button.new_from_icon_name(
                "non-starred-symbolic", Gtk.IconSize.BUTTON,
            )
            pin_btn.set_valign(Gtk.Align.CENTER)
            pin_btn.set_tooltip_text("Pin as snippet (also lets you name it)")
            pin_btn.connect("clicked", self._on_pin_clicked, entry)
            hbox.pack_end(pin_btn, False, False, 0)

        row.add(hbox)
        return row

    # ---- filter ----

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter_text = entry.get_text().strip().lower()
        if self.recent_listbox:
            self.recent_listbox.invalidate_filter()
        if self.snippets_listbox:
            self.snippets_listbox.invalidate_filter()

    def _row_filter(self, row: Gtk.ListBoxRow) -> bool:
        if not self._filter_text:
            return True
        entry = getattr(row, "entry", None)
        if entry is None:
            return False
        return self._filter_text in entry.search_haystack()

    # ---- actions ----

    def _on_row_activated(self, _listbox, row) -> None:
        entry = getattr(row, "entry", None)
        is_snippet = bool(getattr(row, "is_snippet", False))
        if entry is not None:
            self._paste_and_close(entry, is_snippet=is_snippet)

    def _paste_and_close(self, entry: Entry, is_snippet: bool = False) -> None:
        target = self.target_window  # captured before we showed
        cursor_left = 0
        # Only snippets get placeholder substitution. Recent-history entries
        # are pasted verbatim - a "{date}" the user copied from somewhere
        # else should stay literal.
        if is_snippet and entry.kind == "text" and "{" in entry.text:
            rendered, cursor_left, cancelled = render_placeholders(
                entry.text, self._ask_placeholder_values,
            )
            if cancelled:
                # Keep the picker open so the user can try again.
                return
            if rendered != entry.text or cursor_left:
                entry = Entry(
                    id=entry.id, timestamp=entry.timestamp, kind=entry.kind,
                    text=rendered, image_path=entry.image_path, name=entry.name,
                )
        if self.dialog is not None:
            self.dialog.destroy()
        _paste_to_window(entry, target, cursor_left=cursor_left)

    def _ask_placeholder_values(
        self, fields: List[Tuple[str, Optional[List[str]]]],
    ) -> Optional[dict]:
        """Bridge for render_placeholders → blocking Gtk form dialog
        with one widget per unique {ask:Label}. `fields` is a list of
        (label, options) pairs: options=None means free-text Entry,
        a list of strings becomes a ComboBoxText dropdown. Returns
        {label: value} on OK, None on Cancel."""
        if not fields:
            return {}
        self._modal_child_open = True
        try:
            title = "Snippet" if len(fields) > 1 else fields[0][0]
            dlg = Gtk.Dialog(title=title, transient_for=self.dialog, flags=0)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                            "OK", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.OK)
            content = dlg.get_content_area()
            content.set_spacing(6)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(12)
            content.set_margin_end(12)
            widgets: dict = {}
            for i, (label, options) in enumerate(fields):
                lbl = Gtk.Label(xalign=0)
                lbl.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
                content.add(lbl)
                if options:
                    combo = Gtk.ComboBoxText()
                    for opt in options:
                        combo.append_text(opt)
                    combo.set_active(0)
                    content.add(combo)
                    widgets[label] = combo
                else:
                    entry = Gtk.Entry()
                    entry.set_activates_default(True)
                    entry.set_width_chars(40)
                    content.add(entry)
                    widgets[label] = entry
                    if i == 0:
                        entry.set_property("has-focus", True)
            dlg.show_all()
            response = dlg.run()
            answers = {}
            for label, w in widgets.items():
                if isinstance(w, Gtk.ComboBoxText):
                    answers[label] = w.get_active_text() or ""
                else:
                    answers[label] = w.get_text()
            dlg.destroy()
            if response != Gtk.ResponseType.OK:
                return None
            return answers
        finally:
            self._modal_child_open = False

    def _on_pin_clicked(self, _btn, entry: Entry) -> None:
        result = self._ask_edit_snippet_meta(
            default_name=entry.preview(40), default_trigger="",
        )
        if result is None:
            return  # cancelled
        name, trigger = result
        _pin_entry(entry, name)
        # _pin_entry inserts at index 0 - find that newest snippet and
        # write the trigger through. Avoids reaching into internals.
        if trigger:
            with _snippets_lock:
                if _snippets:
                    new_id = _snippets[0].id
                else:
                    new_id = None
            if new_id is not None:
                _set_snippet_trigger(new_id, trigger)
        self._populate_snippets()
        if self.notebook is not None:
            self.notebook.set_current_page(1)

    def _on_unpin_clicked(self, _btn, entry: Entry) -> None:
        _unpin_snippet(entry.id)
        self._populate_snippets()

    def _on_rename_clicked(self, _btn, entry: Entry) -> None:
        result = self._ask_edit_snippet_meta(
            default_name=entry.name or entry.preview(40),
            default_trigger=entry.trigger,
        )
        if result is None:
            return
        new_name, new_trigger = result
        _rename_snippet(entry.id, new_name)
        _set_snippet_trigger(entry.id, new_trigger)
        self._populate_snippets()

    def _show_snippet_help_dialog(self, parent: Gtk.Window) -> None:
        """A friendly walkthrough of what snippets are, how triggers work,
        and what every placeholder does. Intentionally written for the
        person who is NOT a programmer - heavy on examples, light on jargon."""
        self._modal_child_open = True
        try:
            dlg = Gtk.Dialog(
                title="Snippet guide", transient_for=parent, flags=0,
            )
            dlg.set_default_size(620, 580)
            dlg.add_button("Close", Gtk.ResponseType.CLOSE)

            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_hexpand(True)
            scroll.set_vexpand(True)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                             margin_top=16, margin_bottom=16,
                             margin_start=22, margin_end=22)

            # Hero
            hero = Gtk.Label(xalign=0)
            hero.set_markup(
                "<span size='xx-large' weight='bold'>Snippets, in plain words</span>")
            outer.pack_start(hero, False, False, 0)

            intro = Gtk.Label(xalign=0)
            intro.set_line_wrap(True)
            intro.set_markup(
                "<span foreground='#b8c0d4'>A <b>snippet</b> is a "
                "piece of text you save once and reuse forever. Your "
                "email signature, a phone number, a reply to a "
                "frequently asked question, a bug-report template - "
                "anything you find yourself typing more than twice.</span>")
            outer.pack_start(intro, False, False, 0)

            sep1 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            outer.pack_start(sep1, False, False, 4)

            # Three ways
            ways_head = Gtk.Label(xalign=0)
            ways_head.set_markup(
                "<span size='large' weight='bold'>Three ways to use one</span>")
            outer.pack_start(ways_head, False, False, 0)

            for n, head, body in [
                ("1.", "Pick it from the list",
                 "Open the clipboard picker (your shortcut, usually "
                 "<tt>Ctrl+Super+V</tt>), go to the <b>Snippets</b> tab, "
                 "click the one you want. It pastes wherever your cursor is."),
                ("2.", "Type a trigger",
                 "If you give a snippet a shortcut like <tt>;email</tt>, "
                 "you can type <tt>;email</tt> followed by a space "
                 "anywhere on your computer and LinuxPop replaces it "
                 "with the full text. One snippet can have many "
                 "triggers - write them comma-separated: "
                 "<tt>rraak, rb, email</tt>. Triggers must be turned "
                 "on in Settings → Hotkeys."),
                ("3.", "Let it fill in the blanks",
                 "A snippet can contain little tags like <tt>{date}</tt> "
                 "or <tt>{ask:Name}</tt>. They're called <b>placeholders</b> "
                 "and they get filled in for you when you paste - "
                 "today's date, your clipboard, an answer to a question, etc."),
            ]:
                row = Gtk.Box(
                    orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                num = Gtk.Label(xalign=0, yalign=0)
                num.set_markup(
                    f"<span foreground='#5B7DF5' weight='bold' "
                    f"size='large'>{n}</span>")
                row.pack_start(num, False, False, 0)
                text = Gtk.Label(xalign=0, yalign=0)
                text.set_markup(
                    f"<b>{head}</b>\n<span foreground='#b8c0d4'>"
                    f"{body}</span>")
                text.set_line_wrap(True)
                text.set_hexpand(True)
                row.pack_start(text, True, True, 0)
                outer.pack_start(row, False, False, 0)

            sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            outer.pack_start(sep2, False, False, 4)

            # Placeholders table
            ph_head = Gtk.Label(xalign=0)
            ph_head.set_markup(
                "<span size='large' weight='bold'>Placeholders you can use</span>")
            outer.pack_start(ph_head, False, False, 0)

            grid = Gtk.Grid(column_spacing=14, row_spacing=8)
            grid.set_margin_top(4)
            ph_rows = [
                ("{date}",
                 "Today's date - e.g. 2026-05-27."),
                ("{time}",
                 "Current time - e.g. 14:30."),
                ("{datetime}",
                 "Both at once - 2026-05-27 14:30."),
                ("{weekday}",
                 "Name of the day, in your system language - e.g. Wednesday."),
                ("{date:FORMAT}",
                 "Custom date format. <tt>%A</tt>=weekday, <tt>%d</tt>=day, "
                 "<tt>%B</tt>=month name, <tt>%Y</tt>=year, <tt>%V</tt>=week number. "
                 "Example: <tt>{date:%A %d %B}</tt> → Wednesday 27 May."),
                ("{date:+7d}",
                 "Date math: shift by days (d), weeks (w), months "
                 "(m≈30 days), years (y). <tt>+7d</tt> = a week from now. "
                 "Combine with format: <tt>{date:+7d:%A}</tt>."),
                ("{name}",
                 "Your full name, taken from your user account."),
                ("{clipboard}",
                 "Whatever's currently on your clipboard at paste time."),
                ("{cursor}",
                 "Marks where the typing cursor should land after paste. "
                 "Useful for templates like <tt>[ ] {cursor}</tt>."),
                ("{ask:Label}",
                 "Pops up a small dialog and asks for a value when you "
                 "paste. Rename <tt>Label</tt> to whatever the prompt "
                 "should say. Use the same Label twice and it'll only "
                 "ask once."),
                ("{ask:Label|A|B|C}",
                 "Same idea but with a dropdown of choices. "
                 "<tt>{ask:Status|Open|Closed|WIP}</tt> gives you a picker."),
                ("{shell:CMD}",
                 "Runs a shell command and pastes its output. Off by "
                 "default for safety - turn on in Settings if you want it. "
                 "Example: <tt>{shell:date -u}</tt> for UTC time."),
            ]
            for i, (tag, desc) in enumerate(ph_rows):
                tag_lbl = Gtk.Label(xalign=0, yalign=0)
                tag_lbl.set_markup(
                    f"<tt><span foreground='#7C3AED'>"
                    f"{GLib.markup_escape_text(tag)}</span></tt>")
                tag_lbl.set_selectable(True)
                grid.attach(tag_lbl, 0, i, 1, 1)
                desc_lbl = Gtk.Label(xalign=0, yalign=0)
                desc_lbl.set_markup(
                    f"<span foreground='#d8dce8'>{desc}</span>")
                desc_lbl.set_line_wrap(True)
                desc_lbl.set_hexpand(True)
                grid.attach(desc_lbl, 1, i, 1, 1)
            outer.pack_start(grid, False, False, 0)

            sep3 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            outer.pack_start(sep3, False, False, 4)

            # Recipes
            rec_head = Gtk.Label(xalign=0)
            rec_head.set_markup(
                "<span size='large' weight='bold'>A few real-world recipes</span>")
            outer.pack_start(rec_head, False, False, 0)

            for title, body_text, note in [
                ("Email signature with today's date",
                 "Best,\n{name}\n{date}",
                 "Trigger: <tt>;sig</tt>"),
                ("Support reply that asks for the details",
                 "Hi {ask:Customer},\n\n"
                 "Thanks for reaching out about {ask:Topic}.\n"
                 "Current status: {ask:Status|New|In progress|Resolved}.\n\n"
                 "Best,\n{name}",
                 "One dialog asks for all three at once."),
                ("Meeting one week out",
                 "Meeting on {date:+7d:%A %d %B}",
                 "Renders e.g. <i>Meeting on Wednesday 03 June</i>."),
                ("Empty checkbox where the cursor lands after",
                 "[ ] {cursor}",
                 "Paste, then start typing the to-do - the cursor is already in place."),
            ]:
                head = Gtk.Label(xalign=0)
                head.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
                outer.pack_start(head, False, False, 0)
                box = Gtk.Box(
                    orientation=Gtk.Orientation.VERTICAL, spacing=2)
                box.set_margin_start(8)
                code = Gtk.Label(xalign=0, selectable=True)
                code.set_markup(
                    f"<tt><span foreground='#5B7DF5' background='#181d2a'> "
                    f"{GLib.markup_escape_text(body_text)} </span></tt>")
                code.set_line_wrap(True)
                box.pack_start(code, False, False, 0)
                note_lbl = Gtk.Label(xalign=0)
                note_lbl.set_markup(
                    f"<small><span foreground='#b8c0d4'>{note}</span></small>")
                note_lbl.set_line_wrap(True)
                box.pack_start(note_lbl, False, False, 0)
                outer.pack_start(box, False, False, 0)

            sep4 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            outer.pack_start(sep4, False, False, 4)

            # Case-tip
            case_head = Gtk.Label(xalign=0)
            case_head.set_markup(
                "<span size='large' weight='bold'>One small case-trick</span>")
            outer.pack_start(case_head, False, False, 0)
            case_body = Gtk.Label(xalign=0)
            case_body.set_line_wrap(True)
            case_body.set_markup(
                "<span foreground='#d8dce8'>"
                "Triggers don't care about capital letters when they "
                "match, but they DO copy your capitalisation to the "
                "output. If your trigger is <tt>rraak</tt> and the "
                "snippet is <tt>raakanin@gmail.com</tt>:"
                "</span>\n\n"
                "  <tt>rraak </tt> → raakanin@gmail.com\n"
                "  <tt>Rraak </tt> → Raakanin@gmail.com\n"
                "  <tt>RRAAK </tt> → RAAKANIN@GMAIL.COM"
            )
            outer.pack_start(case_body, False, False, 0)

            scroll.add(outer)
            dlg.get_content_area().pack_start(scroll, True, True, 0)
            dlg.show_all()
            dlg.run()
            dlg.destroy()
        finally:
            self._modal_child_open = False

    def _ask_edit_snippet_meta(
        self, default_name: str = "", default_trigger: str = "",
    ) -> Optional[Tuple[str, str]]:
        """Edit name + trigger for an existing snippet."""
        self._modal_child_open = True
        try:
            dlg = Gtk.Dialog(title="Edit snippet",
                             transient_for=self.dialog, flags=0)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                            "Save", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.OK)
            content = dlg.get_content_area()
            content.set_spacing(4)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(12)
            content.set_margin_end(12)

            name_lbl = Gtk.Label(xalign=0)
            name_lbl.set_markup("<b>Name</b>")
            content.add(name_lbl)
            name_entry = Gtk.Entry()
            name_entry.set_text(default_name)
            name_entry.set_activates_default(True)
            name_entry.set_width_chars(36)
            content.add(name_entry)

            trig_lbl = Gtk.Label(xalign=0, margin_top=6)
            trig_lbl.set_markup(
                "<b>Trigger(s)</b>  <small>(optional, comma-separated)</small>"
            )
            content.add(trig_lbl)
            trig_entry = Gtk.Entry()
            trig_entry.set_text(default_trigger)
            trig_entry.set_activates_default(True)
            triggers_on = bool(_cfg("snippet_triggers_enabled", False))
            if triggers_on:
                trig_entry.set_placeholder_text(
                    "e.g. rraak, rb - any of them auto-expands"
                )
            else:
                trig_entry.set_placeholder_text(
                    "Enable 'Snippet triggers' in Settings to use"
                )
            content.add(trig_entry)

            # Small "Snippet guide" button so help is reachable from the
            # rename/edit dialog too, not just New snippet.
            guide_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, margin_top=10)
            spacer = Gtk.Label()
            guide_row.pack_start(spacer, True, True, 0)
            guide_btn = Gtk.Button(label="📖 Snippet guide")
            guide_btn.connect(
                "clicked", lambda _b: self._show_snippet_help_dialog(parent=dlg))
            guide_row.pack_start(guide_btn, False, False, 0)
            content.add(guide_row)

            dlg.show_all()
            name_entry.grab_focus()
            response = dlg.run()
            name = name_entry.get_text().strip()
            trig = trig_entry.get_text().strip()
            dlg.destroy()
            if response != Gtk.ResponseType.OK:
                return None
            return (name, trig)
        finally:
            self._modal_child_open = False

    def _on_new_snippet_clicked(self, _btn) -> None:
        result = self._ask_new_snippet()
        if result is None:
            return
        name, body, trigger = result
        if not body.strip():
            return
        _create_snippet(name=name, text=body, trigger=trigger)
        self._populate_snippets()
        if self.notebook is not None:
            self.notebook.set_current_page(1)

    def _ask_new_snippet(self) -> Optional[Tuple[str, str, str]]:
        """Modal dialog: name (Entry) + body (TextView) + trigger (Entry).
        Returns (name, body, trigger) on OK, None on Cancel."""
        self._modal_child_open = True
        try:
            dlg = Gtk.Dialog(title="New snippet",
                             transient_for=self.dialog, flags=0)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                            "Save", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.OK)
            dlg.set_default_size(480, 360)

            content = dlg.get_content_area()
            content.set_spacing(6)
            content.set_margin_top(8)
            content.set_margin_bottom(8)
            content.set_margin_start(12)
            content.set_margin_end(12)

            name_label = Gtk.Label(xalign=0)
            name_label.set_markup("<b>Name</b>")
            content.add(name_label)
            name_entry = Gtk.Entry()
            name_entry.set_placeholder_text("Short label (optional)")
            content.add(name_entry)

            triggers_on = bool(_cfg("snippet_triggers_enabled", False))
            trigger_label = Gtk.Label(xalign=0, margin_top=6)
            trigger_label.set_markup(
                "<b>Trigger(s)</b>  <small>(optional, comma-separated)</small>"
            )
            content.add(trigger_label)
            trigger_entry = Gtk.Entry()
            if triggers_on:
                trigger_entry.set_placeholder_text(
                    "e.g. rraak, rb - any of them + space/tab auto-expands"
                )
            else:
                trigger_entry.set_placeholder_text(
                    "Set, then enable 'Snippet triggers' in Settings to auto-expand"
                )
            content.add(trigger_entry)

            body_label = Gtk.Label(xalign=0, margin_top=6)
            body_label.set_markup(
                "<b>Text</b>  <small>(this is the snippet body - "
                "type or paste what you want to expand to)</small>"
            )
            content.add(body_label)
            body_scroll = Gtk.ScrolledWindow()
            body_scroll.set_policy(Gtk.PolicyType.AUTOMATIC,
                                   Gtk.PolicyType.AUTOMATIC)
            body_scroll.set_min_content_height(180)
            # IN shadow draws the actual visible border around the scroll
            # area. Without this, no amount of CSS background on the inner
            # TextView reads as a "real" input field because the field
            # has no frame around it.
            body_scroll.set_shadow_type(Gtk.ShadowType.IN)
            body_view = Gtk.TextView()
            body_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            body_view.set_accepts_tab(False)
            # Reuse the same class the terminal-confirm dialog's editable
            # state uses - it gives the standard Entry-field look
            # (lighter background, padding, blue caret).
            body_view.get_style_context().add_class("lp-cmd-edit")
            body_scroll.add(body_view)
            content.pack_start(body_scroll, True, True, 0)

            help_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                spacing=8, margin_top=6)
            help_intro = Gtk.Label(xalign=0)
            help_intro.set_markup(
                "<small>Click to insert a placeholder (filled in at paste time):</small>"
            )
            help_row.pack_start(help_intro, True, True, 0)
            help_btn = Gtk.Button(label="📖 Snippet guide")
            help_btn.set_tooltip_text(
                "Open a friendly walkthrough of snippets, triggers, and placeholders."
            )
            help_btn.connect(
                "clicked", lambda _b: self._show_snippet_help_dialog(parent=dlg))
            help_row.pack_start(help_btn, False, False, 0)
            content.add(help_row)

            chips = Gtk.FlowBox()
            chips.set_selection_mode(Gtk.SelectionMode.NONE)
            chips.set_max_children_per_line(6)
            chips.set_row_spacing(4)
            chips.set_column_spacing(4)

            def insert_token(token: str, select_offset: int = 0,
                             select_len: int = 0) -> None:
                buf = body_view.get_buffer()
                if buf.get_has_selection():
                    buf.delete_selection(False, True)
                start_offset = buf.get_iter_at_mark(buf.get_insert()).get_offset()
                buf.insert_at_cursor(token)
                if select_len > 0:
                    sel_start = buf.get_iter_at_offset(start_offset + select_offset)
                    sel_end = buf.get_iter_at_offset(
                        start_offset + select_offset + select_len)
                    buf.select_range(sel_start, sel_end)
                body_view.grab_focus()

            # (label, token, tooltip, select_offset_in_token, select_len)
            # select_* are non-zero for {ask:Label} / {date:FORMAT} so
            # the editable part is auto-selected and the user can just
            # type to overwrite it.
            chip_specs = [
                ("{date}",        "{date}",        "Current date (YYYY-MM-DD)", 0, 0),
                ("{time}",        "{time}",        "Current time (HH:MM)",      0, 0),
                ("{datetime}",    "{datetime}",    "Date + time together",      0, 0),
                ("{weekday}",     "{weekday}",     "Name of the day - follows your system language", 0, 0),
                ("{date:FORMAT}", "{date:%A %d %B}", "Custom date format. Codes: %A=weekday, %d=day, %B=month name, %V=week number, %Y=year. Edit the highlighted part to change.", 6, 8),
                ("{date:+7d}",    "{date:+7d}",    "Date math: shift by days (d) / weeks (w) / months (m≈30d) / y. Edit the number to change. Combine with format like {date:+7d:%A}.", 6, 3),
                ("{name}",        "{name}",        "Your full name (from your user account)", 0, 0),
                ("{clipboard}",   "{clipboard}",   "Current clipboard contents", 0, 0),
                ("{cursor}",      "{cursor}",      "Where the caret lands after paste", 0, 0),
                ("{ask:Label}",   "{ask:Label}",   "Prompt for a value at paste time. Multiple {ask:} fields show in one dialog. Rename 'Label' to what you want the prompt to say.", 5, 5),
                ("{ask:Label|Opt}", "{ask:Status|Open|Closed|WIP}", "Dropdown variant: pipe-separated options after the label give a chooser instead of free text. Edit the label and options to match your case.", 5, 6),
                ("{shell:CMD}",   "{shell:date -u}", "Run a shell command and paste its output. Requires 'Shell expansion' enabled in Settings - disabled by default for safety.", 7, 8),
            ]
            for label, token, tip, sel_off, sel_len in chip_specs:
                btn = Gtk.Button(label=label)
                btn.set_tooltip_text(tip)
                # Monospace makes the brace syntax read like code.
                child = btn.get_child()
                if isinstance(child, Gtk.Label):
                    child.set_use_markup(False)
                    attrs = child.get_attributes()
                    child.get_style_context().add_class("monospace")
                btn.connect(
                    "clicked",
                    lambda _b, t=token, so=sel_off, sl=sel_len:
                        insert_token(t, so, sl),
                )
                chips.add(btn)
            content.add(chips)

            dlg.show_all()
            name_entry.grab_focus()
            response = dlg.run()
            name = name_entry.get_text().strip()
            trigger = trigger_entry.get_text().strip()
            buf = body_view.get_buffer()
            body = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            dlg.destroy()
            if response != Gtk.ResponseType.OK:
                return None
            return (name, body, trigger)
        finally:
            self._modal_child_open = False

    def _ask_for_name(self, default: str = "") -> str | None:
        self._modal_child_open = True
        try:
            dlg = Gtk.Dialog(title="Snippet name", transient_for=self.dialog, flags=0)
            dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                            "OK", Gtk.ResponseType.OK)
            dlg.set_default_response(Gtk.ResponseType.OK)
            entry = Gtk.Entry()
            entry.set_text(default)
            entry.set_activates_default(True)
            entry.set_margin_top(8)
            entry.set_margin_bottom(8)
            entry.set_margin_start(12)
            entry.set_margin_end(12)
            dlg.get_content_area().add(entry)
            dlg.show_all()
            response = dlg.run()
            value = entry.get_text().strip()
            dlg.destroy()
            if response != Gtk.ResponseType.OK:
                return None
            return value
        finally:
            self._modal_child_open = False

    # ---- keyboard ----

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.dialog is not None:
                self.dialog.destroy()
            return True
        # Ctrl+P → pin currently selected (if in Recent)
        if (event.state & Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_p):
            self._action_on_selected("pin")
            return True
        # Ctrl+R → rename (if in Snippets)
        if (event.state & Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_r):
            self._action_on_selected("rename")
            return True
        return False

    def _action_on_selected(self, action: str) -> None:
        if self.notebook is None:
            return
        page = self.notebook.get_current_page()
        listbox = self.recent_listbox if page == 0 else self.snippets_listbox
        if listbox is None:
            return
        row = listbox.get_selected_row()
        if row is None:
            return
        entry = getattr(row, "entry", None)
        if entry is None:
            return
        if action == "pin" and page == 0:
            self._on_pin_clicked(None, entry)
        elif action == "rename" and page == 1:
            self._on_rename_clicked(None, entry)


_picker = _PickerDialog()


def open_picker(target_window: str | None = None) -> None:
    """Module-level entry point used by both the popup plugin button and
    the global clipboard hotkey in main.py."""
    GLib.idle_add(_picker.show, target_window)


# ----- plugin registration ----------------------------------------------

def _open_from_popup(_text: str) -> None:
    # When invoked from the popup, capture the previously focused window
    # before opening the picker so paste-at-cursor still works.
    target = _get_active_window()
    open_picker(target)


def register(register_plugin) -> None:
    # Honour the master kill-switch so the user can turn the entire
    # clipboard plugin off (no watcher thread, no hotkey, no popup
    # button) without having to uninstall the file.
    if not bool(_cfg("clipboard_history_enabled", True)):
        print("[clipboard] disabled in settings - not registering")
        return
    _start_watcher_once()
    register_plugin(Plugin(
        name="clipboard-history",
        icon="linuxpop-clipboard-symbolic",
        tooltip="Clipboard & snippets",
        handler=_open_from_popup,
        content_types=(),  # always available
        priority=5,
    ))
