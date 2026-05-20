"""GTK popup window showing context-aware action buttons.

Visual style is inspired by PopClip on macOS: a small, dark, rounded
floating bar that appears near the selection and disappears when the
user clicks outside it.
"""
from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from Xlib import X, XK, display as xdisplay  # noqa: E402

import plugin_loader
from classifier import ContentType

_CSS = b"""
window.linuxpop-popup {
    background-color: rgba(0, 0, 0, 0);
}

box.linuxpop-bar {
    background-image: linear-gradient(to bottom, #262d3f, #1c2231);
    background-color: #1c2231;
    border: 1px solid #3a4258;
    border-radius: 9px;
    padding: 3px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.55),
                0 0 0 1px rgba(255, 255, 255, 0.06) inset;
}

button.linuxpop-action {
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 3px 5px;
    margin: 0;
    min-width: 22px;
    min-height: 22px;
    color: #f0f3fa;
    transition: background-color 100ms ease, color 100ms ease;
}

button.linuxpop-action:hover {
    background-image: linear-gradient(to bottom right, #5B7DF5, #7C3AED);
    color: #ffffff;
}

button.linuxpop-action:active {
    background-image: linear-gradient(to bottom right, #4A6CE3, #6929DB);
    color: #ffffff;
}

button.linuxpop-action image {
    color: #f0f3fa;
}
"""


def _install_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    screen = Gdk.Screen.get_default()
    if screen is not None:
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )


class PopupWindow:
    def __init__(
        self,
        initial_grace_ms: int = 4000,
        leave_grace_ms: int = 600,
    ) -> None:
        _install_css()

        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_accept_focus(True)
        self.win.set_type_hint(Gdk.WindowTypeHint.POPUP_MENU)
        self.win.get_style_context().add_class("linuxpop-popup")

        # Transparent background so the rounded corners of the inner bar show through
        screen = self.win.get_screen()
        visual = screen.get_rgba_visual()
        if visual is not None and screen.is_composited():
            self.win.set_visual(visual)
        self.win.set_app_paintable(True)

        # Outer container so the rounded HBox has breathing room from window edges
        self._outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._outer.set_margin_top(1)
        self._outer.set_margin_bottom(1)
        self._outer.set_margin_start(1)
        self._outer.set_margin_end(1)
        self.win.add(self._outer)

        self._bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._bar.get_style_context().add_class("linuxpop-bar")
        self._outer.pack_start(self._bar, True, True, 0)

        # Track enter/leave + button-press for outside-click detection
        ev = self.win.get_events()
        self.win.set_events(
            ev
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )

        self.win.connect("focus-out-event", self._on_focus_out)
        self.win.connect("key-press-event", self._on_key_press)
        self.win.connect("button-press-event", self._on_button_press)
        self.win.connect("enter-notify-event", self._on_enter)
        self.win.connect("leave-notify-event", self._on_leave)

        self._current_text: str = ""
        self._hide_timeout_id: int | None = None
        self._tracker_id: int | None = None
        self._initial_grace_ms = initial_grace_ms  # before mouse enters
        self._leave_grace_ms = leave_grace_ms      # after mouse leaves popup AND text zone
        # Logical-pixel radius around the original selection cursor that counts
        # as "still over the text" — keeps popup alive while user looks at it.
        self._text_zone_radius = 80
        self._origin_logical: tuple[float, float] = (0.0, 0.0)
        self._popup_rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # x, y, w, h logical
        self._scale: int = 1
        # Xlib display used for polling pointer button state + Esc key
        try:
            self._xdpy = xdisplay.Display()
            self._esc_keycode = self._xdpy.keysym_to_keycode(XK.string_to_keysym("Escape"))
        except Exception as exc:  # noqa: BLE001
            print(f"[popup] could not open Xlib display: {exc}")
            self._xdpy = None
            self._esc_keycode = 0
        self._prev_button_pressed = False
        self._prev_esc_pressed = False

    def _clear_buttons(self) -> None:
        # destroy() drops the GTK refcount to 0, which releases the button's
        # signal-handler GClosures and its child image. remove() alone would
        # rely on Python GC noticing the orphan; this is the belt-and-
        # suspenders form.
        for child in list(self._bar.get_children()):
            self._bar.remove(child)
            child.destroy()

    def _add_button(self, icon_name: str, tooltip: str, on_click) -> None:
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(tooltip)
        btn.get_style_context().add_class("linuxpop-action")
        image = self._make_icon_image(icon_name)
        btn.set_image(image)
        btn.set_always_show_image(True)
        btn.connect("clicked", on_click)
        self._bar.pack_start(btn, False, False, 0)

    def _make_icon_image(self, icon_name: str) -> Gtk.Image:
        """Render an icon at a fixed small size. GTK handles HiDPI natively."""
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(16)
        return image

    def show_for(
        self,
        text: str,
        x: int,
        y: int,
        content_type: ContentType,
    ) -> None:
        self._current_text = text
        self._clear_buttons()

        plugins = plugin_loader.for_content_type(content_type, text)
        if not plugins:
            print(f"[popup] no plugins for {content_type.value}")
            return

        for plugin in plugins:
            def make_handler(p):
                def _on_click(_btn):
                    # Snapshot text at click time + run on a worker thread so
                    # blocking handlers (network, subprocess, xdotool) don't
                    # freeze the GTK main loop.
                    text_snapshot = self._current_text
                    plugin_name = p.name

                    def _worker():
                        try:
                            p.execute(text_snapshot)
                        except Exception as exc:  # noqa: BLE001
                            print(f"[popup] plugin '{plugin_name}' failed: {exc}")

                    threading.Thread(
                        target=_worker, daemon=True, name=f"plugin-{plugin_name}",
                    ).start()
                    self.hide()
                return _on_click
            self._add_button(plugin.icon, plugin.tooltip, make_handler(plugin))

        self._bar.show_all()
        self._outer.show_all()

        # Realize so we can query its natural size, then position above the point
        self.win.show_all()
        self.win.realize()

        _, natural = self.win.get_preferred_size()
        w = max(natural.width, 1)
        h = max(natural.height, 1)

        # X11 root coords come in physical pixels; GTK move() takes logical.
        # Convert via the monitor's scale factor before doing layout math.
        screen = self.win.get_screen()
        display = screen.get_display()
        monitor = display.get_monitor_at_point(int(x), int(y))
        scale = monitor.get_scale_factor() if monitor else 1
        lx = x / scale
        ly = y / scale

        # Cursor Y is the mouse position, which sits in the middle of the
        # selected line. We need to clear the whole line (~24 px tall in
        # most fonts) plus a small visual gap, otherwise the popup lands
        # on top of the text it's supposed to act on.
        _LINE_CLEARANCE = 28
        _BELOW_GAP = 32  # used when there isn't room above

        # Place the popup horizontally centered on (lx,ly), clearly above ly
        target_x = int(lx - w / 2)
        target_y = int(ly - h - _LINE_CLEARANCE)

        # Keep on-screen (geom is also in logical coords)
        if monitor is not None:
            geom = monitor.get_geometry()
            target_x = max(geom.x + 4, min(target_x, geom.x + geom.width - w - 4))
            if target_y < geom.y + 4:
                # Not enough room above: show below the selection instead,
                # with the same generous gap so we don't overlap downward.
                target_y = int(ly + _BELOW_GAP)
            target_y = min(target_y, geom.y + geom.height - h - 4)

        self.win.move(target_x, target_y)
        self.win.present()

        # Record safe zones for the cursor-tracking loop
        self._origin_logical = (lx, ly)
        self._popup_rect = (target_x, target_y, w, h)
        self._scale = scale
        # Reset edge-detectors: assume any currently-held button/Esc is from
        # the previous interaction. User must release then press again to
        # trigger dismissal — avoids the "Esc was held during previous popup,
        # next popup ignores my Esc" bug.
        self._prev_button_pressed = True
        self._prev_esc_pressed = True
        self._start_tracking()

    def hide(self) -> None:
        self._cancel_hide_timeout()
        self._stop_tracking()
        self.win.hide()

    def _arm_hide_timeout(self, ms: int) -> None:
        self._cancel_hide_timeout()
        from gi.repository import GLib
        self._hide_timeout_id = GLib.timeout_add(ms, self._on_hide_timeout)

    def _cancel_hide_timeout(self) -> None:
        if self._hide_timeout_id is not None:
            from gi.repository import GLib
            GLib.source_remove(self._hide_timeout_id)
            self._hide_timeout_id = None

    def _on_hide_timeout(self) -> bool:
        self._hide_timeout_id = None
        self.win.hide()
        return False

    def _start_tracking(self) -> None:
        self._stop_tracking()
        from gi.repository import GLib
        self._tracker_id = GLib.timeout_add(150, self._tick)

    def _stop_tracking(self) -> None:
        if self._tracker_id is not None:
            from gi.repository import GLib
            GLib.source_remove(self._tracker_id)
            self._tracker_id = None

    def _tick(self) -> bool:
        # Stop tracking once the window is hidden
        if not self.win.get_visible():
            self._tracker_id = None
            return False

        # Logical-pixel pointer position via GDK seat (handles HiDPI correctly)
        display = self.win.get_display()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        _screen, px, py = pointer.get_position()

        # Poll physical button state + Esc via Xlib (works even when GTK seat-grab
        # is denied by ibus/cinnamon). Edge-detect 0→1 transitions.
        if self._xdpy is not None:
            try:
                data = self._xdpy.screen().root.query_pointer()
                button_mask = X.Button1Mask | X.Button2Mask | X.Button3Mask
                pressed = bool(data.mask & button_mask)
                phys_x, phys_y = data.root_x, data.root_y
                click_lx = phys_x / self._scale
                click_ly = phys_y / self._scale
                if pressed and not self._prev_button_pressed:
                    if not self._point_in_popup(click_lx, click_ly):
                        self._prev_button_pressed = pressed
                        self.hide()
                        return False
                self._prev_button_pressed = pressed

                if self._esc_keycode:
                    keymap = self._xdpy.query_keymap()
                    byte_idx = self._esc_keycode // 8
                    bit_idx = self._esc_keycode % 8
                    esc_pressed = bool(keymap[byte_idx] & (1 << bit_idx))
                    if esc_pressed and not self._prev_esc_pressed:
                        self._prev_esc_pressed = esc_pressed
                        self.hide()
                        return False
                    self._prev_esc_pressed = esc_pressed
            except Exception as exc:  # noqa: BLE001
                print(f"[popup] xlib query failed: {exc}")

        in_safe = self._in_safe_zone(px, py)
        if in_safe:
            # Reset/cancel any countdown — user is still on text or popup
            self._cancel_hide_timeout()
        else:
            # Arm leave-grace if not already counting down
            if self._hide_timeout_id is None:
                self._arm_hide_timeout(self._leave_grace_ms)
        return True  # keep ticking

    def _point_in_popup(self, lx: float, ly: float) -> bool:
        x, y, w, h = self._popup_rect
        return x <= lx <= x + w and y <= ly <= y + h

    def _in_safe_zone(self, px: int, py: int) -> bool:
        # Over the popup itself?
        x, y, w, h = self._popup_rect
        if x <= px <= x + w and y <= py <= y + h:
            return True
        # Near the original cursor (i.e., still over the selected text)?
        ox, oy = self._origin_logical
        dx = px - ox
        dy = py - oy
        return (dx * dx + dy * dy) <= (self._text_zone_radius * self._text_zone_radius)

    def _on_focus_out(self, *_):
        self.hide()
        return False

    def _on_enter(self, _widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        self._cancel_hide_timeout()
        return False

    def _on_leave(self, _widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        # Don't arm here — the tracker decides based on whether we're still
        # near the original text. Leaving the popup alone isn't enough.
        return False

    def _on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _on_button_press(self, _widget, event):
        # With seat-grab + owner_events, clicks outside the popup come here.
        # Clicks on our own buttons go through normal child dispatch.
        win_w = self.win.get_allocated_width()
        win_h = self.win.get_allocated_height()
        # event.x/y are relative to the popup window's origin
        if event.x < 0 or event.y < 0 or event.x > win_w or event.y > win_h:
            self.hide()
            return True
        return False
