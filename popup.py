"""GTK popup window showing context-aware action buttons.

Visual style is inspired by PopClip on macOS: a small, dark, rounded
floating bar that appears near the selection and disappears when the
user clicks outside it.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from Xlib import X, XK, display as xdisplay  # noqa: E402

import actions
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
    border-radius: __WIN_RADIUS__px;
    padding: __WIN_PAD__px;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.55),
                0 0 0 1px rgba(255, 255, 255, 0.06) inset;
}

button.linuxpop-action {
    background: transparent;
    border: none;
    border-radius: __BTN_RADIUS__px;
    padding: __PAD_V__px __PAD_H__px;
    margin: 0;
    min-width: __BTN_SIZE__px;
    min-height: __BTN_SIZE__px;
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

/* Overflow chip - shown when more plugins matched than the popup
 * limit allows. Renders as a "+N" pill with a softer tone so it
 * reads as informational rather than a normal action. */
button.linuxpop-overflow {
    color: #c4cad8;
    font-weight: 600;
    font-size: 0.85em;
    padding-left: __PAD_H_OVERFLOW__px;
    padding-right: __PAD_H_OVERFLOW__px;
}
button.linuxpop-overflow:hover {
    background-image: linear-gradient(to bottom right, #3a4258, #2c3146);
    color: #ffffff;
}
"""


def _shift_held_in_current_event() -> bool:
    """Return True if Shift was held when the GTK event currently being
    dispatched was generated. Read inside a 'clicked' callback to know
    whether the user shift-clicked the button. Falls back to False if
    GTK didn't surface an event state (rare - happens for
    programmatically-fired clicks).
    """
    try:
        ok, state = Gtk.get_current_event_state()
    except Exception:
        return False
    if not ok:
        return False
    return bool(state & Gdk.ModifierType.SHIFT_MASK)


_BUTTON_SIZE_MIN = 16   # symbolic icons go muddy below this on every screen
_BUTTON_SIZE_MAX = 32   # > 32 is touch-target territory; mouse users don't benefit
_BUTTON_SIZE_DEFAULT = 22

_popup_css_provider: Gtk.CssProvider | None = None


def _resolve_button_size() -> int:
    """Clamp the popup_button_size setting into the supported range.
    Falls back to the default if the key isn't set or has been hand-
    edited to nonsense in settings.json."""
    try:
        from settings import get_settings
        raw = get_settings().get("popup_button_size", _BUTTON_SIZE_DEFAULT)
        size = int(raw or _BUTTON_SIZE_DEFAULT)
    except Exception:
        size = _BUTTON_SIZE_DEFAULT
    return max(_BUTTON_SIZE_MIN, min(_BUTTON_SIZE_MAX, size))


def _build_css(size: int) -> bytes:
    """Substitute the per-size measurements into the static CSS template.
    Padding, radii and inner gutters scale with button size so the
    popup shrinks/grows as a single visual unit. Colours are remapped
    through theme._LIGHT_REMAP when the user's theme is light, so the
    popup background/border/text follow the same palette as the rest
    of the app."""
    pad_v = max(1, size // 12)
    pad_h = max(3, size // 6)
    win_pad = max(2, size // 7)        # gutter between bar and frame
    win_radius = max(6, size // 2 - 2) # outer corner roundness
    btn_radius = max(3, size // 4)     # button corner roundness
    css = (_CSS
           .replace(b"__BTN_SIZE__", str(size).encode())
           .replace(b"__PAD_V__", str(pad_v).encode())
           .replace(b"__PAD_H__", str(pad_h).encode())
           .replace(b"__PAD_H_OVERFLOW__", str(max(pad_h, 8)).encode())
           .replace(b"__WIN_PAD__", str(win_pad).encode())
           .replace(b"__WIN_RADIUS__", str(win_radius).encode())
           .replace(b"__BTN_RADIUS__", str(btn_radius).encode()))
    # Apply the same dark->light palette swap the main theme uses, so
    # the popup doesn't end up as a dark island in a light window.
    try:
        import theme as _theme
        mode = _theme._resolve_mode(
            (get_settings_func() or {}).get("theme", "dark")
        )
        if mode == "light":
            css = _theme._apply_remap(css, _theme._LIGHT_REMAP)
    except Exception:
        # If theme can't be imported, fall back to dark - it was the
        # default for a long time and won't surprise anyone.
        pass
    return css


def get_settings_func():
    """Single point that yields the live settings dict-like, or {} if
    settings can't be reached. Wrapped so _build_css doesn't have to
    care about import-time ordering."""
    try:
        from settings import get_settings
        return get_settings()
    except Exception:
        return {}


def _install_css() -> None:
    """Install (or reinstall) the popup CSS provider with the current
    button-size setting. Safe to call repeatedly; the old provider is
    removed before the new one is added."""
    global _popup_css_provider
    screen = Gdk.Screen.get_default()
    if screen is None:
        return
    if _popup_css_provider is not None:
        try:
            Gtk.StyleContext.remove_provider_for_screen(
                screen, _popup_css_provider)
        except Exception:
            pass
    provider = Gtk.CssProvider()
    provider.load_from_data(_build_css(_resolve_button_size()))
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    _popup_css_provider = provider


def reinstall_popup_css() -> None:
    """Public hook for the settings dialog to call when the user
    changes popup_button_size - rebuilds the CSS so the next popup
    renders at the new size without a daemon restart."""
    _install_css()


class PopupWindow:
    def __init__(
        self,
        initial_grace_ms: int = 4000,
        leave_grace_ms: int = 600,
        on_open_plugin_order: "Callable[[], None] | None" = None,
    ) -> None:
        # on_open_plugin_order is invoked when the user clicks the
        # overflow chip - lets us route them straight to Plugin Manager
        # → Order so they can fix what's been hidden.
        self._on_open_plugin_order = on_open_plugin_order
        _install_css()

        # TOPLEVEL (not POPUP) so the WM actually stacks us above other
        # LinuxPop dialogs like Settings / Plugin Manager. Override-
        # redirect POPUP windows bypass WM stacking, which on Cinnamon
        # caused the popup to silently end up *under* an already-visible
        # Settings or Plugin Manager window -- clicks went to the wrong
        # window. TOPLEVEL + decorated=False + POPUP_MENU type-hint
        # gives us a borderless on-top window the WM handles correctly.
        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.set_keep_above(True)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        # accept_focus=False prevents the popup from stealing keyboard
        # focus from the text-source app (so paste still goes there).
        # Esc-to-dismiss is handled via the Xlib keymap poll in _tick(),
        # not via GTK key events, so we don't lose that flow.
        self.win.set_accept_focus(False)
        self.win.set_focus_on_map(False)
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

        # _bar is the rounded pill background. It stacks one or two
        # rows vertically depending on how many actions matched.
        self._bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._bar.get_style_context().add_class("linuxpop-bar")
        self._outer.pack_start(self._bar, True, True, 0)
        self._row1 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._bar.pack_start(self._row1, False, False, 0)
        self._bar.pack_start(self._row2, False, False, 0)
        # Row 2 stays out of the layout entirely until needed; without
        # this it would leave a faint baseline gap below row 1 even when
        # empty.
        self._row2.set_no_show_all(True)

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
        # Absolute ceiling on time the popup will linger if the user
        # never moves the mouse into it. Armed in show_for, cancelled
        # the moment the pointer enters the popup. Independent of the
        # leave-grace timer so a stationary cursor still triggers hide.
        self._initial_hide_id: int | None = None
        self._initial_grace_ms = initial_grace_ms  # before mouse enters
        self._leave_grace_ms = leave_grace_ms      # after mouse leaves popup AND text zone
        # Logical-pixel radius around the original selection cursor that counts
        # as "still over the text" - keeps popup alive while user looks at it.
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
        for row in (self._row1, self._row2):
            for child in list(row.get_children()):
                row.remove(child)
                child.destroy()
        # Collapse row 2 until something gets added to it.
        self._row2.hide()
        self._row2.set_no_show_all(True)

    def _add_button(
        self, icon_name: str, tooltip: str, on_click,
        row: Gtk.Box | None = None,
    ) -> None:
        target = row if row is not None else self._row1
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(tooltip)
        btn.get_style_context().add_class("linuxpop-action")
        image = self._make_icon_image(icon_name)
        btn.set_image(image)
        btn.set_always_show_image(True)
        btn.connect("clicked", on_click)
        target.pack_start(btn, False, False, 0)
        if target is self._row2:
            # Once anything lands in row 2, allow it to show.
            self._row2.set_no_show_all(False)

    def _force_all_held(self) -> bool:
        """True if the popup_force_all_modifier setting names a modifier
        that's currently pressed. Read at the moment show_for runs - the
        user must hold the key from before the selection finishes until
        the popup renders (so the modifier survives the 150 ms debounce).
        Returning False is the safe default; misreads just give the
        normal filtered popup."""
        try:
            from settings import get_settings as _gs
            mod_name = (_gs().get("popup_force_all_modifier") or "").strip().lower()
        except Exception:
            return False
        if not mod_name:
            return False
        if self._xdpy is None:
            return False
        try:
            root = self._xdpy.screen().root
            mask = root.query_pointer().mask
        except Exception:
            return False
        # X11 mod bitmasks. Mod1Mask is Alt on essentially every modern
        # X11 desktop; Mod4Mask is Super/Win on Cinnamon, GNOME, KDE,
        # XFCE, MATE. The rare exception (some i3/sway setups remap)
        # rarely runs LinuxPop anyway.
        mod_bits = {
            "shift":   1 << 0,    # ShiftMask
            "ctrl":    1 << 2,    # ControlMask
            "alt":     1 << 3,    # Mod1Mask
            "super":   1 << 6,    # Mod4Mask
        }
        bit = mod_bits.get(mod_name)
        if bit is None:
            return False
        return bool(mask & bit)

    def _max_per_row(self) -> int:
        """How many buttons fit in a single row before we wrap.

        Uses logical pixels and the user's chosen button size, capped by
        a "comfortable reading width" constant so even a 5K screen
        doesn't grow the popup into a freeway-sized strip.
        """
        size = _resolve_button_size()
        # Button outer width: min-width + horizontal padding on each side
        # (kept in sync with linuxpop.css). Slight overestimate so we
        # don't push a button half-off the line by one pixel.
        approx_button_width = size + 14
        try:
            screen = self.win.get_screen()
            display = screen.get_display()
            cx, cy, _ = display.get_default_seat().get_pointer().get_position()
            monitor = display.get_monitor_at_point(cx, cy)
            monitor_w = monitor.get_geometry().width if monitor else 1920
        except Exception:
            monitor_w = 1920
        target_w = min(680, int(monitor_w * 0.5))
        return max(4, target_w // approx_button_width)

    def _make_icon_image(self, icon_name: str) -> Gtk.Image:
        """Render an icon scaled to ~72% of the configured button size,
        so it has a comfortable halo of padding inside the button. GTK
        handles HiDPI natively."""
        size = _resolve_button_size()
        icon_px = max(12, int(size * 0.72))
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(icon_px)
        return image

    def show_for(
        self,
        text: str,
        x: int,
        y: int,
        content_type: ContentType,
        editable: bool = True,
    ) -> None:
        self._current_text = text
        self._clear_buttons()

        # Force-all escape hatch: when the user is holding the configured
        # modifier (default Alt) at the moment we render the popup, skip
        # the classifier's content-type filter and surface every plugin
        # that matches the text - lets the user override a misclassified
        # selection ("Channel | Title" wrongly tagged as COMMAND, etc.)
        # without changing settings. Editable filter still applies so
        # Cut/Paste don't appear in read-only contexts.
        force_all = self._force_all_held()
        if force_all:
            plugins = plugin_loader.all_plugins()
            # Still keep only plugins that match this text via their
            # matches() method - e.g. calculator filters out non-math
            # selections by content. That's intent-preserving, not
            # classifier-based, so it's fine to leave.
            plugins = [p for p in plugins if p.matches(text)]
            # Sort the same way for_content_type does so plugin_order
            # is honoured in the expanded view too.
            try:
                from settings import get_settings as _gs
                order = list(_gs().get("plugin_order") or [])
            except Exception:
                order = []
            order_index = {n: i for i, n in enumerate(order)}
            big = len(order) + 1_000_000
            plugins.sort(key=lambda p: (
                (0, order_index[p.name], p.priority)
                if p.name in order_index
                else (1, big, p.priority)))
        else:
            plugins = plugin_loader.for_content_type(content_type, text)
        # Strip out plugins that only make sense in editable widgets
        # (Cut/Paste/Backspace/Bold/Italic/Underline) when the focused
        # context is read-only. Callers pass editable=False after probing
        # the focused widget - see main.py / editable_detect.py.
        if not editable:
            plugins = [p for p in plugins if not p.requires_editable]
        # Hard cap so the popup doesn't grow to 25 icons across when
        # many plugins are installed. for_content_type already returns
        # in priority/custom-order, so the first N are the highest-
        # ranked. Users who want everything raise max_popup_buttons.
        try:
            from settings import get_settings as _gs
            max_btns = int(_gs().get("max_popup_buttons") or 10)
        except Exception:
            max_btns = 10
        hidden_count = 0
        if max_btns > 0 and len(plugins) > max_btns:
            hidden_count = len(plugins) - max_btns
            print(f"[popup] capping {len(plugins)} plugins to "
                  f"{max_btns} (max_popup_buttons)")
            plugins = plugins[:max_btns]
        if not plugins:
            print(f"[popup] no plugins for {content_type.value} (editable={editable})")
            return

        per_row = self._max_per_row()

        def make_handler(p):
            def _on_click(_btn):
                # Snapshot text at click time + run on a worker thread so
                # blocking handlers (network, subprocess, xdotool) don't
                # freeze the GTK main loop.
                text_snapshot = self._current_text
                plugin_name = p.name
                # Read the modifier state on the GTK event that
                # triggered this click. Shift means 'copy the
                # result instead of pasting it back' (PopClip
                # convention) - relevant when the plugin uses
                # actions.replace_selection() for an in-place
                # transform. Plugins that don't paste are
                # unaffected by the flag.
                shift_held = _shift_held_in_current_event()

                def _worker():
                    try:
                        if shift_held:
                            with actions.force_copy_mode():
                                p.execute(text_snapshot)
                        else:
                            p.execute(text_snapshot)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[popup] plugin '{plugin_name}' failed: {exc}")

                threading.Thread(
                    target=_worker, daemon=True, name=f"plugin-{plugin_name}",
                ).start()
                self.hide()
            return _on_click

        for i, plugin in enumerate(plugins):
            target_row = self._row1 if i < per_row else self._row2
            self._add_button(
                plugin.icon, plugin.tooltip, make_handler(plugin),
                row=target_row,
            )

        if hidden_count > 0:
            # Park the overflow chip on whichever row the last button
            # landed on so the popup stays visually balanced.
            chip_row = (self._row2
                        if len(plugins) > per_row else self._row1)
            self._add_overflow_chip(hidden_count, max_btns, row=chip_row)

        self._present_near(x, y)

    def _add_overflow_chip(
        self, hidden: int, cap: int, row: Gtk.Box | None = None,
    ) -> None:
        """Append a +N chip at the end of the bar when plugins were
        capped. Click takes the user to Plugin Manager → Order so the
        hidden ones can be promoted (or the cap raised). Without this
        the missing buttons are silent - users wonder why their enabled
        AI service doesn't show up and never connect that to a global
        cap they set in Settings."""
        tooltip = (
            f"{hidden} more action{'s' if hidden != 1 else ''} hidden by the "
            f"popup limit ({cap}). Click to reorder in Plugin Manager.")

        def _on_click(_btn):
            try:
                subprocess.run(
                    ["notify-send", "--hint=byte:transient:1", "-t", "4000",
                     "-i", "dialog-information",
                     f"{hidden} action{'s' if hidden != 1 else ''} hidden",
                     "Open Plugin Manager → Order to reorder, or raise "
                     "the popup limit in Settings → Appearance."],
                    check=False,
                )
            except OSError:
                pass
            self.hide()
            if self._on_open_plugin_order is not None:
                try:
                    self._on_open_plugin_order()
                except Exception as exc:  # noqa: BLE001
                    print(f"[popup] open plugin order failed: {exc}")

        btn = Gtk.Button(label=f"+{hidden}")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(tooltip)
        btn.get_style_context().add_class("linuxpop-action")
        btn.get_style_context().add_class("linuxpop-overflow")
        btn.connect("clicked", _on_click)
        target = row if row is not None else self._row1
        target.pack_start(btn, False, False, 0)
        if target is self._row2:
            self._row2.set_no_show_all(False)

    def show_actions(
        self,
        items: list[tuple[str, str, "Callable[[], None]"]],
        x: int,
        y: int,
    ) -> None:
        """Show the popup with a hard-coded list of (icon, tooltip, callback)
        actions instead of going through plugin_loader. Used for the
        no-selection popup that surfaces paste-oriented entry points
        when the hotkey fires without highlighted text.

        Each callback takes no arguments - it should encapsulate whatever
        the click needs to do (open a picker, paste, etc.). Callbacks run
        on a worker thread so a slow handler can't freeze the GTK loop.
        """
        self._current_text = ""
        self._clear_buttons()
        if not items:
            return

        per_row = self._max_per_row()
        for i, (icon, tooltip, callback) in enumerate(items):
            def make_handler(cb, name):
                def _on_click(_btn):
                    def _worker():
                        try:
                            cb()
                        except Exception as exc:  # noqa: BLE001
                            print(f"[popup] action '{name}' failed: {exc}")
                    threading.Thread(
                        target=_worker, daemon=True, name=f"action-{name}",
                    ).start()
                    self.hide()
                return _on_click
            target_row = self._row1 if i < per_row else self._row2
            self._add_button(
                icon, tooltip, make_handler(callback, tooltip),
                row=target_row,
            )

        self._present_near(x, y)

    def _present_near(self, x: int, y: int) -> None:
        """Render the popup near physical screen coords (x, y) and arm
        the tracking/auto-hide machinery. Extracted from show_for so
        both show_for() and show_actions() share the same positioning
        + lifecycle code path."""
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
        # Edge-detector seed: read the actual button/Esc state right now.
        # The old "always True" seed swallowed the very first click-outside
        # if no key was actually held, because the rising-edge detector
        # never saw a 0→1 transition (state started at 1). Reading the
        # real state means stuck-held inputs from a previous popup are
        # still suppressed, but a truly idle pointer is correctly tracked.
        if self._xdpy is not None:
            try:
                data = self._xdpy.screen().root.query_pointer()
                btn_mask = X.Button1Mask | X.Button2Mask | X.Button3Mask
                self._prev_button_pressed = bool(data.mask & btn_mask)
                if self._esc_keycode:
                    keymap = self._xdpy.query_keymap()
                    byte_idx = self._esc_keycode // 8
                    bit_idx = self._esc_keycode % 8
                    self._prev_esc_pressed = bool(keymap[byte_idx] & (1 << bit_idx))
                else:
                    self._prev_esc_pressed = False
            except Exception:
                # Fall back to the old conservative seed if Xlib query fails.
                self._prev_button_pressed = True
                self._prev_esc_pressed = True
        else:
            self._prev_button_pressed = True
            self._prev_esc_pressed = True
        self._start_tracking()
        # Arm the initial-grace timer: if the user never moves the
        # pointer into the popup, hide it after _initial_grace_ms
        # regardless of where the cursor is sitting. Cancelled by
        # _on_enter when the pointer actually reaches the popup.
        self._arm_initial_hide()

    def hide(self) -> None:
        self._cancel_hide_timeout()
        self._cancel_initial_hide()
        self._stop_tracking()
        self.win.hide()

    def _arm_initial_hide(self) -> None:
        self._cancel_initial_hide()
        if self._initial_grace_ms <= 0:
            return
        from gi.repository import GLib
        self._initial_hide_id = GLib.timeout_add(
            self._initial_grace_ms, self._on_initial_timeout,
        )

    def _cancel_initial_hide(self) -> None:
        if self._initial_hide_id is not None:
            from gi.repository import GLib
            GLib.source_remove(self._initial_hide_id)
            self._initial_hide_id = None

    def _on_initial_timeout(self) -> bool:
        self._initial_hide_id = None
        # Only auto-hide if the user never reached the popup. If
        # _hide_timeout_id is None and tick says we're in safe zone,
        # we still hide here - the contract is "after N ms without
        # pointer entry, give up".
        self.hide()
        return False

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
        # Hide via the full path so the tracker tick is cancelled too.
        # Calling self.win.hide() alone leaves _tracker_id pointing at a
        # GLib source that fires once after the next show_for, racing
        # the new tick's setup and reading stale _popup_rect.
        self.hide()
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
            # Reset/cancel any countdown - user is still on text or popup
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
        # Three overlapping regions that all keep the popup alive:
        #   (a) the popup window itself (with a small fudge margin so a
        #       diagonal approach to a corner doesn't trip the leave-grace
        #       just because the pointer is 1-2 px outside the frame).
        #   (b) a generous circle around the original selection cursor.
        #   (c) the bounding rectangle spanning both - i.e. the corridor
        #       between text and popup. This is what was missing: moving
        #       the cursor from selection to popup along the natural
        #       diagonal path used to leave the radius briefly, arming
        #       the hide timer; users on faster mice saw the popup vanish
        #       mid-reach.
        x, y, w, h = self._popup_rect
        fudge = 12
        if (x - fudge) <= px <= (x + w + fudge) and (y - fudge) <= py <= (y + h + fudge):
            return True
        ox, oy = self._origin_logical
        dx = px - ox
        dy = py - oy
        if (dx * dx + dy * dy) <= (self._text_zone_radius * self._text_zone_radius):
            return True
        # Corridor: rect from selection cursor to popup centre, padded.
        cx = x + w / 2
        cy = y + h / 2
        pad = 24
        lo_x = min(ox, cx) - pad
        hi_x = max(ox, cx) + pad
        lo_y = min(oy, cy) - pad
        hi_y = max(oy, cy) + pad
        return lo_x <= px <= hi_x and lo_y <= py <= hi_y

    def _on_focus_out(self, *_):
        self.hide()
        return False

    def _on_enter(self, _widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        # User reached the popup - both timers should stop. Initial
        # grace was "give up if they never come"; once they're here,
        # the leave-grace alone governs disappearance.
        self._cancel_initial_hide()
        self._cancel_hide_timeout()
        return False

    def _on_leave(self, _widget, event):
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        # Don't arm here - the tracker decides based on whether we're still
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
