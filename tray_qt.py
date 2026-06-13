#!/usr/bin/env python3
"""LinuxPop tray icon — Qt QSystemTrayIcon (StatusNotifierItem + DBusMenu).

Uses QSystemTrayIcon, NOT the KF6 KStatusNotifierItem (which had a Fedora 44
D-Bus registration bug). Qt's tray registers the SNI *and* exports the context
menu as a com.canonical.dbusmenu object that plasmashell renders itself — the
only thing that actually shows a menu on KWin/Wayland (a parentless QMenu.popup
never maps). Talks to the main LinuxPop process over a small length-prefixed
JSON socket, unchanged from the previous implementation.
"""
from __future__ import annotations

import json, os, socket, sys, struct
from pathlib import Path

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtGui import QIcon, QCursor
from PySide6.QtCore import QTimer

ICON_DIR = str(Path(__file__).resolve().parent / "icons")
SOCKET_DIR = Path(os.path.expanduser("~/.cache/linuxpop"))

# ─── Wire helpers ───────────────────────────────────────────────────

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

# ─── TrayQt ─────────────────────────────────────────────────────────

class TrayQt:
    def __init__(self) -> None:
        self._app = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationName("linuxpop-tray")
        self._app.setDesktopFileName("linuxpop")

        self._install_icon()

        self._menu = QMenu()
        self._setup_menu()

        # QSystemTrayIcon registers the SNI and exports `self._menu` as a
        # DBusMenu. plasmashell renders that menu on its own surface (correctly
        # positioned) when the user activates the item — no client-side popup.
        self._tray = QSystemTrayIcon()
        self._tray.setIcon(self._load_icon())
        self._tray.setToolTip("LinuxPop — clipboard popup assistant")
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)
        self._tray.show()

        # Socket
        self._sock: socket.socket | None = None
        self._client: socket.socket | None = None
        self._running = True
        self._setup_socket()

        self._timer = QTimer()
        self._timer.timeout.connect(self._check_socket)
        self._timer.start(100)

        # Parent-death watchdog. PR_SET_PDEATHSIG (set by the launcher) is the
        # primary guard, but it can miss if the tray gets reparented to a
        # systemd subreaper rather than PID 1. So also poll our parent PID:
        # the moment it stops being the daemon that spawned us, that daemon is
        # gone and we must remove our tray icon instead of lingering forever.
        self._initial_ppid = os.getppid()
        self._ppid_timer = QTimer()
        self._ppid_timer.timeout.connect(self._check_parent_alive)
        self._ppid_timer.start(2000)

        avail = QSystemTrayIcon.isSystemTrayAvailable()
        print(f"[tray-qt] Started (QSystemTrayIcon, tray_available={avail})",
              flush=True)

    # ─── icon ───
    def _load_icon(self) -> QIcon:
        for name in ("linuxpop-tray-symbolic", "linuxpop"):
            p = Path(ICON_DIR) / f"{name}.svg"
            if p.is_file():
                ic = QIcon(str(p))
                if not ic.isNull():
                    return ic
        # Fall back to a themed name so we never show a blank item.
        return (QIcon.fromTheme("linuxpop")
                or QIcon.fromTheme("applications-internet"))

    def _install_icon(self) -> None:
        import shutil
        user_dir = Path.home() / ".local/share/icons/hicolor/scalable/apps"
        user_dir.mkdir(parents=True, exist_ok=True)
        for name in ("linuxpop-tray-symbolic", "linuxpop"):
            src = Path(ICON_DIR) / f"{name}.svg"
            dst = user_dir / f"{name}.svg"
            if src.is_file():
                try:
                    shutil.copy2(src, dst)
                except OSError:
                    pass

    # ─── activation ───
    def _on_activated(self, reason) -> None:
        # Right-click already shows the DBusMenu (plasmashell renders it). On
        # left-click (Trigger) / middle-click, surface the same menu so the user
        # always reaches Settings/Plugins however they click. The popup here is
        # driven by a real input event, so it maps on Wayland.
        R = QSystemTrayIcon.ActivationReason
        if reason in (R.Trigger, R.MiddleClick):
            self._menu.popup(QCursor.pos())

    # ─── menu ───
    def _setup_menu(self) -> None:
        h = self._menu.addAction("LinuxPop")
        h.setEnabled(False)
        self._menu.addSeparator()
        self._toggle_action = self._menu.addAction("Auto-popup on selection")
        self._toggle_action.setCheckable(True)
        self._toggle_action.setChecked(True)
        self._toggle_action.triggered.connect(
            lambda checked: self._emit("toggle_watcher", checked))
        a = self._menu.addAction("Show popup now")
        a.triggered.connect(lambda: self._emit("show_popup", None))
        self._menu.addSeparator()
        a = self._menu.addAction("Settings…")
        a.triggered.connect(lambda: self._emit("settings", None))
        a = self._menu.addAction("Plugins…")
        a.triggered.connect(lambda: self._emit("plugins", None))
        a = self._menu.addAction("About LinuxPop")
        a.triggered.connect(lambda: self._emit("about", None))
        a = self._menu.addAction("Support LinuxPop…")
        a.triggered.connect(lambda: self._emit("support", None))
        self._menu.addSeparator()
        a = self._menu.addAction("Quit LinuxPop")
        a.triggered.connect(lambda: self._emit("quit", None))

    def _emit(self, event: str, value: object) -> None:
        if self._client:
            try:
                _send_message(self._client, {"event": event, "value": value})
            except OSError:
                pass

    # ─── socket IPC (unchanged protocol) ───
    def _setup_socket(self) -> None:
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)
        socket_path = str(SOCKET_DIR / "tray.sock")
        try:
            os.unlink(socket_path)
        except OSError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(socket_path)
        self._sock.listen(1)
        self._sock.setblocking(False)
        info_file = SOCKET_DIR / "tray.info"
        info_file.write_text(f"{socket_path}\n{os.getpid()}\n")

    def _check_parent_alive(self) -> None:
        """Quit if the daemon that spawned us is gone. Detected by the parent
        PID changing (reparented to init or a systemd subreaper). Removes our
        tray icon so a crashed/killed daemon can't leave a ghost behind."""
        try:
            if os.getppid() != self._initial_ppid:
                print("[tray-qt] parent daemon gone -- exiting", flush=True)
                self._running = False
                self._app.quit()
        except Exception:
            pass

    def _check_socket(self) -> None:
        if not self._running:
            self._app.quit()
            return
        try:
            if self._client is None:
                try:
                    self._client, _addr = self._sock.accept()
                    self._client.setblocking(True)
                except BlockingIOError:
                    return
            self._client.setblocking(False)
            try:
                msg = _recv_message(self._client)
                if msg is not None:
                    self._handle_command(msg)
            except BlockingIOError:
                pass
        except OSError:
            self._disconnect_client()

    def _disconnect_client(self) -> None:
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass
            self._client = None

    def _handle_command(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "set_watcher_active":
            self._toggle_action.setChecked(bool(msg.get("value", True)))
        elif cmd == "quit":
            self._running = False
        elif cmd == "ping":
            if self._client:
                try:
                    _send_message(self._client, {"event": "pong", "value": None})
                except OSError:
                    pass

    def run(self) -> None:
        self._app.exec()
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

if __name__ == "__main__":
    TrayQt().run()
