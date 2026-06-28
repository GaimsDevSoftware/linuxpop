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
from xdg_paths import CONFIG_DIR

_PLUGINS: List[Plugin] = []

# Module names of the user plugins loaded by the previous _load_user_plugins()
# run. Before each reload we call every one's optional unregister() hook so a
# plugin that starts a background thread (e.g. clipboard_history's watchers)
# can stop it - otherwise each reload, and one fires on every settings save,
# leaked another live thread.
_USER_MODULE_NAMES: set[str] = set()

USER_PLUGIN_DIR = CONFIG_DIR / "plugins"
LINUXPOP_DIR = str(Path(__file__).resolve().parent)
REPO_PLUGIN_DIR = Path(LINUXPOP_DIR) / "plugins_repo"
ICONS_DIR = Path(LINUXPOP_DIR) / "icons"
HICOLOR_APPS = Path.home() / ".local/share/icons/hicolor/scalable/apps"

# Curated set seeded into a brand-new install. Aim is "useful within
# 5 seconds of first selection" without overwhelming the popup. Dev-
# heavy plugins (base64, json_format, etc.) and niche transforms stay
# in the catalogue - they're one click away from Plugin Manager.
DEFAULT_PLUGIN_SEEDS = (
    "editing_actions.py",       # Cut / Paste / Backspace / Select All
    "clipboard_history.py",     # the core picker
    "wordcount.py",
    "large_type.py",
    "text_transformations.py",  # case + sort/dedupe/trim, all predicate-guarded
    "send_to_ai.py",
    "translate.py",             # in-place translation bubble, language picker
)
_PLUGIN_SEED_MARKER = USER_PLUGIN_DIR.parent / ".default-plugins-seeded"


def _ensure_on_path() -> None:
    """User plugins import classifier/plugin_base - make sure those resolve."""
    if LINUXPOP_DIR not in sys.path:
        sys.path.insert(0, LINUXPOP_DIR)


# Cache of (icons_dir_mtime, user_icons_dir_mtime) → (any_copied) from the
# previous _install_all_icons / _sync_user_icons run. Lets load_all() skip
# the whole icon stat/copy/rescan dance when the source dirs haven't
# changed -- which is the common case after first startup.
_icon_sync_cache: dict[str, float] = {}


def _dir_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _install_all_icons() -> None:
    """Copy every SVG from linuxpop/icons/ into the user's hicolor theme,
    register an extra search path with the running GTK process, and refresh
    the icon-theme cache so newly installed icons are findable.
    """
    if not ICONS_DIR.is_dir():
        return
    # Fast path: if neither the bundled icons dir nor the user icons dir
    # has been touched since the last run, skip the stat-storm + rescan.
    user_icons_dir = Path.home() / ".config/linuxpop/icons"
    bundled_mtime = _dir_mtime(ICONS_DIR)
    user_mtime = _dir_mtime(user_icons_dir)
    if (_icon_sync_cache.get("bundled") == bundled_mtime
            and _icon_sync_cache.get("user") == user_mtime
            and _icon_sync_cache.get("done")):
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
        # Rebuild on-disk cache so future GTK processes pick them up faster.
        # Cold-cache runs of gtk-update-icon-cache have been seen to take
        # 3-6 seconds on machines with large icon dirs - blocking the GTK
        # main thread during startup. Detach to a daemon thread; the
        # in-process icon theme has already been refreshed via the
        # rescan_if_needed() call above, so the daemon doesn't need this
        # to finish to function.
        import subprocess
        import threading as _threading
        def _bg_rebuild_cache() -> None:
            try:
                subprocess.run(
                    ["gtk-update-icon-cache", "-f", str(hicolor_root)],
                    check=False, capture_output=True, timeout=30,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
        _threading.Thread(
            target=_bg_rebuild_cache, daemon=True,
            name="linuxpop-icon-cache",
        ).start()

    # Mirror any user-supplied icons from ~/.config/linuxpop/icons/ into
    # hicolor so they're searchable in the picker and usable by recipes.
    _sync_user_icons()

    # Remember that we've completed an install pass with these source-dir
    # mtimes so subsequent load_all() calls can short-circuit.
    _icon_sync_cache["bundled"] = bundled_mtime
    _icon_sync_cache["user"]    = user_mtime
    _icon_sync_cache["done"]    = True  # type: ignore[assignment]


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


# Map plugin.name -> source file basename (e.g. "editing_actions.py").
# Populated by the file-loader so the Plugin Manager can show "which
# bundle does this come from", and so users can disable sub-plugins
# without removing the whole .py file.
_SOURCE_BY_NAME: dict[str, str] = {}


def register(plugin: Plugin) -> None:
    """Register a plugin. Silently drops it if its name appears in the
    `disabled_plugins` setting - lets users hide one sub-plugin from a
    bundled .py file (e.g. "select-all" from editing_actions) without
    deleting the whole bundle."""
    try:
        from settings import get_settings
        disabled = set(get_settings().get("disabled_plugins") or [])
    except Exception:
        disabled = set()
    if plugin.name in disabled:
        return
    _PLUGINS.append(plugin)


def all_plugins() -> List[Plugin]:
    return list(_PLUGINS)


def source_for(name: str) -> str | None:
    """Return the .py file that registered the plugin named `name`,
    or None for built-ins / unknown."""
    return _SOURCE_BY_NAME.get(name)


def plugins_by_source() -> dict[str, list[str]]:
    """Mapping of source-file basename to the list of plugin names it
    registered. Used by Plugin Manager → Installed to show a bundle as
    expandable with its sub-plugins listed underneath."""
    out: dict[str, list[str]] = {}
    for plugin_name, source in _SOURCE_BY_NAME.items():
        out.setdefault(source, []).append(plugin_name)
    return out


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


# Known plugin categories. A plugin sets Plugin.category to one of these keys;
# the popup collapses each group behind a chip (icon + label) that expands to
# its members. Order here is irrelevant - chips appear at the position of the
# group's first member, so plugin_order still controls placement.
CATEGORIES: dict[str, dict[str, str]] = {
    "format":   {"label": "Formatting", "icon": "linuxpop-format-symbolic"},
    "markdown": {"label": "Markdown",   "icon": "linuxpop-md-symbolic"},
}


def plan_grouped(plugins, *, group: bool, min_size: int,
                 categories: dict | None = None) -> list[tuple]:
    """Turn an ordered plugin list into a popup display plan.

    Returns a list of entries, preserving the incoming order:
      ("action",   plugin)
      ("category",  key, label, icon, [member plugins])

    A category collapses into one chip only when it has at least `min_size`
    members present; smaller groups stay inline as plain actions (no point
    hiding one button behind a chip). With group=False every plugin is an
    inline action, i.e. today's behaviour. Pure/GTK-free so it can be tested.
    """
    cats = CATEGORIES if categories is None else categories
    if not group:
        return [("action", p) for p in plugins]

    members: dict[str, list] = {}
    skeleton: list[tuple] = []
    for p in plugins:
        key = getattr(p, "category", None)
        if key and key in cats:
            if key not in members:
                members[key] = []
                skeleton.append(("catref", key))
            members[key].append(p)
        else:
            skeleton.append(("action", p))

    out: list[tuple] = []
    for entry in skeleton:
        if entry[0] != "catref":
            out.append(entry)
            continue
        key = entry[1]
        group_members = members[key]
        if len(group_members) >= max(2, min_size):
            meta = cats[key]
            out.append(("category", key, meta["label"], meta["icon"], group_members))
        else:
            out.extend(("action", m) for m in group_members)
    return out


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

    # Universal: pin the current selection as a snippet. Sits next to
    # Copy in the popup so the path from "I keep retyping this" to
    # "now it's a snippet" is one click long. Only registers when the
    # clipboard_history plugin is actually loaded - otherwise there's
    # nothing to save into.
    def _snippets_loaded() -> bool:
        import sys
        ch = (sys.modules.get("linuxpop_user_clipboard_history")
              or sys.modules.get("clipboard_history"))
        return ch is not None and hasattr(ch, "_create_snippet")
    register(Plugin(
        name="pin-as-snippet",
        icon="non-starred-symbolic",
        tooltip="Pin as snippet",
        handler=actions.pin_as_snippet,
        content_types=(),  # empty = all
        priority=11,
        predicate=lambda _t: _snippets_loaded(),
    ))

    # COMMAND - also shown for PATH-classified selections that look like
    # an executable script ('./build.sh', '~/bin/deploy'), via the
    # predicate. Lets users run a script with one click instead of having
    # to switch to a terminal and type the path.
    from classifier import is_runnable_path

    def _can_run(text: str) -> bool:
        # If classify() already returned COMMAND we wouldn't be here via
        # the PATH branch - for the COMMAND branch, the plugin's content_types
        # gate already matched. We only need to gate the PATH case.
        from classifier import classify as _classify
        ct = _classify(text)
        if ct == ContentType.COMMAND:
            return True
        if ct == ContentType.PATH:
            return is_runnable_path(text)
        return False

    register(Plugin(
        name="run-terminal",
        icon="utilities-terminal-symbolic",
        tooltip="Run in terminal",
        handler=actions.run_in_terminal,
        content_types=(ContentType.COMMAND, ContentType.PATH),
        priority=20,
        predicate=_can_run,
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
        icon="mail-message-new-symbolic",
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


def _seed_default_plugins() -> None:
    """First-run only: drop the curated DEFAULT_PLUGIN_SEEDS into the
    user's plugin dir so a fresh install ships with a useful popup
    out of the box. Skipped if the seed marker is present OR the dir
    already has any .py files (a user who curated their own set
    should not get clobbered)."""
    if _PLUGIN_SEED_MARKER.is_file():
        return
    USER_PLUGIN_DIR.mkdir(parents=True, exist_ok=True)
    has_existing = any(USER_PLUGIN_DIR.glob("*.py"))
    if has_existing:
        _PLUGIN_SEED_MARKER.touch()
        return
    if not REPO_PLUGIN_DIR.is_dir():
        _PLUGIN_SEED_MARKER.touch()
        return
    for filename in DEFAULT_PLUGIN_SEEDS:
        src = REPO_PLUGIN_DIR / filename
        if not src.is_file():
            continue
        dst = USER_PLUGIN_DIR / filename
        if dst.is_file():
            continue
        try:
            shutil.copy2(src, dst)
            print(f"[plugin_loader] seeded default plugin: {filename}")
        except OSError as exc:
            print(f"[plugin_loader] could not seed {filename}: {exc}")
    _PLUGIN_SEED_MARKER.touch()


def _teardown_user_plugins() -> None:
    """Call the optional unregister() hook on every user plugin loaded last
    time, so its background threads stop before we re-import. Also catches
    plugins whose file was removed since the previous load."""
    for mod_name in _USER_MODULE_NAMES:
        hook = getattr(sys.modules.get(mod_name), "unregister", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                print(f"[plugin_loader] {mod_name}.unregister() failed:")
                traceback.print_exc()
    _USER_MODULE_NAMES.clear()


def _load_user_plugins() -> None:
    _ensure_on_path()
    _seed_default_plugins()
    _teardown_user_plugins()
    if not USER_PLUGIN_DIR.is_dir():
        return
    for path in sorted(USER_PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        mod_name = f"linuxpop_user_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec - Python 3.12 dataclass
            # introspection needs cls.__module__ to resolve.
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
            # Track it so the next reload can call its unregister() hook.
            _USER_MODULE_NAMES.add(mod_name)
            if hasattr(module, "register"):
                # Wrap register so we can track which source file each
                # plugin came from - the Plugin Manager uses this to
                # show a bundle as expandable with its sub-plugins
                # underneath, and per-action enable/disable.
                source_basename = path.name

                def tracked_register(p, src=source_basename):
                    _SOURCE_BY_NAME[p.name] = src
                    return register(p)

                module.register(tracked_register)
                print(f"[plugin_loader] loaded user plugin: {path.name}")
        except Exception:
            # Roll back the half-initialised module so main.py's
            # sys.modules.get("linuxpop_user_clipboard_history") doesn't
            # find a broken stub and silently behave as if the plugin
            # were loaded. Next load_all() then gets a clean re-import.
            sys.modules.pop(mod_name, None)
            print(f"[plugin_loader] failed to load {path.name}:")
            traceback.print_exc()


def load_all() -> None:
    """Idempotent: clears existing registry and reloads everything."""
    _PLUGINS.clear()
    _SOURCE_BY_NAME.clear()
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
