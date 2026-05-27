"""Global double-click watcher for the no-selection popup.

PopClip's "click-in-editable-field" feature: double-click in an empty
text field and the edit menu (Paste / Select All / etc.) appears at
the cursor. Implemented here with the X11 RECORD extension, the same
mechanism the snippet-trigger watcher uses for keystrokes.

Opt-in via the `double_click_popup_enabled` setting. When off, the
thread is never started and no mouse events are observed.

Edge cases:
  - Double-clicking inside an existing word in an editable field
    selects that word. We delay 50 ms after the second click so the
    selection has time to settle, then check the X11 PRIMARY buffer.
    If it has text we treat the gesture as "select a word" and stay
    out of the way - the regular selection popup will handle it.
  - Position match uses an 8 px tolerance so a tiny mouse wiggle
    between the two clicks still counts as a double-click.
  - The watcher emits the callback on the GLib main thread via
    idle_add, so the popup builder doesn't need its own locking.
"""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Callable, Optional

from gi.repository import GLib


def _read_primary_snapshot() -> bytes:
    """Capture the current X11 PRIMARY selection as raw bytes. Returned
    as bytes (not text) so binary or whitespace-only selections compare
    correctly against later snapshots. Returns empty on any xclip error."""
    try:
        out = subprocess.run(
            ["xclip", "-selection", "primary", "-o"],
            capture_output=True, timeout=0.3,
        )
        return out.stdout or b""
    except (OSError, subprocess.SubprocessError):
        return b""


_DOUBLE_CLICK_MS = 300
_POSITION_TOLERANCE_PX = 8
# How long to wait after the second click before checking PRIMARY.
# Was 50 ms, but several apps (Firefox, GTK textviews) take longer
# than that to publish a freshly-selected word to PRIMARY. Result:
# we'd snapshot too early, see no change, and fire the no-selection
# popup over what was actually a word-selection gesture.
# 200 ms is roughly the upper bound observed in testing; humans
# don't perceive sub-200 ms latency as lag for a deliberate gesture.
_POST_CLICK_DELAY_MS = 200


class DoubleClickWatcher:
    """Listens for global left-button double-clicks on the root window."""

    def __init__(self, on_double_click: Callable[[int, int], None]) -> None:
        self._cb = on_double_click
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._record_display = None
        self._local_display = None
        self._ctx = None
        # Tracking the last single click so we can recognise the
        # following one as a double-click.
        self._last_ms = 0
        self._last_xy = (0, 0)
        # PRIMARY-selection snapshot from the first click of a potential
        # double-click. If the second click ends up selecting a word
        # under the cursor, PRIMARY will differ from this snapshot and
        # we'll stay out of the way (the selection watcher handles it).
        self._primary_at_first: bytes = b""

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="linuxpop-dblclick",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._local_display is not None and self._ctx is not None:
                self._local_display.record_disable_context(self._ctx)
                self._local_display.flush()
        except Exception:
            pass

    def _run(self) -> None:
        try:
            from Xlib import display as Xdisplay, X
            from Xlib.ext import record
            from Xlib.protocol import rq
        except ImportError:
            print("[dblclick] python-xlib record extension missing - disabled")
            return
        try:
            self._record_display = Xdisplay.Display()
            self._local_display = Xdisplay.Display()
            v = self._local_display.record_get_version(0, 0)
            print(f"[dblclick] XRecord {v.major_version}.{v.minor_version} ready")
        except Exception as exc:
            print(f"[dblclick] could not open X displays: {exc}")
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
                "device_events": (X.ButtonPress, X.ButtonRelease),
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
                # Button 1 = primary mouse button (left for right-handers,
                # right for southpaws who've swapped). Ignore mouse wheel
                # (4/5) and middle (2) - this is specifically a left-
                # button gesture.
                if event.type == X.ButtonPress and event.detail == 1:
                    self._on_left_click(event.root_x, event.root_y)

        try:
            self._local_display.record_enable_context(self._ctx, handler)
        except Exception as exc:
            print(f"[dblclick] record_enable_context exited: {exc}")
        finally:
            try:
                self._local_display.record_free_context(self._ctx)
            except Exception:
                pass
            self._ctx = None

    def _on_left_click(self, x: int, y: int) -> None:
        now_ms = int(time.monotonic() * 1000)
        elapsed = now_ms - self._last_ms
        dx = abs(x - self._last_xy[0])
        dy = abs(y - self._last_xy[1])
        if (elapsed < _DOUBLE_CLICK_MS
                and dx < _POSITION_TOLERANCE_PX
                and dy < _POSITION_TOLERANCE_PX):
            # Reset so a third click doesn't fire again.
            primary_before = self._primary_at_first
            self._last_ms = 0
            self._last_xy = (0, 0)
            self._primary_at_first = b""
            GLib.timeout_add(_POST_CLICK_DELAY_MS,
                              self._fire_callback, x, y, primary_before)
            return
        self._last_ms = now_ms
        self._last_xy = (x, y)
        # Snapshot PRIMARY now so we can tell the difference, in
        # _fire_callback 50 ms from now, between "the second click
        # selected a word" (PRIMARY changed) and "double-clicked in
        # an empty field" (PRIMARY identical, ours to handle).
        self._primary_at_first = _read_primary_snapshot()

    def _fire_callback(self, x: int, y: int, primary_before: bytes) -> bool:
        primary_now = _read_primary_snapshot()
        if primary_now != primary_before:
            # The second click selected a word - the selection watcher
            # will surface the regular popup for that selection. We stay
            # out of the way.
            return False
        try:
            self._cb(x, y)
        except Exception as exc:
            print(f"[dblclick] callback failed: {exc}")
        return False  # one-shot
