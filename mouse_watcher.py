"""Global Ctrl+double-click watcher for the no-selection popup.

PopClip-style "click-in-editable-field" feature: Ctrl+double-click
anywhere and the edit menu (Paste / Select All / Backspace) appears
at the cursor. Ctrl as the gating modifier means we never collide
with the app's own double-click word-select behaviour - the user has
to deliberately ask for our menu, so there's no race with PRIMARY
selections from word-select gestures.

Opt-in via the `double_click_popup_enabled` setting. When off, the
thread is never started and no mouse events are observed.

Position match uses an 8 px tolerance so a tiny mouse wiggle
between the two clicks still counts as a double-click.

The watcher emits the callback on the GLib main thread via
timeout_add, so the popup builder doesn't need its own locking.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from gi.repository import GLib


_DOUBLE_CLICK_MS = 300
_POSITION_TOLERANCE_PX = 8


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
                # Button 1 = primary mouse button (left for right-
                # handers, right for southpaws who've swapped). Ignore
                # mouse wheel (4/5) and middle (2). Require Ctrl held
                # so we never collide with the app's own word-select
                # gesture - this is the chord that opens our menu.
                if (event.type == X.ButtonPress
                        and event.detail == 1
                        and event.state & X.ControlMask):
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
        """Handle a Ctrl+Left-click. We see only the Ctrl-modified
        ones - the handler filter upstream drops plain clicks - so
        any second click within the double-click window IS our
        gesture and there's no PRIMARY-race to worry about."""
        now_ms = int(time.monotonic() * 1000)
        elapsed = now_ms - self._last_ms
        dx = abs(x - self._last_xy[0])
        dy = abs(y - self._last_xy[1])
        if (elapsed < _DOUBLE_CLICK_MS
                and dx < _POSITION_TOLERANCE_PX
                and dy < _POSITION_TOLERANCE_PX):
            # Reset so a third click doesn't fire again.
            self._last_ms = 0
            self._last_xy = (0, 0)
            # Tiny settle so the app sees the click first, then we
            # show the menu. No need to poll PRIMARY - Ctrl-double-
            # click is unambiguous user intent.
            GLib.timeout_add(50, self._fire_callback, x, y)
            return
        self._last_ms = now_ms
        self._last_xy = (x, y)

    def _fire_callback(self, x: int, y: int) -> bool:
        try:
            self._cb(x, y)
        except Exception as exc:
            print(f"[dblclick] callback failed: {exc}")
        return False  # one-shot
