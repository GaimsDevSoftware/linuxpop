"""Watches the X11 PRIMARY selection and notifies a callback when it changes.

Uses XFixes selection-owner-notify events (event-driven, ~zero CPU when
idle) with a short xclip read after each event to fetch the actual text.
"""
from __future__ import annotations

import select as _select
import subprocess
import threading
import time
from typing import Callable, Optional

from Xlib import display
from Xlib.ext import xfixes


class SelectionWatcher:
    def __init__(
        self,
        on_selection: Callable[[str, int, int], None],
        debounce_ms: int = 300,
    ) -> None:
        self._on_selection = on_selection
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_text: str = ""
        # Wait this long after the last selection-change event before firing.
        # Avoids popup churn while the user is still dragging the selection —
        # X PRIMARY updates on every character as the drag extends.
        self._debounce_s: float = max(0.0, debounce_ms / 1000.0)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="linuxpop-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_debounce_ms(self, ms: int) -> None:
        """Update the debounce window live. Read by the event loop on its
        next iteration — no restart needed."""
        self._debounce_s = max(0.0, ms / 1000.0)

    def _read_primary(self) -> str:
        try:
            output = subprocess.run(
                ["xclip", "-selection", "primary", "-o"],
                capture_output=True,
                timeout=0.5,
            )
            return output.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return ""

    def _pointer_position(self, dpy: display.Display) -> tuple[int, int]:
        data = dpy.screen().root.query_pointer()
        return data.root_x, data.root_y

    def _handle_selection_change(self, dpy: display.Display) -> None:
        # Try a few times to read the new content. The xfixes event fires
        # on owner change, but the new owner often hasn't actually written
        # the data yet (esp. apps that build their selection lazily on
        # CONVERT_SELECTION). One short sleep was racy on slower machines
        # and certain GTK apps. Retry with backoff up to ~250 ms.
        text = ""
        for delay in (0.04, 0.08, 0.15):
            time.sleep(delay)
            text = self._read_primary()
            if text and text.strip():
                break
        if not text or text == self._last_text or not text.strip():
            return
        self._last_text = text
        try:
            x, y = self._pointer_position(dpy)
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] pointer query failed: {exc}")
            x, y = 0, 0
        try:
            self._on_selection(text, x, y)
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] callback error: {exc}")

    def _run(self) -> None:
        try:
            dpy = display.Display()
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] cannot open display: {exc}")
            return

        # Try to set up XFixes selection-change notifications
        use_xfixes = False
        try:
            dpy.xfixes_query_version()
            root = dpy.screen().root
            primary_atom = dpy.intern_atom("PRIMARY")
            dpy.xfixes_select_selection_input(
                root,
                primary_atom,
                xfixes.XFixesSetSelectionOwnerNotifyMask,
            )
            dpy.flush()
            use_xfixes = True
            print("[watcher] using XFixes selection events")
        except Exception as exc:  # noqa: BLE001
            print(f"[watcher] XFixes unavailable ({exc}); falling back to polling")

        try:
            if use_xfixes:
                self._event_loop(dpy)
            else:
                self._poll_loop(dpy)
        finally:
            try:
                dpy.close()
            except Exception:
                pass

    def _event_loop(self, dpy: display.Display) -> None:
        fd = dpy.fileno()
        # Idle wakeup cadence — keep at 5 s so a quiet daemon barely costs
        # anything. While a selection change is pending, we override the
        # timeout with the remaining debounce window so we fire promptly.
        IDLE_TIMEOUT = 5.0
        pending = False
        last_event_at = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if pending:
                # Wait at most until the debounce window expires
                timeout = max(0.0, self._debounce_s - (now - last_event_at))
            else:
                timeout = IDLE_TIMEOUT
            try:
                readable, _, _ = _select.select([fd], [], [], timeout)
            except (OSError, ValueError):
                break
            if readable:
                had_event = False
                while dpy.pending_events():
                    _ = dpy.next_event()
                    had_event = True
                if had_event:
                    pending = True
                    last_event_at = time.monotonic()
                    # Loop back to start a new debounce wait
                    continue
            # No fd activity in the timeout window. If we were debouncing,
            # the user has stopped extending the selection — fire now.
            if pending and (time.monotonic() - last_event_at) >= self._debounce_s:
                pending = False
                self._handle_selection_change(dpy)

    def _poll_loop(self, dpy: display.Display) -> None:
        # Fallback: 250 ms polling. Less responsive but XFixes-free.
        while not self._stop.is_set():
            text = self._read_primary()
            if text and text != self._last_text and text.strip():
                self._last_text = text
                try:
                    x, y = self._pointer_position(dpy)
                except Exception:
                    x, y = 0, 0
                try:
                    self._on_selection(text, x, y)
                except Exception as exc:  # noqa: BLE001
                    print(f"[watcher] callback error: {exc}")
            time.sleep(0.25)
