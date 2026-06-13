#!/usr/bin/env python3
"""LinuxPop tray icon — manual D-Bus StatusNotifierItem (no KStatusNotifierItem).

Pure PySide6 + QtDBus. Works around Fedora 44 KSNI D-Bus registration bug.
Based on Freedesktop SNI spec + ssokolow's reference implementation.
"""
from __future__ import annotations

import json, os, socket, sys, struct, shutil
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMenu
from PySide6.QtGui import QAction
from PySide6.QtCore import QObject, QTimer, Signal, Slot, Property
from PySide6 import QtDBus

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

# ─── Manual D-Bus StatusNotifierItem ────────────────────────────────

class StatusNotifierItemDBus(QObject):
    """Manual SNI via D-Bus — no KStatusNotifierItem dependency."""

    NewTitle = Signal()
    NewIcon = Signal()
    NewAttentionIcon = Signal()
    NewOverlayIcon = Signal()
    NewToolTip = Signal()
    NewStatus = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id = f"linuxpop-{os.getpid()}"
        self._title = "LinuxPop"
        self._status = "Active"
        self._category = "ApplicationStatus"
        self._icon_name = "linuxpop-tray-symbolic"
        self._attention_icon_name = ""
        self._overlay_icon_name = ""
        self._tooltip_title = "LinuxPop"
        self._tooltip_subtitle = "Clipboard popup assistant"
        self._tooltip_icon_name = ""
        self._menu_path = "/NO_DBUS_MENU"
        self._item_is_menu = False
        self._window_id = 0
        self._dbus_svc: str | None = None
        self._registered = False
        self._callback = None

    # ─── D-Bus Properties ───

    @Property(str)
    def Category(self): return self._category
    @Property(str)
    def Id(self): return self._id
    @Property(str)
    def Title(self): return self._title
    @Property(str)
    def Status(self): return self._status
    @Property(int)
    def WindowId(self): return self._window_id
    @Property(str)
    def IconName(self): return self._icon_name
    @Property(str)
    def IconThemePath(self): return ICON_DIR
    @Property(str)
    def AttentionIconName(self): return self._attention_icon_name
    @Property(str)
    def OverlayIconName(self): return self._overlay_icon_name
    @Property("QVariant")
    def ToolTip(self):
        return (self._tooltip_icon_name or self._icon_name, [],
                self._tooltip_title, self._tooltip_subtitle)
    @Property(bool)
    def ItemIsMenu(self): return self._item_is_menu
    @Property(str)
    def Menu(self): return self._menu_path

    # ─── D-Bus Slots ───

    @Slot(int, int)
    def ContextMenu(self, x, y): pass  # menu shown via Qt, not dbusmenu

    @Slot(int, int)
    def Activate(self, x, y):
        if self._callback:
            self._callback("show_popup", None)

    @Slot(int, int)
    def SecondaryActivate(self, x, y): pass

    @Slot(int, int)
    def Scroll(self, delta, orientation): pass

    def set_callback(self, cb):
        self._callback = cb

    def register_on_dbus(self) -> bool:
        session = QtDBus.QDBusConnection.sessionBus()
        if not session.isConnected():
            print("[tray-qt] D-Bus session not connected", flush=True)
            return False
        for i in range(100):
            svc = f"org.kde.StatusNotifierItem-{os.getpid()}-{i}"
            if session.registerService(svc):
                self._dbus_svc = svc
                break
        else:
            print("[tray-qt] Could not register D-Bus service", flush=True)
            return False
        flags = (QtDBus.QDBusConnection.ExportAllSlots
                 | QtDBus.QDBusConnection.ExportAllProperties
                 | QtDBus.QDBusConnection.ExportAllSignals)
        if not session.registerObject("/StatusNotifierItem", self, flags):
            print("[tray-qt] Could not register D-Bus object", flush=True)
            session.unregisterService(self._dbus_svc)
            self._dbus_svc = None
            return False
        msg = QtDBus.QDBusMessage.createMethodCall(
            "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
            "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem")
        msg.setArguments([self._dbus_svc])
        reply = session.call(msg)
        if reply.type() == QtDBus.QDBusMessage.ReplyMessage:
            self._registered = True
            print(f"[tray-qt] SNI registered: {self._dbus_svc}", flush=True)
            return True
        else:
            print(f"[tray-qt] Watcher rejected: {reply.errorMessage()}", flush=True)
            session.unregisterObject("/StatusNotifierItem")
            session.unregisterService(self._dbus_svc)
            self._dbus_svc = None
            return False

    def unregister_from_dbus(self):
        if self._dbus_svc:
            session = QtDBus.QDBusConnection.sessionBus()
            session.unregisterObject("/StatusNotifierItem")
            session.unregisterService(self._dbus_svc)
            self._dbus_svc = None
            self._registered = False

# ─── TrayQt ─────────────────────────────────────────────────────────

class TrayQt:
    def __init__(self) -> None:
        self._app = QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)
        self._app.setApplicationName("linuxpop-tray")

        # Manual D-Bus SNI
        self._sni = StatusNotifierItemDBus()
        self._sni.set_callback(self._on_sni_event)

        # Qt context menu (shown on right-click via xembed fallback)
        self._menu = QMenu()
        self._setup_menu()

        self._install_icon()

        # D-Bus registration
        if not self._sni.register_on_dbus():
            print("[tray-qt] WARNING: SNI D-Bus registration failed", flush=True)

        # Socket
        self._sock: socket.socket | None = None
        self._client: socket.socket | None = None
        self._running = True
        self._setup_socket()

        self._timer = QTimer()
        self._timer.timeout.connect(self._check_socket)
        self._timer.start(100)

        print("[tray-qt] Started (manual D-Bus SNI)", flush=True)

    def _on_sni_event(self, event: str, value: object):
        self._emit(event, value)

    def _install_icon(self) -> None:
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

    def _setup_menu(self) -> None:
        h = self._menu.addAction("LinuxPop")
        h.setEnabled(False)
        self._menu.addSeparator()
        self._toggle_action = self._menu.addAction("Auto-popup on selection")
        self._toggle_action.setCheckable(True)
        self._toggle_action.setChecked(True)
        self._toggle_action.triggered.connect(lambda checked: self._emit("toggle_watcher", checked))
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
        self._sni.unregister_from_dbus()
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
