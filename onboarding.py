"""Animated onboarding for LinuxPop.

A multi-page first-run walkthrough with hand-drawn Cairo animations that show
exactly how LinuxPop works — select text → actions popup, how to summon it,
how to add ready-made plugins, and how to build your own with the recipe
builder. Fully skippable, with a "don't show this again" checkbox that flips
the `show_welcome_dialog` setting.

Entry point: ``show_onboarding(settings, on_open_plugins=None, parent=None)``.
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

# ── brand palette (matches theme.py / the app icon) ──────────────────────────
BLUE = (0.357, 0.490, 0.961)    # #5B7DF5
VIOLET = (0.486, 0.227, 0.929)  # #7C3AED
PINK = (0.925, 0.282, 0.600)    # #EC4899
GREEN = (0.204, 0.780, 0.349)   # #34C759
INK = (0.105, 0.130, 0.184)     # #1c2231 (dark text on the light stage)
MUTE = (0.62, 0.66, 0.74)
STAGE = (0.985, 0.990, 1.0)     # near-white card the mockups sit on
STAGE_BORDER = (0.86, 0.88, 0.93)

_ACCENTS = [BLUE, VIOLET, PINK, GREEN]


# ── tiny easing / drawing helpers ────────────────────────────────────────────
def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _eo(x):  # ease-out cubic
    x = _clamp(x)
    return 1.0 - (1.0 - x) ** 3


def _eio(x):  # ease-in-out cubic
    x = _clamp(x)
    return 4 * x * x * x if x < 0.5 else 1.0 - (-2 * x + 2) ** 3 / 2.0


def _rrect(cr, x, y, w, h, r):
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _bar(cr, x, y, w, h, rgb, a=1.0):
    _rrect(cr, x, y, w, h, h / 2.0)
    cr.set_source_rgba(*rgb, a)
    cr.fill()


def _stage(cr, w, h):
    """A soft light card centred in the drawing area; returns its inner rect."""
    m = 14
    sx, sy, sw, sh = m, m, w - 2 * m, h - 2 * m
    _rrect(cr, sx, sy, sw, sh, 18)
    cr.set_source_rgba(*STAGE, 1.0)
    cr.fill_preserve()
    cr.set_source_rgba(*STAGE_BORDER, 1.0)
    cr.set_line_width(1.0)
    cr.stroke()
    return sx, sy, sw, sh


def _cursor(cr, x, y, scale=1.0, rgb=INK):
    cr.save()
    cr.translate(x, y)
    cr.scale(scale, scale)
    cr.move_to(0, 0)
    cr.line_to(0, 16)
    cr.line_to(4, 12)
    cr.line_to(7, 18)
    cr.line_to(9, 17)
    cr.line_to(6, 11)
    cr.line_to(11, 11)
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, 1)
    cr.set_line_width(2.4)
    cr.stroke_preserve()
    cr.set_source_rgba(*rgb, 1)
    cr.fill()
    cr.restore()


# ── page illustrations: each draw(cr, w, h, t) where t = seconds on page ─────
def _draw_how(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    lt = t % 4.2
    # mock editor: four text lines
    lx = sx + 34
    widths = [sw - 120, sw - 90, sw - 150, sw - 60]
    ys = [sy + 40, sy + 72, sy + 104, sy + 136]
    for i, (ww, yy) in enumerate(zip(widths, ys)):
        _bar(cr, lx, yy, ww, 12, (0.80, 0.83, 0.89))
    # selection sweeps over line 2
    seln = _eo(lt / 0.8)
    selw = widths[1] * seln
    if selw > 1:
        _rrect(cr, lx - 4, ys[1] - 5, selw + 8, 22, 5)
        cr.set_source_rgba(*BLUE, 0.30)
        cr.fill()
        _bar(cr, lx, ys[1], widths[1] * min(1.0, seln), 12, BLUE)
    # popup bar rises under the selection
    pa = _eo((lt - 1.0) / 0.5)
    if pa > 0.01:
        pw, ph = 188, 40
        px = lx - 6
        py = ys[1] + 26 + (1 - pa) * 14
        cr.save()
        cr.translate(0, 0)
        _rrect(cr, px, py, pw, ph, 11)
        cr.set_source_rgba(1, 1, 1, pa)
        cr.fill_preserve()
        cr.set_source_rgba(*BLUE, 0.18 * pa)
        cr.set_line_width(1.0)
        cr.stroke()
        # shadow hint
        for i in range(4):
            cx = px + 26 + i * 46
            cr.arc(cx, py + ph / 2, 11, 0, 2 * math.pi)
            cr.set_source_rgba(*_ACCENTS[i], 0.9 * pa)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, pa)
            cr.set_line_width(2.0)
            cr.arc(cx, py + ph / 2, 5, 0, 2 * math.pi)
            cr.stroke()
        cr.restore()
        # pointer moves to first action and clicks
        mt = _clamp((lt - 1.9) / 1.0)
        if mt > 0:
            tx = px + 26 + 16 * (1 - _eo(mt))
            ty = py + ph / 2 + 22 * (1 - _eo(mt))
            click = math.sin(_clamp((lt - 2.7) / 0.5) * math.pi)
            if click > 0:
                cr.arc(px + 26, py + ph / 2, 11 + 8 * click, 0, 2 * math.pi)
                cr.set_source_rgba(*BLUE, 0.35 * (1 - click))
                cr.fill()
            _cursor(cr, tx, ty, 1.0)


def _draw_summon(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    lt = t % 3.6
    keys = getattr(_draw_summon, "keys", ["Super", "Shift", "Y"])
    # keycaps, pressed in sequence
    cx = sx + sw / 2
    total = sum(28 + 16 * len(k) for k in keys) + 14 * (len(keys) - 1)
    x = cx - total / 2
    cy = sy + 56
    for i, k in enumerate(keys):
        kw = 28 + 16 * len(k)
        press = _clamp((lt - i * 0.28) / 0.22) - _clamp((lt - i * 0.28 - 0.9) / 0.22)
        dy = 4 * press
        glow = press
        _rrect(cr, x, cy + dy, kw, 40, 9)
        cr.set_source_rgba(*BLUE, 0.10 + 0.85 * glow)
        cr.fill_preserve()
        cr.set_source_rgba(*BLUE, 0.45)
        cr.set_line_width(1.4)
        cr.stroke()
        cr.select_font_face("sans-serif", 0, 1)
        cr.set_font_size(15)
        te = cr.text_extents(k)
        cr.move_to(x + kw / 2 - te.width / 2 - te.x_bearing,
                   cy + dy + 26)
        cr.set_source_rgba(*([1, 1, 1] if glow > 0.5 else list(INK)), 1)
        cr.show_text(k)
        if i < len(keys) - 1:
            cr.move_to(x + kw + 4, cy + 26)
            cr.set_source_rgba(*MUTE, 1)
            cr.set_font_size(16)
            cr.show_text("+")
        x += kw + 14
    # popup blips after the combo
    blip = math.sin(_clamp((lt - 1.2) / 0.6) * math.pi)
    if blip > 0:
        pw, ph = 150, 34
        px, py = cx - pw / 2, cy + 70
        _rrect(cr, px, py, pw, ph, 10)
        cr.set_source_rgba(1, 1, 1, blip)
        cr.fill_preserve()
        cr.set_source_rgba(*BLUE, 0.2 * blip)
        cr.set_line_width(1.0)
        cr.stroke()
        for i in range(3):
            cr.arc(px + 26 + i * 50, py + ph / 2, 9, 0, 2 * math.pi)
            cr.set_source_rgba(*_ACCENTS[i], 0.9 * blip)
            cr.fill()


def _draw_plugins(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    lt = t % 4.4
    cols, rows = 3, 2
    gap = 16
    cw = (sw - 60 - (cols - 1) * gap) / cols
    ch = (sh - 56 - (rows - 1) * gap) / rows
    ox = sx + 30
    oy = sy + 28
    for r in range(rows):
        for c in range(cols):
            i = r * cols + c
            appear = _eo((lt - i * 0.16) / 0.6)
            if appear <= 0.01:
                continue
            x = ox + c * (cw + gap)
            y = oy + r * (ch + gap) + (1 - appear) * 18
            cr.save()
            cr.push_group()
            _rrect(cr, x, y, cw, ch, 12)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(*STAGE_BORDER, 1)
            cr.set_line_width(1.0)
            cr.stroke()
            acc = _ACCENTS[i % len(_ACCENTS)]
            cr.arc(x + 22, y + ch / 2, 12, 0, 2 * math.pi)
            cr.set_source_rgba(*acc, 1)
            cr.fill()
            _bar(cr, x + 42, y + ch / 2 - 11, cw - 60, 7, (0.78, 0.81, 0.88))
            _bar(cr, x + 42, y + ch / 2 + 2, cw - 80, 6, (0.86, 0.88, 0.93))
            # a check pops on once it has "installed"
            chk = _clamp((lt - i * 0.16 - 0.7) / 0.4)
            if chk > 0:
                cr.arc(x + cw - 16, y + 15, 8 * _eo(chk), 0, 2 * math.pi)
                cr.set_source_rgba(*GREEN, 1)
                cr.fill()
                if chk > 0.5:
                    cr.set_source_rgba(1, 1, 1, 1)
                    cr.set_line_width(1.8)
                    cr.move_to(x + cw - 19, y + 15)
                    cr.line_to(x + cw - 16.5, y + 18)
                    cr.line_to(x + cw - 12.5, y + 12)
                    cr.stroke()
            cr.pop_group_to_source()
            cr.paint_with_alpha(appear)
            cr.restore()


def _draw_recipe(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    lt = t % 4.0
    blocks = [("Match", "a URL", BLUE), ("Action", "open it", VIOLET),
              ("Run", "done", GREEN)]
    bw, bh = 118, 64
    gap = 26
    total = len(blocks) * bw + (len(blocks) - 1) * gap
    cx = sx + sw / 2
    cy = sy + sh / 2 - bh / 2
    base_x = cx - total / 2
    join = _eio(lt / 1.0)
    spread = (1 - join) * 40
    # connector line draws as blocks settle
    cr.set_source_rgba(*MUTE, 0.6)
    cr.set_line_width(2.0)
    for i in range(len(blocks) - 1):
        x0 = base_x + (i + 1) * bw + i * gap
        ln = _clamp((lt - 0.6 - i * 0.2) / 0.4)
        cr.move_to(x0 - spread, cy + bh / 2)
        cr.line_to(x0 + gap * ln + spread * (1 - ln), cy + bh / 2)
        cr.stroke()
    for i, (head, sub, acc) in enumerate(blocks):
        appear = _eo((lt - i * 0.18) / 0.6)
        if appear <= 0.01:
            continue
        x = base_x + i * (bw + gap) + (i - 1) * -spread
        cr.save()
        _rrect(cr, x, cy, bw, bh, 12)
        cr.set_source_rgba(1, 1, 1, appear)
        cr.fill_preserve()
        cr.set_source_rgba(*acc, 0.55 * appear)
        cr.set_line_width(1.6)
        cr.stroke()
        _rrect(cr, x, cy, 5, bh, 2)
        cr.set_source_rgba(*acc, appear)
        cr.fill()
        cr.select_font_face("sans-serif", 0, 1)
        cr.set_font_size(14)
        cr.set_source_rgba(*INK, appear)
        cr.move_to(x + 16, cy + 27)
        cr.show_text(head)
        cr.set_font_size(12)
        cr.set_source_rgba(*MUTE, appear)
        cr.move_to(x + 16, cy + 46)
        cr.show_text(sub)
        cr.restore()
    # a play pulse once assembled
    pulse = math.sin(_clamp((lt - 1.6) / 0.7) * math.pi)
    if pulse > 0:
        px = base_x + total
        cr.arc(px + 8, cy + bh / 2, 14 + 10 * pulse, 0, 2 * math.pi)
        cr.set_source_rgba(*GREEN, 0.25 * (1 - pulse))
        cr.fill()


def _draw_done(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    cx, cy = sx + sw / 2, sy + sh / 2 - 6
    R = 40
    ring = _eo(t / 0.7)
    # ring
    cr.set_line_width(5)
    cr.set_source_rgba(*GREEN, 1)
    cr.arc(cx, cy, R, -math.pi / 2, -math.pi / 2 + 2 * math.pi * ring)
    cr.stroke()
    if ring >= 1.0:
        cr.arc(cx, cy, R, 0, 2 * math.pi)
        cr.set_source_rgba(*GREEN, 0.10)
        cr.fill()
    # checkmark strokes in
    ck = _eo((t - 0.55) / 0.45)
    if ck > 0:
        p0 = (cx - 18, cy + 1)
        p1 = (cx - 5, cy + 14)
        p2 = (cx + 20, cy - 14)
        cr.set_line_width(5)
        cr.set_line_cap(1)
        cr.set_source_rgba(*GREEN, 1)
        cr.move_to(*p0)
        if ck <= 0.5:
            f = ck / 0.5
            cr.line_to(p0[0] + (p1[0] - p0[0]) * f, p0[1] + (p1[1] - p0[1]) * f)
        else:
            f = (ck - 0.5) / 0.5
            cr.line_to(*p1)
            cr.line_to(p1[0] + (p2[0] - p1[0]) * f, p1[1] + (p2[1] - p1[1]) * f)
        cr.stroke()
    # ripple + confetti once done
    rip = _clamp((t - 0.9) / 0.8)
    if 0 < rip < 1:
        cr.set_line_width(2)
        cr.set_source_rgba(*GREEN, 0.5 * (1 - rip))
        cr.arc(cx, cy, R + 14 * _eo(rip), 0, 2 * math.pi)
        cr.stroke()
    if t > 1.0:
        for i in range(8):
            ang = i * math.pi / 4 + 0.3
            d = 58 + 10 * math.sin(t * 2 + i)
            px, py = cx + math.cos(ang) * d, cy + math.sin(ang) * d
            cr.arc(px, py, 3.2, 0, 2 * math.pi)
            cr.set_source_rgba(*_ACCENTS[i % 4], 0.8)
            cr.fill()


def _build_pages(settings):
    try:
        popup_key = (settings.get("hotkey") or "super+shift+y")
        clip_key = (settings.get("clipboard_hotkey") or "ctrl+super+v")
    except Exception:
        popup_key, clip_key = "super+shift+y", "ctrl+super+v"

    def _fmt(combo):
        return "+".join(p.strip().capitalize() for p in combo.split("+") if p.strip())

    _draw_summon.keys = [p.strip().capitalize()
                         for p in popup_key.split("+") if p.strip()]
    return [
        dict(title="Select text, get instant actions",
             body="Highlight anything in any app — a link, an error, a "
                  "paragraph — and LinuxPop pops up a little bar of actions "
                  "tailored to what you picked.",
             draw=_draw_how),
        dict(title="Summon it your way",
             body=f"It appears automatically when you select text. Or press "
                  f"<b>{_fmt(popup_key)}</b> for the popup anywhere, and "
                  f"<b>{_fmt(clip_key)}</b> for clipboard history.",
             draw=_draw_summon),
        dict(title="Add ready-made plugins",
             body="Open the tray icon → <b>Plugins</b> → <b>Available</b> and "
                  "one-click install extras: send-to-AI, transforms, QR codes, "
                  "word count, search shortcuts and more.",
             draw=_draw_plugins),
        dict(title="Build your own — no code needed",
             body="In <b>Plugins → Custom</b>, the recipe builder lets you "
                  "assemble your own actions: match some text, then run a "
                  "command or open a URL. That's a real plugin.",
             draw=_draw_recipe),
        dict(title="You're all set",
             body="Everything lives in the tray icon (top-right): Settings, "
                  "Plugins, and your custom buttons. Now go select some text "
                  "and try it!",
             draw=_draw_done),
    ]


class OnboardingWindow(Gtk.Window):
    def __init__(self, settings, on_open_plugins=None):
        super().__init__(title="Welcome to LinuxPop")
        self._settings = settings
        self._on_open_plugins = on_open_plugins
        self._pages = _build_pages(settings)
        self._index = 0
        self._t0 = GLib.get_monotonic_time()

        self.set_default_size(680, 580)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        try:
            self.set_icon_name("linuxpop")
        except Exception:
            pass

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.set_margin_top(0)

        # illustration (persistent, animates the current page)
        self._area = Gtk.DrawingArea()
        self._area.set_size_request(-1, 264)
        self._area.connect("draw", self._on_draw)
        self._area.set_margin_top(14)
        self._area.set_margin_start(20)
        self._area.set_margin_end(20)
        root.pack_start(self._area, False, False, 0)

        # text pages slide
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(280)
        self._stack.set_homogeneous(True)
        for i, pg in enumerate(self._pages):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(10)
            box.set_margin_start(40)
            box.set_margin_end(40)
            title = Gtk.Label(xalign=0.5)
            title.set_markup(
                f"<span size='xx-large' weight='bold'>{pg['title']}</span>")
            title.set_justify(Gtk.Justification.CENTER)
            title.set_line_wrap(True)
            box.pack_start(title, False, False, 0)
            body = Gtk.Label(xalign=0.5)
            body.set_markup(f"<span size='large'>{pg['body']}</span>")
            body.set_justify(Gtk.Justification.CENTER)
            body.set_line_wrap(True)
            body.set_max_width_chars(52)
            box.pack_start(body, False, False, 0)
            self._stack.add_named(box, f"p{i}")
        root.pack_start(self._stack, True, True, 0)

        # progress dots
        self._dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._dots.set_halign(Gtk.Align.CENTER)
        self._dot_widgets = []
        for _ in self._pages:
            d = Gtk.DrawingArea()
            d.set_size_request(9, 9)
            d.connect("draw", self._draw_dot)
            self._dot_widgets.append(d)
            self._dots.pack_start(d, False, False, 0)
        self._dots.set_margin_top(6)
        root.pack_start(self._dots, False, False, 0)

        # controls
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_margin_top(14)
        controls.set_margin_bottom(16)
        controls.set_margin_start(20)
        controls.set_margin_end(20)

        self._dont = Gtk.CheckButton(label="Don't show this again")
        controls.pack_start(self._dont, False, False, 0)

        self._skip = Gtk.Button(label="Skip")
        self._skip.get_style_context().add_class("flat")
        self._skip.connect("clicked", lambda _b: self._finish(open_plugins=False))
        controls.pack_start(self._skip, False, False, 0)

        controls.pack_start(Gtk.Label(), True, True, 0)  # spacer

        self._back = Gtk.Button(label="Back")
        self._back.get_style_context().add_class("flat")
        self._back.connect("clicked", lambda _b: self._go(-1))
        controls.pack_start(self._back, False, False, 0)

        self._next = Gtk.Button(label="Next")
        self._next.get_style_context().add_class("suggested-action")
        self._next.connect("clicked", lambda _b: self._go(1))
        controls.pack_start(self._next, False, False, 0)

        root.pack_start(controls, False, False, 0)
        self.add(root)

        self.connect("destroy", self._on_destroy)
        self.connect("key-press-event", self._on_key)
        self._timer = GLib.timeout_add(33, self._tick)
        self._update_controls()
        self.show_all()

    # ── animation ──
    def _on_draw(self, area, cr):
        w = area.get_allocated_width()
        h = area.get_allocated_height()
        t = (GLib.get_monotonic_time() - self._t0) / 1_000_000.0
        try:
            self._pages[self._index]["draw"](cr, w, h, t)
        except Exception:
            pass
        return False

    def _tick(self):
        self._area.queue_draw()
        return True

    def _draw_dot(self, area, cr):
        i = self._dot_widgets.index(area)
        cr.arc(4.5, 4.5, 4.0, 0, 2 * math.pi)
        if i == self._index:
            cr.set_source_rgba(*BLUE, 1)
        else:
            cr.set_source_rgba(*MUTE, 0.5)
        cr.fill()
        return False

    # ── navigation ──
    def _go(self, delta):
        ni = self._index + delta
        if ni >= len(self._pages):
            self._finish(open_plugins=False)
            return
        if ni < 0:
            return
        self._stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT
            if delta > 0 else Gtk.StackTransitionType.SLIDE_RIGHT)
        self._index = ni
        self._t0 = GLib.get_monotonic_time()
        self._stack.set_visible_child_name(f"p{ni}")
        self._update_controls()

    def _update_controls(self):
        last = self._index == len(self._pages) - 1
        self._back.set_sensitive(self._index > 0)
        self._next.set_label("Get started" if last else "Next")
        self._skip.set_visible(not last)
        for d in self._dot_widgets:
            d.queue_draw()

    def _on_key(self, _w, ev):
        from gi.repository import Gdk
        if ev.keyval in (Gdk.KEY_Right, Gdk.KEY_Return, Gdk.KEY_space):
            self._go(1)
            return True
        if ev.keyval == Gdk.KEY_Left:
            self._go(-1)
            return True
        if ev.keyval == Gdk.KEY_Escape:
            self._finish(open_plugins=False)
            return True
        return False

    def _finish(self, open_plugins=False):
        if self._dont.get_active():
            try:
                self._settings.set("show_welcome_dialog", False)
                self._settings.save()
            except Exception:
                pass
        cb = self._on_open_plugins if (open_plugins and self._on_open_plugins) else None
        self.destroy()
        if cb:
            try:
                cb()
            except Exception:
                pass

    def _on_destroy(self, _w):
        if getattr(self, "_timer", None):
            try:
                GLib.source_remove(self._timer)
            except Exception:
                pass
            self._timer = None


def show_onboarding(settings, on_open_plugins=None, parent=None) -> bool:
    """Show the animated onboarding. Returns False (and shows nothing) when the
    user has opted out via `show_welcome_dialog`. `parent` is accepted for API
    parity with the old welcome dialog but the window is shown standalone so its
    animation isn't blocked by a modal grab."""
    try:
        if not bool(settings.get("show_welcome_dialog")):
            return False
    except Exception:
        pass
    win = OnboardingWindow(settings, on_open_plugins=on_open_plugins)
    if parent is not None:
        try:
            win.set_transient_for(parent)
        except Exception:
            pass
    win.present()
    return True


if __name__ == "__main__":  # standalone preview / replay
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from settings import get_settings
        _s = get_settings()
    except Exception:
        class _Dummy(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)

            def set(self, k, v):
                self[k] = v

            def save(self):
                pass
        _s = _Dummy(show_welcome_dialog=True, hotkey="super+shift+y",
                    clipboard_hotkey="ctrl+super+v")
    try:
        from theme import install_premium_theme
        install_premium_theme(_s.get("theme", "dark") or "dark")
    except Exception:
        pass
    _w = OnboardingWindow(_s)
    _w.connect("destroy", Gtk.main_quit)
    Gtk.main()
