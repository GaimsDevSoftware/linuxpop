"""System tray icon (Ayatana AppIndicator) with a menu for LinuxPop."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Callable

import gi

ICON_DIR = str(Path(__file__).resolve().parent / "icons")
ICON_NAME = "linuxpop-tray-symbolic"  # monochrome icon for the panel
APP_ICON_NAME = "linuxpop"             # colored icon for dialogs / launcher

# Standard XDG user icon location — required for AppIndicator to find it via
# the GTK theme machinery on most desktops.
USER_ICON_DIR = Path.home() / ".local/share/icons/hicolor/scalable/apps"


def _ensure_icon_installed() -> None:
    """Install both icons (tray-symbolic + colored app) into XDG user icon dir.

    Also creates index.theme for hicolor if it doesn't exist (some user-level
    hicolor dirs are missing it, which prevents GTK from finding icons).
    """
    USER_ICON_DIR.mkdir(parents=True, exist_ok=True)
    for name in (ICON_NAME, APP_ICON_NAME):
        src = Path(ICON_DIR) / f"{name}.svg"
        if not src.is_file():
            continue
        dst = USER_ICON_DIR / f"{name}.svg"
        if not dst.is_file() or src.stat().st_mtime > dst.stat().st_mtime:
            try:
                shutil.copy2(src, dst)
                print(f"[tray] installed icon to {dst}")
            except OSError as exc:
                print(f"[tray] could not install {name}: {exc}")

    # Make sure hicolor has an index.theme; without it GTK lookup fails silently.
    hicolor_root = USER_ICON_DIR.parent.parent
    index_file = hicolor_root / "index.theme"
    if not index_file.is_file():
        try:
            index_file.write_text(
                "[Icon Theme]\nName=hicolor\nComment=Default icon theme\n"
                "Directories=scalable/apps\n\n"
                "[scalable/apps]\nSize=48\nMinSize=8\nMaxSize=512\n"
                "Type=Scalable\nContext=Applications\n",
                encoding="utf-8",
            )
            print(f"[tray] wrote {index_file}")
        except OSError as exc:
            print(f"[tray] could not write index.theme: {exc}")

gi.require_version("Gtk", "3.0")

# Prefer Ayatana (modern, used by Mint/Ubuntu), fall back to legacy AppIndicator3.
_INDICATOR_BACKEND = None
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
    _INDICATOR_BACKEND = "ayatana"
except (ImportError, ValueError):
    try:
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator  # noqa: E402
        _INDICATOR_BACKEND = "legacy"
    except (ImportError, ValueError):
        AppIndicator = None  # type: ignore

from gi.repository import Gtk  # noqa: E402


class Tray:
    def __init__(
        self,
        on_toggle_watcher: Callable[[bool], None],
        get_watcher_active: Callable[[], bool],
        on_show_popup_now: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_open_plugins: Callable[[], None],
        on_open_about: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._on_toggle_watcher = on_toggle_watcher
        self._get_watcher_active = get_watcher_active
        self._on_show_popup_now = on_show_popup_now
        self._on_open_settings = on_open_settings
        self._on_open_plugins = on_open_plugins
        self._on_open_about = on_open_about
        self._on_quit = on_quit
        self._indicator = None

        if AppIndicator is None:
            print("[tray] AppIndicator not available — tray icon disabled")
            return

        # Make sure the icon is in the standard XDG location so the panel
        # can resolve it by name through the icon theme.
        _ensure_icon_installed()
        installed_path = USER_ICON_DIR / f"{ICON_NAME}.svg"
        icon_to_use = ICON_NAME if installed_path.is_file() else "accessories-text-editor"

        self._indicator = AppIndicator.Indicator.new(
            "linuxpop",
            icon_to_use,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self._indicator.set_title("LinuxPop")
        self._indicator.set_menu(self._build_menu())
        print(f"[tray] AppIndicator started ({_INDICATOR_BACKEND}, icon={icon_to_use})")

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        header = Gtk.MenuItem(label="LinuxPop")
        header.set_sensitive(False)
        menu.append(header)
        menu.append(Gtk.SeparatorMenuItem())

        self._toggle_item = Gtk.CheckMenuItem(label="Auto-popup on selection")
        self._toggle_item.set_active(self._get_watcher_active())
        self._toggle_item.connect("toggled", self._on_toggle_clicked)
        menu.append(self._toggle_item)

        trigger = Gtk.MenuItem(label="Show popup now")
        trigger.connect("activate", lambda *_: self._on_show_popup_now())
        menu.append(trigger)

        menu.append(Gtk.SeparatorMenuItem())

        settings_item = Gtk.MenuItem(label="Settings…")
        settings_item.connect("activate", lambda *_: self._on_open_settings())
        menu.append(settings_item)

        plugins_item = Gtk.MenuItem(label="Plugins…")
        plugins_item.connect("activate", lambda *_: self._on_open_plugins())
        menu.append(plugins_item)

        about_item = Gtk.MenuItem(label="About LinuxPop")
        about_item.connect("activate", lambda *_: self._on_open_about())
        menu.append(about_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit LinuxPop")
        quit_item.connect("activate", lambda *_: self._on_quit())
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_toggle_clicked(self, widget) -> None:
        self._on_toggle_watcher(widget.get_active())

    def refresh(self) -> None:
        if self._indicator is None:
            return
        self._toggle_item.set_active(self._get_watcher_active())
