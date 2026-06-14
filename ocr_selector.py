"""Frictionless region selector for OCR on Wayland/KDE.

Spectacle 6's region capture always pops its annotate overlay and needs an
explicit Accept. This module replaces that with a PopClip-style flow:

  1. grab the whole screen instantly and silently (spectacle -f -b),
  2. show it as a dimmed full-screen layer-shell overlay,
  3. the user drags one rectangle,
  4. on mouse-up we crop the screenshot to that rectangle and hand the PNG
     back - no buttons, no second app window.

`select_and_capture(callback)` MUST run on the GTK main thread. It calls
`callback(png_path_or_None)` exactly once (None = cancelled / failed).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402


def _capture_fullscreen() -> "str | None":
    """Grab the whole screen to a temp PNG with no UI. spectacle -f -b is the
    KWin-native path; grim covers wlroots; maim covers X11."""
    fd, path = tempfile.mkstemp(suffix=".png", prefix="lp-ocr-full-")
    os.close(fd)
    try:
        if shutil.which("spectacle"):
            subprocess.run(["spectacle", "-f", "-b", "-n", "-o", path],
                           capture_output=True, timeout=15)
        elif shutil.which("grim"):
            subprocess.run(["grim", path], capture_output=True, timeout=15)
        elif shutil.which("maim"):
            subprocess.run(["maim", path], capture_output=True, timeout=15)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(path)
    except OSError:
        pass
    return None


class _Selector(Gtk.Window):
    def __init__(self, shot_path: str, callback) -> None:
        super().__init__()
        self._cb = callback
        self._fired = False
        self._pb = GdkPixbuf.Pixbuf.new_from_file(shot_path)
        self._shot_path = shot_path
        self._start = None
        self._cur = None

        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_keep_above(True)

        if not self._init_layer_shell():
            # X11 / non-layer-shell fallback: a fullscreen always-on-top window.
            self.fullscreen()

        area = Gtk.DrawingArea()
        area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK)
        area.connect("draw", self._on_draw)
        self.add(area)
        self._area = area

        self.connect("button-press-event", self._on_press)
        self.connect("button-release-event", self._on_release)
        self.connect("motion-notify-event", self._on_motion)
        self.connect("key-press-event", self._on_key)
        try:
            crosshair = Gdk.Cursor.new_from_name(self.get_display(), "crosshair")
            self.connect("realize",
                         lambda w: w.get_window().set_cursor(crosshair))
        except Exception:
            pass

    def _init_layer_shell(self) -> bool:
        try:
            gi.require_version("GtkLayerShell", "0.1")
            from gi.repository import GtkLayerShell as L
        except Exception:
            return False
        try:
            L.init_for_window(self)
            L.set_layer(self, L.Layer.OVERLAY)
            L.set_keyboard_mode(self, L.KeyboardMode.EXCLUSIVE)
            for edge in (L.Edge.LEFT, L.Edge.RIGHT, L.Edge.TOP, L.Edge.BOTTOM):
                L.set_anchor(self, edge, True)
            L.set_exclusive_zone(self, -1)
            return True
        except Exception:
            return False

    # ----- drawing -----
    def _on_draw(self, _area, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        # Dimmed screenshot as the backdrop, scaled to the window.
        scaled = self._pb.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
        Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()
        if self._start and self._cur:
            x0, y0 = self._start
            x1, y1 = self._cur
            rx, ry = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            if rw > 1 and rh > 1:
                # Punch the bright, undimmed screenshot through the selection.
                cr.save()
                cr.rectangle(rx, ry, rw, rh)
                cr.clip()
                Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
                cr.paint()
                cr.restore()
                cr.set_source_rgba(0.36, 0.49, 0.96, 1.0)
                cr.set_line_width(2)
                cr.rectangle(rx, ry, rw, rh)
                cr.stroke()
        return False

    # ----- input -----
    def _on_press(self, _w, ev) -> bool:
        if ev.button == 1:
            self._start = (ev.x, ev.y)
            self._cur = (ev.x, ev.y)
        return True

    def _on_motion(self, _w, ev) -> bool:
        if self._start:
            self._cur = (ev.x, ev.y)
            self._area.queue_draw()
        return True

    def _on_release(self, _w, ev) -> bool:
        if ev.button != 1 or not self._start:
            return True
        x0, y0 = self._start
        x1, y1 = ev.x, ev.y
        self._finish((min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
        return True

    def _on_key(self, _w, ev) -> bool:
        if ev.keyval == Gdk.KEY_Escape:
            self._finish(None)
        return True

    def _finish(self, rect) -> None:
        if self._fired:
            return
        self._fired = True
        crop_path = None
        try:
            if rect is not None:
                rx, ry, rw, rh = rect
                if rw >= 4 and rh >= 4:
                    # Window coords are logical; the screenshot is at device
                    # resolution. Scale the rectangle up to pixbuf pixels.
                    sx = self._pb.get_width() / max(1, self.get_allocated_width())
                    sy = self._pb.get_height() / max(1, self.get_allocated_height())
                    px = max(0, int(rx * sx))
                    py = max(0, int(ry * sy))
                    pw = min(self._pb.get_width() - px, int(rw * sx))
                    ph = min(self._pb.get_height() - py, int(rh * sy))
                    if pw > 0 and ph > 0:
                        sub = self._pb.new_subpixbuf(px, py, pw, ph)
                        fd, crop_path = tempfile.mkstemp(
                            suffix=".png", prefix="lp-ocr-crop-")
                        os.close(fd)
                        sub.savev(crop_path, "png", [], [])
        except Exception as exc:  # noqa: BLE001
            print(f"[ocr] crop failed: {exc}")
            crop_path = None
        self.destroy()
        try:
            os.unlink(self._shot_path)
        except OSError:
            pass
        try:
            self._cb(crop_path)
        except Exception as exc:  # noqa: BLE001
            print(f"[ocr] selector callback error: {exc}")


def select_and_capture(callback) -> bool:
    """Show the overlay and call callback(crop_png_or_None). Main thread only.
    Returns False (and never calls back) if we couldn't grab the screen."""
    shot = _capture_fullscreen()
    if not shot:
        return False
    sel = _Selector(shot, callback)
    sel.show_all()
    sel.present()
    return False  # idle_add: run once


def available() -> bool:
    """True if we have a full-screen grabber and layer-shell/X11 to overlay."""
    if not (shutil.which("spectacle") or shutil.which("grim")
            or shutil.which("maim")):
        return False
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell  # noqa: F401
        return True
    except Exception:
        # No layer-shell: only safe to overlay on X11.
        return bool(os.environ.get("DISPLAY")) and not os.environ.get(
            "WAYLAND_DISPLAY")
