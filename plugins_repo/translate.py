"""Translate the selection in place - no browser, no Google Translate tab.

Clicking the Translate button sends the selected text to Google's keyless
`translate_a/single` endpoint and shows the result in a small bubble that
floats over the selection. The bubble has a language picker so you can
change the target language on the fly; the choice is remembered in
settings (`translate_target_lang`). A Copy button puts the translation on
the clipboard.

No API key, no window switch. Falls back to a desktop notification if the
network call fails.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from plugin_base import Plugin

# (code, human label). Kept short so the dropdown stays compact. "auto" is
# only ever the *source* (detected), never a target.
_LANGS = [
    ("en", "English"), ("no", "Norsk"), ("sv", "Svenska"), ("da", "Dansk"),
    ("de", "Deutsch"), ("fr", "Français"), ("es", "Español"),
    ("it", "Italiano"), ("nl", "Nederlands"), ("pt", "Português"),
    ("pl", "Polski"), ("ru", "Русский"), ("uk", "Українська"),
    ("fi", "Suomi"), ("is", "Íslenska"), ("cs", "Čeština"),
    ("tr", "Türkçe"), ("ar", "العربية"), ("hi", "हिन्दी"),
    ("zh-CN", "中文"), ("ja", "日本語"), ("ko", "한국어"),
]
_LABEL_BY_CODE = dict(_LANGS)

_CSS = b"""
window.linuxpop-translate {
    background-color: transparent;
}
box.linuxpop-translate-bubble {
    background-color: #1f2330;
    border-radius: 14px;
    border: 1px solid rgba(255,255,255,0.08);
    padding: 12px 14px;
}
label.linuxpop-translate-text {
    color: #f4f6fb;
    font-size: 13pt;
}
label.linuxpop-translate-src {
    color: #8a92a8;
    font-size: 9pt;
}
button.linuxpop-translate-chip {
    padding: 2px 8px;
    min-height: 0;
}
"""

_css_installed = False


def _install_css() -> None:
    global _css_installed
    if _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def _target_lang() -> str:
    try:
        from settings import get_settings
        return (get_settings().get("translate_target_lang") or "en")
    except Exception:
        return "en"


def _save_target_lang(code: str) -> None:
    try:
        from settings import get_settings
        s = get_settings()
        s.set("translate_target_lang", code)
        s.save()
    except Exception:
        pass


def _translate_text(text: str, target: str) -> tuple[str, str]:
    """Return (translated_text, detected_source_lang). Raises on failure."""
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx"
           "&sl=auto&tl=%s&dt=t&q=%s"
           % (urllib.parse.quote(target), urllib.parse.quote(text)))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    raw = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
    data = json.loads(raw)
    translated = "".join(seg[0] for seg in data[0] if seg and seg[0])
    src = data[2] if len(data) > 2 and data[2] else "?"
    return translated, src


def _notify(title: str, body: str) -> None:
    import subprocess
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "4000",
         "-i", "preferences-desktop-locale", title, body[:300]],
        check=False)


class _Bubble:
    """The floating translation result window."""

    def __init__(self, original: str, translated: str, src: str,
                 target: str, x: int, y: int) -> None:
        self._original = original
        self._x, self._y = x, y
        self._dismiss_id = None
        self._pointer_in_bubble = False
        self._combo_open = False
        self._click_watcher = None

        _install_css()
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("LinuxPop - Translate")
        self.win.set_decorated(False)
        self.win.set_accept_focus(False)
        self.win.set_app_paintable(True)
        self.win.get_style_context().add_class("linuxpop-translate")
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.win.set_visual(visual)

        self._layer_init()

        # Outside-click dismissal: track whether the pointer is on the bubble
        # (reliable on Wayland, unlike surface-local pointer coords) and watch
        # global clicks via the same evdev watcher the main popup uses.
        ev = self.win.get_events()
        self.win.set_events(ev | Gdk.EventMask.ENTER_NOTIFY_MASK
                            | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.win.connect("enter-notify-event", self._on_enter)
        self.win.connect("leave-notify-event", self._on_leave)

        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bubble.get_style_context().add_class("linuxpop-translate-bubble")
        bubble.set_size_request(280, -1)
        self.win.add(bubble)

        # Header: detected source -> target picker, Copy, Close.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._src_lbl = Gtk.Label(xalign=0)
        self._src_lbl.get_style_context().add_class("linuxpop-translate-src")
        header.pack_start(self._src_lbl, False, False, 0)

        arrow = Gtk.Label(label="→")
        arrow.get_style_context().add_class("linuxpop-translate-src")
        header.pack_start(arrow, False, False, 0)

        self._combo = Gtk.ComboBoxText()
        for code, label in _LANGS:
            self._combo.append(code, label)
        self._combo.set_active_id(target if target in _LABEL_BY_CODE else "en")
        self._combo.connect("changed", self._on_lang_changed)
        # Don't dismiss the bubble while the dropdown is open - selecting a
        # language happens on a separate surface, outside the bubble.
        self._combo.connect("notify::popup-shown", self._on_combo_popup)
        header.pack_start(self._combo, False, False, 0)

        header.pack_start(Gtk.Label(), True, True, 0)  # spacer

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.get_style_context().add_class("linuxpop-translate-chip")
        copy_btn.connect("clicked", self._on_copy)
        header.pack_start(copy_btn, False, False, 0)

        close_btn = Gtk.Button(label="✕")
        close_btn.get_style_context().add_class("linuxpop-translate-chip")
        close_btn.connect("clicked", lambda *_: self.dismiss())
        header.pack_start(close_btn, False, False, 0)
        bubble.pack_start(header, False, False, 0)

        # Body: the translated text, selectable + wrapping.
        self._text_lbl = Gtk.Label(xalign=0, yalign=0)
        self._text_lbl.set_line_wrap(True)
        self._text_lbl.set_max_width_chars(42)
        self._text_lbl.set_selectable(True)
        self._text_lbl.get_style_context().add_class("linuxpop-translate-text")
        bubble.pack_start(self._text_lbl, True, True, 0)

        self._set_result(translated, src)

        # Safety net: don't let an orphaned bubble linger forever.
        self._dismiss_id = GLib.timeout_add_seconds(45, self._on_timeout)

    # ---- layer-shell placement (Wayland) --------------------------------
    def _layer_init(self) -> None:
        try:
            gi.require_version("GtkLayerShell", "0.1")
            from gi.repository import GtkLayerShell as L
        except (ValueError, ImportError):
            self._L = None
            return
        self._L = L
        L.init_for_window(self.win)
        L.set_layer(self.win, L.Layer.OVERLAY)
        # NONE: never steal keyboard focus from the source app (same choice
        # the main popup makes). Selection + Copy work without it.
        try:
            L.set_keyboard_mode(self.win, L.KeyboardMode.NONE)
        except Exception:
            pass
        L.set_anchor(self.win, L.Edge.LEFT, True)
        L.set_anchor(self.win, L.Edge.TOP, True)

    def _move(self, x: int, y: int) -> None:
        if self._L is not None:
            self._L.set_margin(self.win, self._L.Edge.LEFT, int(x))
            self._L.set_margin(self.win, self._L.Edge.TOP, int(y))
        else:
            self.win.move(int(x), int(y))  # X11 fallback

    def _present(self) -> None:
        # Off-screen measure so we can clamp to the monitor and sit just
        # below the cursor without flashing at 0,0.
        self._move(-10000, -10000)
        self.win.show_all()
        self.win.realize()
        _, nat = self.win.get_preferred_size()
        w, h = max(nat.width, 1), max(nat.height, 1)
        disp = self.win.get_display()
        mon = disp.get_monitor_at_point(self._x, self._y)
        geom = mon.get_geometry() if mon else None
        tx = int(self._x - w / 2)
        ty = int(self._y + 24)            # below the selection/cursor
        if geom is not None:
            tx = max(geom.x + 4, min(tx, geom.x + geom.width - w - 4))
            if ty + h > geom.y + geom.height - 4:
                ty = int(self._y - h - 24)  # flip above if no room below
            ty = max(geom.y + 4, ty)
        self._move(tx, ty)
        self.win.present()
        self._pointer_in_bubble = False
        self._start_click_watcher()

    def _start_click_watcher(self) -> None:
        try:
            from popup import _ClickWatcher
        except Exception:
            return  # no outside-click dismissal; × button + timeout remain
        self._click_watcher = _ClickWatcher(self._on_global_click)
        self._click_watcher.start()

    # ---- enter/leave + outside-click ------------------------------------
    def _on_enter(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._pointer_in_bubble = True
        return False

    def _on_leave(self, _w, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._pointer_in_bubble = False
        return False

    def _on_combo_popup(self, combo, _pspec):
        self._combo_open = bool(combo.get_property("popup-shown"))

    def _on_global_click(self) -> bool:
        # A click landed somewhere. Keep the bubble if the dropdown is open
        # or the pointer is on the bubble; otherwise dismiss.
        if self._combo_open:
            return False
        if not self._pointer_in_bubble:
            self.dismiss()
        return False

    # ---- result + interaction -------------------------------------------
    def _set_result(self, translated: str, src: str) -> None:
        src_label = _LABEL_BY_CODE.get(src, src or "?")
        self._src_lbl.set_text(src_label)
        self._text_lbl.set_text(translated or "(no translation)")
        self._last_translation = translated or ""

    def _on_copy(self, *_):
        try:
            from platform_backend import get_backend
            get_backend().set_clipboard(self._last_translation)
        except Exception:
            pass

    def _on_lang_changed(self, combo: Gtk.ComboBoxText) -> None:
        code = combo.get_active_id()
        if not code:
            return
        _save_target_lang(code)
        self._text_lbl.set_text("…")

        def _work():
            try:
                translated, src = _translate_text(self._original, code)
            except Exception as exc:  # noqa: BLE001
                GLib.idle_add(self._text_lbl.set_text, f"(error: {exc})")
                return
            GLib.idle_add(self._set_result, translated, src)
        threading.Thread(target=_work, daemon=True,
                         name="translate-relang").start()

    def _on_timeout(self) -> bool:
        self._dismiss_id = None
        self.dismiss()
        return False

    def dismiss(self) -> None:
        if self._dismiss_id is not None:
            GLib.source_remove(self._dismiss_id)
            self._dismiss_id = None
        cw = self._click_watcher
        if cw is not None:
            cw.stop()
            self._click_watcher = None
        self.win.destroy()


def _translate(text: str) -> None:
    """Plugin handler. Runs on a worker thread: do the network call here,
    then build the bubble on the GTK main thread."""
    text = text.strip()
    if not text:
        return
    target = _target_lang()
    # Cursor position for placement (KWin query on Wayland).
    x, y = 0, 0
    try:
        from platform_backend import get_backend
        x, y = get_backend().pointer_position()
    except Exception:
        pass
    try:
        translated, src = _translate_text(text, target)
    except Exception as exc:  # noqa: BLE001
        _notify("Translate failed", str(exc))
        return

    def _build() -> bool:
        try:
            _Bubble(text, translated, src, target, x, y)._present()
        except Exception as exc:  # noqa: BLE001
            print(f"[translate] bubble failed: {exc}")
        return False
    GLib.idle_add(_build)


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="translate",
        icon="preferences-desktop-locale-symbolic",
        tooltip="Translate",
        handler=_translate,
        content_types=(),  # offer for any selection
        priority=45,
    ))
