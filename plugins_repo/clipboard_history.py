"""Clipboard history + snippets picker.

Two backing stores:
  - history.json   : rolling buffer of the last N clipboard entries
  - snippets.json  : pinned/named entries that never expire

The picker dialog has Recent + Snippets tabs, a search box, and pastes
the chosen entry at the cursor of whichever app had focus when the
picker was summoned (via xdotool windowactivate + ctrl+v).

Activated either from the popup ("Clipboard…" plugin button) or from
the global clipboard_hotkey (default Super+V).

Settings (~/.config/linuxpop/settings.json):
  "clipboard_history_size":   25
  "clipboard_history_images": true
  "clipboard_hotkey":         "super+v"   (handled by main.py)
"""
from __future__ import annotations

import json
import os
import select as _select
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Callable, List, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

try:
    gi.require_version("GdkX11", "3.0")
    from gi.repository import GdkX11  # noqa: F401, E402
except (ImportError, ValueError):
    pass


def _force_to_front(window) -> None:
    """Reliably raise AND focus the picker on X11. Keep_above stays on
    so the picker doesn't slip behind a Settings window. Previously we
    also added WindowTypeHint.UTILITY here -- removed, because Cinnamon
    treats UTILITY windows as auxiliary panels that don't fully take
    keyboard focus, which empirically caused 'buttons don't work,
    scrolling doesn't work' freezes where the picker appeared but
    refused all input."""
    try:
        window.deiconify()
        window.set_accept_focus(True)
        window.set_focus_on_map(True)
        gdk_win = window.get_window()
        if gdk_win is not None:
            try:
                ts = Gdk.X11.get_server_time(gdk_win)
            except Exception:
                ts = Gtk.get_current_event_time() or 0
            window.present_with_time(ts)
            # The stronger X11-level focus request. present_with_time
            # asks the WM to raise; .focus() demands keyboard focus.
            try:
                gdk_win.focus(ts)
            except Exception:
                pass
        else:
            window.present()
        # Permanent keep-above while the picker is visible. Removed in
        # _on_destroy → no-op since the window is already gone.
        window.set_keep_above(True)
    except Exception:
        try:
            window.present()
        except Exception:
            pass


from classifier import ContentType
from plugin_base import Plugin

try:
    from settings import get_settings
    _settings = get_settings()
except Exception:
    _settings = None


def _cfg(key: str, default):
    if _settings is None:
        return default
    val = _settings.get(key, None)
    return val if val is not None else default


HISTORY_SIZE = max(1, int(_cfg("clipboard_history_size", 25)))
CAPTURE_IMAGES = bool(_cfg("clipboard_history_images", True))
POLL_INTERVAL = max(0.1, float(_cfg("clipboard_poll_interval", 0.6)))

CACHE_DIR = Path(os.path.expanduser("~/.cache/linuxpop/clipboard"))
HISTORY_FILE = CACHE_DIR / "history.json"
SNIPPETS_FILE = Path(os.path.expanduser("~/.config/linuxpop/snippets.json"))
IMAGES_DIR = CACHE_DIR / "images"


# ----- data --------------------------------------------------------------

@dataclass
class Entry:
    id: str
    timestamp: float
    kind: str         # "text" | "image"
    text: str = ""
    image_path: str = ""
    name: str = ""    # only used for snippets

    def preview(self, max_len: int = 80) -> str:
        if self.name:
            return self.name
        if self.kind == "image":
            return f"🖼  Image — {Path(self.image_path).name}"
        s = self.text.replace("\n", "↵").replace("\t", "  ")
        return s[:max_len] + "…" if len(s) > max_len else s

    def search_haystack(self) -> str:
        return f"{self.name}\n{self.text}\n{self.image_path}".lower()


_history: List[Entry] = []
_snippets: List[Entry] = []
_history_lock = threading.Lock()
_snippets_lock = threading.Lock()
_watcher_thread: Optional[threading.Thread] = None
_watcher_stop = threading.Event()


# ----- persistence -------------------------------------------------------

def _atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def _load_list(path: Path, target: List[Entry]) -> None:
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for item in data:
            try:
                target.append(Entry(**item))
            except TypeError:
                continue
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[clipboard] could not load {path}: {exc}")


def _save_history() -> None:
    with _history_lock:
        data = [asdict(e) for e in _history]
    try:
        _atomic_write_json(HISTORY_FILE, data)
    except OSError as exc:
        print(f"[clipboard] could not save history: {exc}")


def _save_snippets() -> None:
    with _snippets_lock:
        data = [asdict(e) for e in _snippets]
    try:
        _atomic_write_json(SNIPPETS_FILE, data)
    except OSError as exc:
        print(f"[clipboard] could not save snippets: {exc}")


def _add_entry(entry: Entry) -> None:
    with _history_lock:
        if _history and _history[0].kind == entry.kind:
            if entry.kind == "text" and _history[0].text == entry.text:
                return
            if entry.kind == "image" and _history[0].image_path == entry.image_path:
                return
        _history.insert(0, entry)
        while len(_history) > HISTORY_SIZE:
            old = _history.pop()
            if old.kind == "image" and old.image_path:
                try:
                    Path(old.image_path).unlink(missing_ok=True)
                except OSError:
                    pass
    _save_history()


def _pin_entry(entry: Entry, name: str = "") -> None:
    """Copy a history entry into the persistent snippets store."""
    new_id = uuid.uuid4().hex[:12]
    snippet = Entry(
        id=new_id,
        timestamp=time.time(),
        kind=entry.kind,
        text=entry.text,
        image_path=entry.image_path,
        name=name,
    )
    with _snippets_lock:
        _snippets.insert(0, snippet)
    _save_snippets()


def _unpin_snippet(snippet_id: str) -> None:
    with _snippets_lock:
        _snippets[:] = [s for s in _snippets if s.id != snippet_id]
    _save_snippets()


def _rename_snippet(snippet_id: str, new_name: str) -> None:
    with _snippets_lock:
        for s in _snippets:
            if s.id == snippet_id:
                s.name = new_name
                break
    _save_snippets()


# ----- clipboard reading -------------------------------------------------

def _read_clipboard_targets() -> list[str]:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, timeout=0.5,
        )
        return out.stdout.decode("utf-8", errors="replace").splitlines()
    except (OSError, subprocess.SubprocessError):
        return []


def _read_clipboard_text() -> str:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-o"],
            capture_output=True, timeout=0.5,
        )
        return out.stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_clipboard_image() -> Optional[bytes]:
    try:
        out = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            capture_output=True, timeout=1.0,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


_state = {"last_text": "", "last_image_hash": b""}

# Persistent dedup-hash file. Without this, every daemon restart with
# an image still on the clipboard re-saves it as a "new" entry.
_DEDUP_STATE_FILE = CACHE_DIR / "last_image.sha1"


def _load_dedup_state() -> None:
    """Restore the last seen clipboard image hash from disk so we don't
    re-capture the same image just because the daemon was restarted."""
    try:
        _state["last_image_hash"] = _DEDUP_STATE_FILE.read_bytes()
    except OSError:
        pass  # first run or missing -- fine


def _save_dedup_state(h: bytes) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _DEDUP_STATE_FILE.write_bytes(h)
    except OSError:
        pass


def _sweep_orphan_images() -> None:
    """Delete image files in the cache that are not referenced by any
    history or snippet entry. Cleans up files left behind by older
    daemon runs whose entries have since been bumped out of history."""
    if not IMAGES_DIR.is_dir():
        return
    referenced: set[str] = set()
    with _history_lock:
        for e in _history:
            if e.kind == "image" and e.image_path:
                referenced.add(Path(e.image_path).name)
    with _snippets_lock:
        for e in _snippets:
            if e.kind == "image" and e.image_path:
                referenced.add(Path(e.image_path).name)
    removed = 0
    for p in IMAGES_DIR.glob("*.png"):
        if p.name not in referenced:
            try:
                p.unlink()
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"[clipboard] swept {removed} orphan image(s) from cache")


def _capture_current_clipboard() -> None:
    try:
        targets = _read_clipboard_targets()
        has_image = CAPTURE_IMAGES and any(t.startswith("image/") for t in targets)
        has_text = any(t in ("UTF8_STRING", "TEXT", "STRING", "text/plain",
                              "text/plain;charset=utf-8") for t in targets)
        if has_image:
            data = _read_clipboard_image()
            if data:
                import hashlib
                h = hashlib.sha1(data).digest()
                if h != _state["last_image_hash"]:
                    _state["last_image_hash"] = h
                    _save_dedup_state(h)
                    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                    eid = uuid.uuid4().hex[:12]
                    path = IMAGES_DIR / f"{eid}.png"
                    try:
                        path.write_bytes(data)
                        _add_entry(Entry(
                            id=eid, timestamp=time.time(), kind="image",
                            image_path=str(path),
                        ))
                    except OSError as exc:
                        print(f"[clipboard] could not save image: {exc}")
        elif has_text:
            text = _read_clipboard_text()
            if text and text != _state["last_text"] and text.strip():
                _state["last_text"] = text
                _add_entry(Entry(
                    id=uuid.uuid4().hex[:12], timestamp=time.time(),
                    kind="text", text=text,
                ))
    except Exception as exc:  # noqa: BLE001
        print(f"[clipboard] capture error: {exc}")


def _watcher_loop() -> None:
    try:
        from Xlib import display
        from Xlib.ext import xfixes
    except ImportError:
        _poll_only_loop()
        return
    try:
        dpy = display.Display()
        dpy.xfixes_query_version()
        root = dpy.screen().root
        clipboard_atom = dpy.intern_atom("CLIPBOARD")
        dpy.xfixes_select_selection_input(
            root, clipboard_atom, xfixes.XFixesSetSelectionOwnerNotifyMask,
        )
        dpy.flush()
    except Exception as exc:  # noqa: BLE001
        print(f"[clipboard] XFixes unavailable ({exc}); polling")
        _poll_only_loop()
        return

    print("[clipboard] using XFixes selection events (CLIPBOARD)")
    fd = dpy.fileno()
    try:
        _capture_current_clipboard()
        while not _watcher_stop.is_set():
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
                time.sleep(0.05)
                _capture_current_clipboard()
    finally:
        try:
            dpy.close()
        except Exception:
            pass


def _poll_only_loop() -> None:
    while not _watcher_stop.is_set():
        _capture_current_clipboard()
        time.sleep(POLL_INTERVAL)


def _start_watcher_once() -> None:
    global _watcher_thread
    if _watcher_thread is not None and _watcher_thread.is_alive():
        return
    _load_list(HISTORY_FILE, _history)
    _load_list(SNIPPETS_FILE, _snippets)
    _load_dedup_state()       # keep dedup hash across daemon restarts
    _sweep_orphan_images()    # delete cached images no entry refers to
    _watcher_stop.clear()
    _watcher_thread = threading.Thread(
        target=_watcher_loop, daemon=True, name="linuxpop-clipboard",
    )
    _watcher_thread.start()
    print(f"[clipboard] watcher started (size={HISTORY_SIZE}, "
          f"images={CAPTURE_IMAGES}, snippets={len(_snippets)})")


# ----- paste-at-cursor ---------------------------------------------------

def _get_active_window() -> str | None:
    if not shutil.which("xdotool"):
        return None
    try:
        out = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=0.5,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _paste_to_window(entry: Entry, target_window: str | None) -> None:
    """Put the entry on the clipboard, focus the target window, send Ctrl+V."""
    if entry.kind == "text":
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=entry.text.encode("utf-8"), check=False,
        )
    elif entry.kind == "image":
        if not Path(entry.image_path).is_file():
            return
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "image/png",
             "-i", entry.image_path], check=False,
        )

    def worker():
        if target_window and shutil.which("xdotool"):
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", target_window],
                check=False,
            )
            time.sleep(0.1)  # let focus settle
            subprocess.run(
                ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                check=False,
            )

    threading.Thread(target=worker, daemon=True, name="clipboard-paste").start()


# ----- picker dialog -----------------------------------------------------

class _PickerDialog:
    def __init__(self) -> None:
        self.dialog: Optional[Gtk.Window] = None
        self.target_window: str | None = None
        self.search_entry: Optional[Gtk.SearchEntry] = None
        self.notebook: Optional[Gtk.Notebook] = None
        self.recent_listbox: Optional[Gtk.ListBox] = None
        self.snippets_listbox: Optional[Gtk.ListBox] = None
        self._filter_text = ""

    def show(self, target_window: str | None = None) -> None:
        # Stamp each phase via the real logger (not print) so a UI freeze
        # leaves breadcrumbs in ~/.cache/linuxpop/linuxpop.log. Times
        # > 100 ms at any phase would explain a perceived freeze.
        import logging
        _log = logging.getLogger("linuxpop")
        t0 = time.monotonic()
        def _stamp(label):
            _log.info("[clipboard.show] %s: %.0f ms",
                      label, (time.monotonic()-t0)*1000)
        _stamp("entered show()")

        if self.dialog is not None and self.dialog.get_visible():
            _force_to_front(self.dialog)
            _stamp("reused existing")
            return
        self.target_window = target_window

        win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        win.set_title("LinuxPop — Clipboard & Snippets")
        win.set_default_size(580, 540)
        win.set_position(Gtk.WindowPosition.CENTER)
        win.set_icon_name("linuxpop")
        win.set_skip_taskbar_hint(True)
        win.set_keep_above(True)
        win.connect("destroy", self._on_destroy)
        win.connect("key-press-event", self._on_key_press)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        outer.set_margin_start(8)
        outer.set_margin_end(8)

        # Search
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Type to filter…")
        self.search_entry.connect("search-changed", self._on_search_changed)
        outer.pack_start(self.search_entry, False, False, 0)

        # Tabs
        self.notebook = Gtk.Notebook()
        self.notebook.set_margin_top(6)

        # Recent tab
        recent_scroll = Gtk.ScrolledWindow()
        recent_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.recent_listbox = Gtk.ListBox()
        self.recent_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.recent_listbox.set_filter_func(self._row_filter)
        self.recent_listbox.connect("row-activated", self._on_row_activated)
        recent_scroll.add(self.recent_listbox)
        self.notebook.append_page(recent_scroll, Gtk.Label(label="Recent"))

        # Snippets tab
        snippets_scroll = Gtk.ScrolledWindow()
        snippets_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.snippets_listbox = Gtk.ListBox()
        self.snippets_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.snippets_listbox.set_filter_func(self._row_filter)
        self.snippets_listbox.connect("row-activated", self._on_row_activated)
        snippets_scroll.add(self.snippets_listbox)
        self.notebook.append_page(snippets_scroll, Gtk.Label(label="Snippets"))

        outer.pack_start(self.notebook, True, True, 0)

        # Hint
        hint = Gtk.Label(xalign=0, margin_top=6)
        hint.set_markup(
            "<small>Enter to paste at cursor · Ctrl+P pin · Ctrl+R rename · "
            "Esc to close</small>"
        )
        outer.pack_start(hint, False, False, 0)

        win.add(outer)
        _stamp("built widgets")
        self._populate_recent()
        _stamp("populated recent")
        self._populate_snippets()
        _stamp("populated snippets")
        win.show_all()
        _stamp("show_all")
        _force_to_front(win)
        _stamp("force_to_front")
        # Defer search-entry focus until AFTER the WM has had a chance to
        # process our focus request — calling grab_focus immediately after
        # show_all races the FocusIn event and silently fails.
        if self.search_entry is not None:
            GLib.idle_add(self._grab_search_focus)
        self.dialog = win

    def _grab_search_focus(self) -> bool:
        if self.search_entry is not None and self.dialog is not None:
            self.search_entry.grab_focus()
        return False  # one-shot

    def _on_destroy(self, *_):
        import logging
        logging.getLogger("linuxpop").info(
            "[clipboard.show] window destroyed -- state cleared")
        self.dialog = None
        self.search_entry = None
        self.notebook = None
        self.recent_listbox = None
        self.snippets_listbox = None
        self._filter_text = ""

    # ---- population ----

    def _populate_recent(self) -> None:
        if self.recent_listbox is None:
            return
        for child in list(self.recent_listbox.get_children()):
            self.recent_listbox.remove(child)
        with _history_lock:
            entries = list(_history)
        if not entries:
            self.recent_listbox.add(self._empty_row("History is empty",
                                                   "Copy something — it appears here."))
        else:
            for entry in entries:
                self.recent_listbox.add(self._make_row(entry, is_snippet=False))
        self.recent_listbox.show_all()

    def _populate_snippets(self) -> None:
        if self.snippets_listbox is None:
            return
        for child in list(self.snippets_listbox.get_children()):
            self.snippets_listbox.remove(child)
        with _snippets_lock:
            entries = list(_snippets)
        if not entries:
            self.snippets_listbox.add(self._empty_row(
                "No snippets yet",
                "Pin (★) an entry from Recent to save it here permanently."
            ))
        else:
            for entry in entries:
                self.snippets_listbox.add(self._make_row(entry, is_snippet=True))
        self.snippets_listbox.show_all()

    def _empty_row(self, title: str, subtitle: str) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_selectable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        t = Gtk.Label(xalign=0.5)
        t.set_markup(f"<b>{GLib.markup_escape_text(title)}</b>")
        s = Gtk.Label(label=subtitle, xalign=0.5, wrap=True)
        s.get_style_context().add_class("dim-label")
        box.pack_start(t, False, False, 0)
        box.pack_start(s, False, False, 0)
        row.add(box)
        return row

    def _make_row(self, entry: Entry, is_snippet: bool) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.entry = entry  # type: ignore[attr-defined]
        row.is_snippet = is_snippet  # type: ignore[attr-defined]

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin=6)

        # Thumbnail / icon
        if entry.kind == "image" and Path(entry.image_path).is_file():
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    entry.image_path, 48, 48, True,
                )
                img = Gtk.Image.new_from_pixbuf(pix)
            except Exception:
                img = Gtk.Image.new_from_icon_name(
                    "image-x-generic-symbolic", Gtk.IconSize.LARGE_TOOLBAR,
                )
        else:
            icon_name = "starred-symbolic" if is_snippet else "edit-paste-symbolic"
            img = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        hbox.pack_start(img, False, False, 0)

        # Text
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{GLib.markup_escape_text(entry.preview())}</b>")
        title.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        title.set_max_width_chars(60)
        vbox.pack_start(title, False, False, 0)

        if is_snippet and entry.name:
            # Show a preview of contents under the name
            preview = entry.text[:80] if entry.kind == "text" else entry.image_path
            sub = Gtk.Label(xalign=0)
            sub.set_markup(f"<small><span foreground='#888'>"
                            f"{GLib.markup_escape_text(preview)}</span></small>")
            sub.set_ellipsize(3)
            sub.set_max_width_chars(70)
            vbox.pack_start(sub, False, False, 0)
        else:
            meta = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
            if entry.kind == "text":
                meta += f"  ·  {len(entry.text)} chars"
            sub = Gtk.Label(xalign=0)
            sub.set_markup(f"<small><span foreground='#888'>{meta}</span></small>")
            vbox.pack_start(sub, False, False, 0)
        hbox.pack_start(vbox, True, True, 0)

        # Action buttons
        if is_snippet:
            rename_btn = Gtk.Button.new_from_icon_name(
                "document-edit-symbolic", Gtk.IconSize.BUTTON,
            )
            rename_btn.set_valign(Gtk.Align.CENTER)
            rename_btn.set_tooltip_text("Rename")
            rename_btn.connect("clicked", self._on_rename_clicked, entry)
            hbox.pack_end(rename_btn, False, False, 0)

            unpin_btn = Gtk.Button.new_from_icon_name(
                "starred-symbolic", Gtk.IconSize.BUTTON,
            )
            unpin_btn.set_valign(Gtk.Align.CENTER)
            unpin_btn.set_tooltip_text("Unpin")
            unpin_btn.connect("clicked", self._on_unpin_clicked, entry)
            hbox.pack_end(unpin_btn, False, False, 0)
        else:
            pin_btn = Gtk.Button.new_from_icon_name(
                "non-starred-symbolic", Gtk.IconSize.BUTTON,
            )
            pin_btn.set_valign(Gtk.Align.CENTER)
            pin_btn.set_tooltip_text("Pin as snippet (also lets you name it)")
            pin_btn.connect("clicked", self._on_pin_clicked, entry)
            hbox.pack_end(pin_btn, False, False, 0)

        row.add(hbox)
        return row

    # ---- filter ----

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._filter_text = entry.get_text().strip().lower()
        if self.recent_listbox:
            self.recent_listbox.invalidate_filter()
        if self.snippets_listbox:
            self.snippets_listbox.invalidate_filter()

    def _row_filter(self, row: Gtk.ListBoxRow) -> bool:
        if not self._filter_text:
            return True
        entry = getattr(row, "entry", None)
        if entry is None:
            return False
        return self._filter_text in entry.search_haystack()

    # ---- actions ----

    def _on_row_activated(self, _listbox, row) -> None:
        entry = getattr(row, "entry", None)
        if entry is not None:
            self._paste_and_close(entry)

    def _paste_and_close(self, entry: Entry) -> None:
        target = self.target_window  # captured before we showed
        if self.dialog is not None:
            self.dialog.destroy()
        _paste_to_window(entry, target)

    def _on_pin_clicked(self, _btn, entry: Entry) -> None:
        name = self._ask_for_name(default=entry.preview(40))
        if name is None:
            return  # cancelled
        _pin_entry(entry, name)
        self._populate_snippets()
        # Switch to snippets tab so user sees the result
        if self.notebook is not None:
            self.notebook.set_current_page(1)

    def _on_unpin_clicked(self, _btn, entry: Entry) -> None:
        _unpin_snippet(entry.id)
        self._populate_snippets()

    def _on_rename_clicked(self, _btn, entry: Entry) -> None:
        new_name = self._ask_for_name(default=entry.name or entry.preview(40))
        if new_name is None:
            return
        _rename_snippet(entry.id, new_name)
        self._populate_snippets()

    def _ask_for_name(self, default: str = "") -> str | None:
        dlg = Gtk.Dialog(title="Snippet name", transient_for=self.dialog, flags=0)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                        "OK", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(default)
        entry.set_activates_default(True)
        entry.set_margin_top(8)
        entry.set_margin_bottom(8)
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dlg.get_content_area().add(entry)
        dlg.show_all()
        response = dlg.run()
        value = entry.get_text().strip()
        dlg.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        return value

    # ---- keyboard ----

    def _on_key_press(self, _widget, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            if self.dialog is not None:
                self.dialog.destroy()
            return True
        # Ctrl+P → pin currently selected (if in Recent)
        if (event.state & Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_p):
            self._action_on_selected("pin")
            return True
        # Ctrl+R → rename (if in Snippets)
        if (event.state & Gdk.ModifierType.CONTROL_MASK
                and event.keyval == Gdk.KEY_r):
            self._action_on_selected("rename")
            return True
        return False

    def _action_on_selected(self, action: str) -> None:
        if self.notebook is None:
            return
        page = self.notebook.get_current_page()
        listbox = self.recent_listbox if page == 0 else self.snippets_listbox
        if listbox is None:
            return
        row = listbox.get_selected_row()
        if row is None:
            return
        entry = getattr(row, "entry", None)
        if entry is None:
            return
        if action == "pin" and page == 0:
            self._on_pin_clicked(None, entry)
        elif action == "rename" and page == 1:
            self._on_rename_clicked(None, entry)


_picker = _PickerDialog()


def open_picker(target_window: str | None = None) -> None:
    """Module-level entry point used by both the popup plugin button and
    the global clipboard hotkey in main.py."""
    GLib.idle_add(_picker.show, target_window)


# ----- plugin registration ----------------------------------------------

def _open_from_popup(_text: str) -> None:
    # When invoked from the popup, capture the previously focused window
    # before opening the picker so paste-at-cursor still works.
    target = _get_active_window()
    open_picker(target)


def register(register_plugin) -> None:
    # Honour the master kill-switch so the user can turn the entire
    # clipboard plugin off (no watcher thread, no hotkey, no popup
    # button) without having to uninstall the file.
    if not bool(_cfg("clipboard_history_enabled", True)):
        print("[clipboard] disabled in settings — not registering")
        return
    _start_watcher_once()
    register_plugin(Plugin(
        name="clipboard-history",
        icon="linuxpop-clipboard-symbolic",
        tooltip="Clipboard & snippets",
        handler=_open_from_popup,
        content_types=(),  # always available
        priority=5,
    ))
