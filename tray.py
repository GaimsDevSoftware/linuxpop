"""System tray icon (KStatusNotifierItem via separate Qt process) for LinuxPop.

Spawns a lean Qt process that uses KStatusNotifierItem — native KDE/Wayland
protocol, no XWayland dependency, no GTK thread conflicts.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

SOCKET_DIR = Path(os.path.expanduser("~/.cache/linuxpop"))
TRAY_SCRIPT = str(Path(__file__).resolve().parent / "tray_qt.py")


def _tray_preexec() -> None:
    """Runs in the tray child between fork and exec.

    1. PR_SET_PDEATHSIG: ask the kernel to SIGTERM this process the instant
       its parent (the main daemon) dies. Without this, killing or crashing
       the daemon left the Qt tray subprocess orphaned (reparented to systemd)
       and its tray icon lingered forever -- which is how two LinuxPop icons
       could end up side by side after a few restarts.
    2. setsid(): give the tray its own session so a Ctrl-C in the daemon's
       terminal doesn't also tear it down (the original intent of the
       start_new_session flag this replaces).

    The death-signal is armed FIRST, then we re-check the parent is still
    alive, to close the tiny fork-then-parent-exits race before setsid()."""
    try:
        import ctypes
        PR_SET_PDEATHSIG = 1
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(
            PR_SET_PDEATHSIG, signal.SIGTERM)
        # If the parent vanished between fork and now, PDEATHSIG already
        # missed its window -- exit rather than become the next orphan.
        if os.getppid() == 1:
            os._exit(0)
    except Exception:
        pass
    try:
        os.setsid()
    except OSError:
        pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed")
        buf += chunk
    return buf


def _recv_message(sock: socket.socket) -> dict | None:
    try:
        raw_len = _recv_exact(sock, 4)
        msg_len = struct.unpack("!I", raw_len)[0]
        if msg_len > 1_000_000:
            return None
        raw = _recv_exact(sock, msg_len)
        return json.loads(raw.decode("utf-8"))
    except (ConnectionError, OSError, json.JSONDecodeError):
        return None


def _send_message(sock: socket.socket, msg: dict) -> None:
    raw = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


class Tray:
    """Same API as the old GTK/Ayatana Tray, but backed by a Qt KSNI subprocess."""

    def __init__(
        self,
        on_toggle_watcher: Callable[[bool], None],
        get_watcher_active: Callable[[], bool],
        on_show_popup_now: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_open_plugins: Callable[[], None],
        on_open_about: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_support: Callable[[], None] | None = None,
    ) -> None:
        self._on_toggle_watcher = on_toggle_watcher
        self._get_watcher_active = get_watcher_active
        self._on_show_popup_now = on_show_popup_now
        self._on_open_settings = on_open_settings
        self._on_open_plugins = on_open_plugins
        self._on_open_about = on_open_about
        self._on_quit = on_quit
        self._on_open_support = on_open_support

        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._connected = False

        if not os.path.isfile(TRAY_SCRIPT):
            print("[tray] tray_qt.py not found — tray disabled")
            return

        self._start_process()
        self._connect()

    def _start_process(self) -> None:
        """Launch the Qt tray subprocess."""
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)
        # Remove stale socket
        try:
            os.unlink(str(SOCKET_DIR / "tray.sock"))
        except OSError:
            pass
        try:
            self._proc = subprocess.Popen(
                [sys.executable, TRAY_SCRIPT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                preexec_fn=_tray_preexec,  # die with parent + own session
            )
            print(f"[tray] spawned Qt tray process (pid={self._proc.pid})")
        except OSError as exc:
            print(f"[tray] failed to start tray process: {exc}")
            self._proc = None

    def _connect(self) -> None:
        """Connect to the tray subprocess's Unix socket with retries."""
        socket_path = str(SOCKET_DIR / "tray.sock")
        for attempt in range(30):  # wait up to ~3 seconds
            if not os.path.exists(socket_path):
                time.sleep(0.1)
                continue
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.settimeout(3)
                self._sock.connect(socket_path)
                self._sock.setblocking(True)
                self._connected = True
                # Sync initial watcher state
                self.refresh()
                print("[tray] connected to tray subprocess")
                return
            except (OSError, ConnectionRefusedError):
                time.sleep(0.1)
        print("[tray] WARNING: could not connect to tray subprocess")

    def _reconnect(self) -> bool:
        """Try to reconnect if the socket was lost."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._connected = False
        # If the subprocess died, restart it
        if self._proc is None or self._proc.poll() is not None:
            self._start_process()
        self._connect()
        return self._connected

    def _dispatch(self, event: str, value: object) -> None:
        """Route an event from the tray subprocess to the right callback."""
        if event == "toggle_watcher":
            self._on_toggle_watcher(bool(value))
        elif event == "show_popup":
            self._on_show_popup_now()
        elif event == "settings":
            self._on_open_settings()
        elif event == "plugins":
            self._on_open_plugins()
        elif event == "about":
            self._on_open_about()
        elif event == "support":
            if self._on_open_support:
                self._on_open_support()
        elif event == "quit":
            self._on_quit()
        elif event == "pong":
            pass  # keepalive

    def refresh(self) -> None:
        """Update tray state from app — called by main.py."""
        if not self._connected or self._sock is None:
            self._reconnect()
            return
        try:
            active = self._get_watcher_active()
            _send_message(self._sock, {"cmd": "set_watcher_active", "value": active})
        except OSError:
            self._connected = False
            self._reconnect()

    def poll(self) -> None:
        """Read any pending messages from the tray subprocess.
        
        Must be called periodically from the GTK main loop (via GLib.timeout_add).
        """
        if not self._connected or self._sock is None:
            self._reconnect()
            return
        try:
            self._sock.setblocking(False)
            while True:
                try:
                    msg = _recv_message(self._sock)
                    if msg is None:
                        break
                    self._dispatch(msg.get("event", ""), msg.get("value"))
                except BlockingIOError:
                    break
        except (OSError, ConnectionError):
            self._connected = False
            self._reconnect()

    def shutdown(self) -> None:
        """Tell tray subprocess to quit and wait for it."""
        if self._connected and self._sock:
            try:
                _send_message(self._sock, {"cmd": "quit"})
            except OSError:
                pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._proc:
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None
