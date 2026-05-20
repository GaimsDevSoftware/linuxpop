"""Loads built-in and user-defined plugins.

User plugins are Python files in ~/.config/linuxpop/plugins/ that expose
a top-level `register(register_plugin)` function. Each plugin file may
register one or more Plugin instances.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import List

import actions
from classifier import ContentType
from plugin_base import Plugin

_PLUGINS: List[Plugin] = []

USER_PLUGIN_DIR = Path(os.path.expanduser("~/.config/linuxpop/plugins"))
LINUXPOP_DIR = str(Path(__file__).resolve().parent)
ICONS_DIR = Path(LINUXPOP_DIR) / "icons"
HICOLOR_APPS = Path.home() / ".local/share/icons/hicolor/scalable/apps"


def _ensure_on_path() -> None:
    """User plugins import classifier/plugin_base — make sure those resolve."""
    if LINUXPOP_DIR not in sys.path:
        sys.path.insert(0, LINUXPOP_DIR)


def _install_all_icons() -> None:
    """Copy every SVG from linuxpop/icons/ into the user's hicolor theme,
    register an extra search path with the running GTK process, and refresh
    the icon-theme cache so newly installed icons are findable.
    """
    if not ICONS_DIR.is_dir():
        return
    HICOLOR_APPS.mkdir(parents=True, exist_ok=True)
    copied_any = False
    for src in ICONS_DIR.glob("*.svg"):
        dst = HICOLOR_APPS / src.name
        if not dst.is_file() or src.stat().st_mtime > dst.stat().st_mtime:
            try:
                shutil.copy2(src, dst)
                copied_any = True
            except OSError as exc:
                print(f"[plugin_loader] icon copy failed for {src.name}: {exc}")

    # Ensure hicolor has an index.theme so GTK actually scans it
    hicolor_root = HICOLOR_APPS.parent.parent
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
            copied_any = True
        except OSError:
            pass

    # Tell the running GTK process about our icon dir AND refresh the cache.
    # Without this, GTK has already scanned its theme dirs before we copied,
    # and falls back to the "image-missing" icon for our linuxpop-* names.
    try:
        from gi.repository import Gtk
        theme = Gtk.IconTheme.get_default()
        if theme is not None:
            theme.append_search_path(str(ICONS_DIR))
            theme.rescan_if_needed()
    except Exception as exc:  # noqa: BLE001
        print(f"[plugin_loader] could not refresh icon theme: {exc}")

    if copied_any:
        # Rebuild on-disk cache so future GTK processes pick them up faster
        try:
            import subprocess
            subprocess.run(
                ["gtk-update-icon-cache", "-f", str(hicolor_root)],
                check=False, capture_output=True,
            )
        except FileNotFoundError:
            pass

    # Mirror any user-supplied icons from ~/.config/linuxpop/icons/ into
    # hicolor so they're searchable in the picker and usable by recipes.
    _sync_user_icons()


def _sync_user_icons() -> None:
    user_icons_dir = Path.home() / ".config/linuxpop/icons"
    user_icons_dir.mkdir(parents=True, exist_ok=True)
    user_copied = False
    for src in list(user_icons_dir.glob("*.svg")) + list(user_icons_dir.glob("*.png")):
        dst = HICOLOR_APPS / src.name
        if not dst.is_file() or src.stat().st_mtime > dst.stat().st_mtime:
            try:
                shutil.copy2(src, dst)
                user_copied = True
            except OSError as exc:
                print(f"[plugin_loader] user icon copy failed for {src.name}: {exc}")
    if user_copied:
        try:
            from gi.repository import Gtk
            theme = Gtk.IconTheme.get_default()
            if theme is not None:
                theme.rescan_if_needed()
        except Exception:
            pass
        print(f"[plugin_loader] synced user icons from {user_icons_dir}")


def register(plugin: Plugin) -> None:
    _PLUGINS.append(plugin)


def all_plugins() -> List[Plugin]:
    return list(_PLUGINS)


def for_content_type(content_type: ContentType, text: str | None = None) -> List[Plugin]:
    matched = [p for p in _PLUGINS if p.handles(content_type)]
    if text is not None:
        matched = [p for p in matched if p.matches(text)]
    # User-defined order takes precedence; plugins listed earlier come first.
    # Plugins not in the order list fall back to their built-in priority.
    try:
        from settings import get_settings
        order = list(get_settings().get("plugin_order") or [])
    except Exception:
        order = []
    order_index = {name: i for i, name in enumerate(order)}
    big = len(order) + 1_000_000

    def sort_key(p: Plugin):
        if p.name in order_index:
            return (0, order_index[p.name], p.priority)
        return (1, big, p.priority)

    matched.sort(key=sort_key)
    return matched


def _register_builtins() -> None:
    # Universal: copy works on anything
    register(Plugin(
        name="copy",
        icon="edit-copy-symbolic",
        tooltip="Copy",
        handler=actions.copy_to_clipboard,
        content_types=(),  # empty = all
        priority=10,
    ))

    # COMMAND
    register(Plugin(
        name="run-terminal",
        icon="utilities-terminal-symbolic",
        tooltip="Run in terminal",
        handler=actions.run_in_terminal,
        content_types=(ContentType.COMMAND,),
        priority=20,
    ))

    # URL
    register(Plugin(
        name="open-url",
        icon="web-browser-symbolic",
        tooltip="Open in browser",
        handler=actions.open_url,
        content_types=(ContentType.URL,),
        priority=20,
    ))

    # EMAIL
    register(Plugin(
        name="compose-email",
        icon="mail-send-symbolic",
        tooltip="Compose email",
        handler=actions.compose_email,
        content_types=(ContentType.EMAIL,),
        priority=20,
    ))

    # PATH
    register(Plugin(
        name="open-path",
        icon="folder-open-symbolic",
        tooltip="Open path",
        handler=actions.open_path,
        content_types=(ContentType.PATH,),
        priority=20,
    ))

    # PLAIN_TEXT: search
    register(Plugin(
        name="search-web",
        icon="system-search-symbolic",
        tooltip="Search the web",
        handler=actions.search_web,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=30,
    ))


def _load_user_plugins() -> None:
    _ensure_on_path()
    if not USER_PLUGIN_DIR.is_dir():
        return
    for path in sorted(USER_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            mod_name = f"linuxpop_user_{path.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec — Python 3.12 dataclass
            # introspection needs cls.__module__ to resolve.
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            if hasattr(module, "register"):
                module.register(register)
                print(f"[plugin_loader] loaded user plugin: {path.name}")
        except Exception:
            print(f"[plugin_loader] failed to load {path.name}:")
            traceback.print_exc()


def load_all() -> None:
    """Idempotent: clears existing registry and reloads everything."""
    _PLUGINS.clear()
    _install_all_icons()
    _register_builtins()
    _load_user_plugins()
    _load_recipes()
    print(f"[plugin_loader] {len(_PLUGINS)} plugins loaded")


def _load_recipes() -> None:
    try:
        import recipe_loader
        recipe_loader.load_recipes(register)
    except Exception as exc:  # noqa: BLE001
        print(f"[plugin_loader] recipe loading failed: {exc}")
