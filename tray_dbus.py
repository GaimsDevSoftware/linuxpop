#!/usr/bin/env python3
"""LinuxPop tray icon - hand-rolled StatusNotifierItem (dbus-python + GLib).

Why not QSystemTrayIcon?  Qt hardcodes the SNI `ItemIsMenu` property to
false (see QStatusNotifierItemAdaptor::itemIsMenu in qtbase), and there is
no public API to change it.  With ItemIsMenu=false the host (plasmashell)
sends Activate() on left-click instead of showing the menu, and a
client-side QMenu.popup() never maps on KDE Wayland - so left-click could
not surface the menu.  KStatusNotifierItem.setIsMenu(true) is the documented
fix but it has no PySide6 binding and hit a Fedora D-Bus registration bug.

So we implement the StatusNotifierItem + com.canonical.dbusmenu ourselves
with **ItemIsMenu=true**, which tells plasmashell to render our context menu
on BOTH left- and right-click.  dbus-python is already bundled (the Wayland
backend uses it for KGlobalAccel) and marshals the nested DBusMenu types far
more painlessly than QtDBus.  The icon is rasterised with GdkPixbuf; the
length-prefixed JSON socket protocol to the main daemon is unchanged.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import struct
import sys
from pathlib import Path

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

import dbus  # noqa: E402
import dbus.service  # noqa: E402
from dbus.mainloop.glib import DBusGMainLoop  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xdg_paths import CACHE_DIR as SOCKET_DIR, CONFIG_DIR  # noqa: E402

ICON_DIR = Path(__file__).resolve().parent / "icons"
SETTINGS_FILE = CONFIG_DIR / "settings.json"

SNI_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS_IFACE = "org.freedesktop.DBus.Properties"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
SNI_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"


# ─── settings / icon ────────────────────────────────────────────────

def _tray_icon_style() -> str:
    """'color' (coloured badge, default), 'light' (monochrome for dark
    panels) or 'dark' (monochrome for light panels)."""
    try:
        d = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        v = str(d.get("tray_icon_style", "color")).strip().lower()
        return v if v in ("color", "light", "dark") else "color"
    except Exception:
        return "color"


def _pixbuf_to_argb(pb: "GdkPixbuf.Pixbuf") -> bytes:
    """GdkPixbuf RGBA -> SNI IconPixmap ARGB32 in network (big-endian) order."""
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    src = pb.get_pixels()
    out = bytearray(w * h * 4)
    o = 0
    for y in range(h):
        row = y * stride
        for x in range(w):
            i = row + x * 4
            r, g, b, a = src[i], src[i + 1], src[i + 2], src[i + 3]
            out[o] = a
            out[o + 1] = r
            out[o + 2] = g
            out[o + 3] = b
            o += 4
    return bytes(out)


def _render_icon_pixmaps() -> "dbus.Array":
    """Build the SNI IconPixmap array a(iiay) for the current style.

    color -> the full-colour brand SVG; light/dark -> the monochrome
    symbolic SVG recoloured to a fixed panel-appropriate colour (plasmashell
    won't recolour custom symbolic icons reliably, so we bake the colour in).
    """
    style = _tray_icon_style()
    if style == "color":
        src = ICON_DIR / "linuxpop.svg"
        svg = src.read_text(encoding="utf-8") if src.is_file() else ""
    else:
        src = ICON_DIR / "linuxpop-tray-symbolic.svg"
        color = "#f4f5f6" if style == "light" else "#2a2e32"
        svg = (src.read_text(encoding="utf-8").replace("currentColor", color)
               if src.is_file() else "")
    pixmaps = dbus.Array([], signature="(iiay)")
    if not svg:
        return pixmaps
    raw = svg.encode("utf-8")
    for size in (16, 22, 24, 32, 48):
        try:
            loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
            loader.set_size(size, size)
            loader.write(raw)
            loader.close()
            pb = loader.get_pixbuf()
            if pb is None:
                continue
            argb = _pixbuf_to_argb(pb)
            pixmaps.append(dbus.Struct(
                (dbus.Int32(pb.get_width()), dbus.Int32(pb.get_height()),
                 dbus.ByteArray(argb)), signature="iiay"))
        except Exception as exc:  # noqa: BLE001
            print(f"[tray-dbus] icon render {size}px failed: {exc}", flush=True)
    return pixmaps


# ─── menu model ─────────────────────────────────────────────────────
# id 0 is the root. Each entry maps to a com.canonical.dbusmenu item. The
# `event` is the message sent to the main daemon when the item is clicked.

SEP = {"kind": "separator"}


def _build_menu_model() -> list[dict]:
    return [
        {"id": 1, "label": "LinuxPop", "enabled": False},
        {"id": 2, **SEP},
        {"id": 3, "label": "Auto-popup on selection", "toggle": True,
         "checked": True, "event": "toggle_watcher"},
        {"id": 4, "label": "Show popup now", "event": "show_popup"},
        {"id": 5, **SEP},
        {"id": 6, "label": "Settings…", "event": "settings"},
        {"id": 7, "label": "Plugins…", "event": "plugins"},
        {"id": 8, "label": "About LinuxPop", "event": "about"},
        {"id": 9, "label": "Support LinuxPop…", "event": "support"},
        {"id": 10, "label": "Contact on GitHub…",
         "url": "https://github.com/GaimsDevSoftware/linuxpop/issues"},
        {"id": 11, **SEP},
        {"id": 12, "label": "Quit LinuxPop", "event": "quit"},
    ]


# ─── socket wire helpers (unchanged protocol) ───────────────────────

def _send_message(sock: socket.socket, msg: dict) -> None:
    raw = json.dumps(msg).encode("utf-8")
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed")
        buf += chunk
    return buf


def _recv_message(sock: socket.socket) -> dict | None:
    raw_len = _recv_exact(sock, 4)
    msg_len = struct.unpack("!I", raw_len)[0]
    if msg_len > 1_000_000:
        return None
    return json.loads(_recv_exact(sock, msg_len).decode("utf-8"))


# ─── DBusMenu object ────────────────────────────────────────────────

class DBusMenu(dbus.service.Object):
    """Minimal com.canonical.dbusmenu the host renders itself."""

    def __init__(self, bus, on_event):
        super().__init__(bus, MENU_PATH)
        self._on_event = on_event
        self._items = _build_menu_model()
        self._revision = 1

    # -- helpers --
    def _item(self, item_id):
        for it in self._items:
            if it["id"] == item_id:
                return it
        return None

    def _props(self, it, names):
        if it.get("kind") == "separator":
            p = {"type": dbus.String("separator")}
        else:
            p = {"label": dbus.String(it.get("label", "")),
                 "enabled": dbus.Boolean(it.get("enabled", True)),
                 "visible": dbus.Boolean(True)}
            if it.get("toggle"):
                p["toggle-type"] = dbus.String("checkmark")
                p["toggle-state"] = dbus.Int32(1 if it.get("checked") else 0)
        if names:
            p = {k: v for k, v in p.items() if k in names}
        return dbus.Dictionary(p, signature="sv")

    def _leaf(self, it, names):
        return dbus.Struct(
            (dbus.Int32(it["id"]), self._props(it, names),
             dbus.Array([], signature="v")), signature="(ia{sv}av)")

    def set_checked(self, item_id, checked):
        it = self._item(item_id)
        if it is None:
            return
        it["checked"] = bool(checked)
        self.ItemsPropertiesUpdated(
            dbus.Array([dbus.Struct(
                (dbus.Int32(item_id),
                 dbus.Dictionary(
                     {"toggle-state": dbus.Int32(1 if checked else 0)},
                     signature="sv")), signature="(ia{sv})")],
                signature="(ia{sv})"),
            dbus.Array([], signature="(ias)"))

    # -- interface methods --
    @dbus.service.method(MENU_IFACE, in_signature="iias",
                         out_signature="u(ia{sv}av)")
    def GetLayout(self, parentId, recursionDepth, propertyNames):
        names = list(propertyNames)
        if parentId == 0:
            children = dbus.Array(
                [self._leaf(it, names) for it in self._items], signature="v")
            root = dbus.Struct(
                (dbus.Int32(0),
                 dbus.Dictionary({"children-display": dbus.String("submenu")},
                                 signature="sv"),
                 children), signature="(ia{sv}av)")
            return dbus.UInt32(self._revision), root
        it = self._item(parentId)
        if it is None:
            it = {"id": parentId}
        return dbus.UInt32(self._revision), self._leaf(it, names)

    @dbus.service.method(MENU_IFACE, in_signature="aias",
                         out_signature="a(ia{sv})")
    def GetGroupProperties(self, ids, propertyNames):
        names = list(propertyNames)
        want = list(ids) if ids else [it["id"] for it in self._items]
        out = dbus.Array([], signature="(ia{sv})")
        for item_id in want:
            it = self._item(item_id)
            if it is not None:
                out.append(dbus.Struct(
                    (dbus.Int32(item_id), self._props(it, names)),
                    signature="(ia{sv})"))
        return out

    @dbus.service.method(MENU_IFACE, in_signature="is", out_signature="v")
    def GetProperty(self, item_id, name):
        it = self._item(item_id) or {}
        return self._props(it, [name]).get(name, dbus.String(""))

    @dbus.service.method(MENU_IFACE, in_signature="isvu", out_signature="")
    def Event(self, item_id, eventId, data, timestamp):
        if str(eventId) != "clicked":
            return
        it = self._item(int(item_id))
        if it is None:
            return
        if it.get("toggle"):
            it["checked"] = not it.get("checked")
            self.set_checked(it["id"], it["checked"])
            self._on_event(it.get("event"), it["checked"])
        elif it.get("url"):
            self._on_event("__open_url__", it["url"])
        elif it.get("event"):
            self._on_event(it["event"], None)

    @dbus.service.method(MENU_IFACE, in_signature="a(isvu)",
                         out_signature="ai")
    def EventGroup(self, events):
        for ev in events:
            try:
                self.Event(ev[0], ev[1], ev[2], ev[3])
            except Exception:  # noqa: BLE001
                pass
        return dbus.Array([], signature="i")

    @dbus.service.method(MENU_IFACE, in_signature="i", out_signature="b")
    def AboutToShow(self, item_id):
        return dbus.Boolean(False)

    @dbus.service.method(MENU_IFACE, in_signature="ai",
                         out_signature="aiai")
    def AboutToShowGroup(self, ids):
        return (dbus.Array([], signature="i"), dbus.Array([], signature="i"))

    # -- properties --
    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface, prop):
        return self.GetAll(iface).get(prop, dbus.String(""))

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface):
        return dbus.Dictionary({
            "Version": dbus.UInt32(3),
            "Status": dbus.String("normal"),
            "TextDirection": dbus.String("ltr"),
            "IconThemePath": dbus.Array([], signature="s"),
        }, signature="sv")

    # -- signals --
    @dbus.service.signal(MENU_IFACE, signature="a(ia{sv})a(ias)")
    def ItemsPropertiesUpdated(self, updated, removed):
        pass

    @dbus.service.signal(MENU_IFACE, signature="ui")
    def LayoutUpdated(self, revision, parent):
        pass

    @dbus.service.signal(MENU_IFACE, signature="iu")
    def ItemActivationRequested(self, item_id, timestamp):
        pass


# ─── StatusNotifierItem object ──────────────────────────────────────

class StatusNotifierItem(dbus.service.Object):
    def __init__(self, bus, menu: DBusMenu, on_event):
        super().__init__(bus, SNI_PATH)
        self._menu = menu
        self._on_event = on_event
        self._pixmaps = _render_icon_pixmaps()
        self._style = _tray_icon_style()

    def reload_icon(self):
        self._pixmaps = _render_icon_pixmaps()
        self._style = _tray_icon_style()
        try:
            self.NewIcon()
        except Exception:  # noqa: BLE001
            pass

    # -- SNI methods (host -> us). With ItemIsMenu=true the host shows the
    #    menu itself, but we keep Activate as a fallback that asks the menu
    #    host to open via the standard activation request. --
    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def Activate(self, x, y):
        # With ItemIsMenu=true the host shows the menu itself on left-click and
        # never calls this. Hosts that ignore ItemIsMenu would call Activate -
        # but no Wayland protocol lets us pop a menu at the cursor, so there's
        # nothing useful to do; leave it a no-op rather than misbehave.
        pass

    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def SecondaryActivate(self, x, y):
        self._on_event("show_popup", None)

    @dbus.service.method(SNI_IFACE, in_signature="ii", out_signature="")
    def ContextMenu(self, x, y):
        pass  # host renders the DBusMenu itself

    @dbus.service.method(SNI_IFACE, in_signature="is", out_signature="")
    def Scroll(self, delta, orientation):
        pass

    # -- properties --
    @dbus.service.method(PROPS_IFACE, in_signature="ss", out_signature="v")
    def Get(self, iface, prop):
        return self.GetAll(iface).get(prop, dbus.String(""))

    @dbus.service.method(PROPS_IFACE, in_signature="s", out_signature="a{sv}")
    def GetAll(self, iface):
        # color -> themed IconName. light/dark -> embedded IconPixmap (works in
        # the Flatpak sandbox, where the host can't read our private icon
        # files). If pixmap rasterising ever yields nothing, fall back to the
        # themed name so the item is never iconless.
        icon_name = ("linuxpop"
                     if self._style == "color" or len(self._pixmaps) == 0
                     else "")
        return dbus.Dictionary({
            "Category": dbus.String("ApplicationStatus"),
            "Id": dbus.String("linuxpop-tray"),
            "Title": dbus.String("LinuxPop"),
            "Status": dbus.String("Active"),
            "WindowId": dbus.Int32(0),
            "IconName": dbus.String(icon_name),
            "IconPixmap": self._pixmaps,
            "OverlayIconName": dbus.String(""),
            "AttentionIconName": dbus.String(""),
            "ToolTip": dbus.Struct(
                (dbus.String(""), dbus.Array([], signature="(iiay)"),
                 dbus.String("LinuxPop - clipboard popup assistant"),
                 dbus.String("")), signature="(sa(iiay)ss)"),
            # The whole point: tells the host to show the menu on left-click.
            "ItemIsMenu": dbus.Boolean(True),
            "Menu": dbus.ObjectPath(MENU_PATH),
        }, signature="sv")

    @dbus.service.method(PROPS_IFACE, in_signature="ssv", out_signature="")
    def Set(self, iface, prop, value):
        pass

    # -- signals the host listens for --
    @dbus.service.signal(SNI_IFACE, signature="")
    def NewIcon(self):
        pass

    @dbus.service.signal(SNI_IFACE, signature="")
    def NewStatus(self):
        pass

    @dbus.service.signal(SNI_IFACE, signature="")
    def NewToolTip(self):
        pass


# ─── tray app ───────────────────────────────────────────────────────

class TrayDBus:
    def __init__(self) -> None:
        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()
        self._loop = GLib.MainLoop()

        self._install_icon()

        self._menu = DBusMenu(self._bus, self._on_event)
        self._sni = StatusNotifierItem(self._bus, self._menu, self._on_event)
        # Register with the watcher using our UNIQUE connection name (":1.x"),
        # not a well-known "org.kde.StatusNotifierItem-PID-1" name. We always
        # own our unique name, so this needs no D-Bus name-ownership permission
        # - which is exactly why it works inside the Flatpak sandbox (owning an
        # org.kde.* well-known name would be denied by the bus proxy). This is
        # the same approach Qt's QSystemTrayIcon uses. plasmashell introspects
        # /StatusNotifierItem on whatever service string we pass.
        self._service = self._bus.get_unique_name()
        self._registered = False
        self._register_with_watcher()
        # Re-register if the watcher (kded/plasmashell) restarts.
        self._bus.watch_name_owner(WATCHER_NAME, self._on_watcher_owner)

        # daemon socket
        self._client: socket.socket | None = None
        self._server: socket.socket | None = None
        self._setup_socket()

        self._initial_ppid = os.getppid()
        GLib.timeout_add(2000, self._check_parent_alive)

        print("[tray-dbus] started (StatusNotifierItem, ItemIsMenu=true)",
              flush=True)

    # -- icon install (so IconName='linuxpop' resolves) --
    def _install_icon(self) -> None:
        import shutil
        user_dir = Path.home() / ".local/share/icons/hicolor/scalable/apps"
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
            for name in ("linuxpop", "linuxpop-tray-symbolic"):
                src = ICON_DIR / f"{name}.svg"
                if src.is_file():
                    shutil.copy2(src, user_dir / f"{name}.svg")
        except OSError:
            pass

    # -- watcher registration --
    def _register_with_watcher(self) -> None:
        try:
            watcher = self._bus.get_object(WATCHER_NAME, WATCHER_PATH)
            watcher.RegisterStatusNotifierItem(
                self._service, dbus_interface=WATCHER_NAME)
            self._registered = True
            print("[tray-dbus] registered with StatusNotifierWatcher",
                  flush=True)
        except dbus.DBusException as exc:
            self._registered = False
            print(f"[tray-dbus] watcher registration failed: {exc}", flush=True)

    def _on_watcher_owner(self, owner: str) -> None:
        # Fires once with the current owner (already handled by the explicit
        # call above) and again whenever the watcher restarts. Only act on a
        # genuine (re)appearance we haven't registered with yet.
        if owner and not self._registered:
            self._register_with_watcher()
        elif not owner:
            self._registered = False

    # -- daemon socket IPC --
    def _setup_socket(self) -> None:
        SOCKET_DIR.mkdir(parents=True, exist_ok=True)
        path = str(SOCKET_DIR / "tray.sock")
        try:
            os.unlink(path)
        except OSError:
            pass
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(path)
        self._server.listen(1)
        self._server.setblocking(False)
        (SOCKET_DIR / "tray.info").write_text(f"{path}\n{os.getpid()}\n")
        GLib.io_add_watch(self._server.fileno(), GLib.IO_IN, self._on_accept)

    def _on_accept(self, *_a) -> bool:
        try:
            client, _ = self._server.accept()
        except OSError:
            return True
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass
        self._client = client
        self._client.setblocking(False)
        GLib.io_add_watch(self._client.fileno(),
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR,
                          self._on_client_data)
        return True  # keep listening

    def _on_client_data(self, _fd, condition) -> bool:
        if condition & (GLib.IO_HUP | GLib.IO_ERR):
            self._disconnect_client()
            return False
        try:
            msg = _recv_message(self._client)
        except (ConnectionError, OSError, BlockingIOError, json.JSONDecodeError):
            self._disconnect_client()
            return False
        if msg is not None:
            self._handle_command(msg)
        return True

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
            self._menu.set_checked(3, bool(msg.get("value", True)))
        elif cmd == "reload_icon":
            self._sni.reload_icon()
        elif cmd == "quit":
            self._loop.quit()
        elif cmd == "ping" and self._client:
            try:
                _send_message(self._client, {"event": "pong", "value": None})
            except OSError:
                pass

    # -- events from menu -> main daemon --
    def _on_event(self, event: str | None, value) -> None:
        if event == "__open_url__":
            self._open_url(value)
            return
        if event == "quit":
            # tell the daemon, then exit ourselves
            self._emit("quit", None)
            GLib.timeout_add(200, lambda: (self._loop.quit(), False)[1])
            return
        if event:
            self._emit(event, value)

    def _emit(self, event: str, value) -> None:
        if self._client:
            try:
                _send_message(self._client, {"event": event, "value": value})
            except OSError:
                self._disconnect_client()

    def _open_url(self, url: str) -> None:
        try:
            GLib.spawn_async(["xdg-open", url],
                             flags=GLib.SpawnFlags.SEARCH_PATH)
        except Exception as exc:  # noqa: BLE001
            print(f"[tray-dbus] xdg-open failed: {exc}", flush=True)

    def _check_parent_alive(self) -> bool:
        try:
            if os.getppid() != self._initial_ppid:
                print("[tray-dbus] parent daemon gone -- exiting", flush=True)
                self._loop.quit()
                return False
        except Exception:  # noqa: BLE001
            pass
        return True

    def run(self) -> None:
        try:
            self._loop.run()
        finally:
            self._disconnect_client()
            if self._server:
                try:
                    self._server.close()
                except OSError:
                    pass


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    TrayDBus().run()
