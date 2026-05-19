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
    ) -> None:
        self._on_selection = on_selection
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_text: str = ""

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="linuxpop-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

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
        # Give the new selection owner a moment to publish the content
        time.sleep(0.05)
        text = self._read_primary()
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
        # 5 s timeout: clean shutdown is "within 5 s of stop()", which is
        # acceptable for a tray daemon and cuts wakeups by 80% vs 1 s.
        while not self._stop.is_set():
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
