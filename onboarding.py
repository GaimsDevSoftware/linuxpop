"""Animated onboarding for LinuxPop.

A multi-page first-run walkthrough with hand-drawn, choreographed Cairo
animations. A friendly little guide ("Pip", the LinuxPop blob) walks new users
through: select text -> actions popup, how to summon it, how to add ready-made
plugins, and how to build their own with the recipe builder. Fully skippable,
with a "don't show this again" checkbox that flips the `show_welcome_dialog`
setting.

Entry point: ``show_onboarding(settings, on_open_plugins=None, parent=None)``.
"""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango  # noqa: E402

# ── brand palette (matches theme.py / the app icon) ──────────────────────────
BLUE = (0.357, 0.490, 0.961)    # #5B7DF5
VIOLET = (0.486, 0.227, 0.929)  # #7C3AED
PINK = (0.925, 0.282, 0.600)    # #EC4899
GREEN = (0.204, 0.780, 0.349)   # #34C759
AMBER = (0.97, 0.70, 0.30)
INK = (0.105, 0.130, 0.184)     # #1c2231 — dark text on the light stage
MUTE = (0.62, 0.66, 0.74)
STAGE = (0.985, 0.990, 1.0)     # near-white card the scenes sit on
STAGE_BORDER = (0.86, 0.88, 0.93)
PANEL = (0.95, 0.96, 0.985)

_ACCENTS = [BLUE, VIOLET, PINK, GREEN]


# ── easing / drawing helpers ─────────────────────────────────────────────────
def _clamp(x, lo=0.0, hi=1.0):
    return lo if x < lo else hi if x > hi else x


def _seg(t, a, b):
    """Map t in [a,b] -> [0,1], clamped (a phase within a timeline)."""
    return _clamp((t - a) / (b - a)) if b > a else 0.0


def _eo(x):           # ease-out cubic
    x = _clamp(x)
    return 1.0 - (1.0 - x) ** 3


def _eio(x):          # ease-in-out cubic
    x = _clamp(x)
    return 4 * x * x * x if x < 0.5 else 1.0 - (-2 * x + 2) ** 3 / 2.0


def _eob(x):          # ease-out-back (overshoot, for springy pops)
    x = _clamp(x) - 1.0
    c1 = 1.70158
    return 1.0 + (c1 + 1) * x ** 3 + c1 * x ** 2


def _bump(x):         # 0->1->0 hump
    return math.sin(_clamp(x) * math.pi)


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


def _text(cr, x, y, s, size, rgb, a=1.0, bold=False, center=False):
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    cr.set_font_size(size)
    if center:
        ext = cr.text_extents(s)
        x -= ext.width / 2 + ext.x_bearing
    cr.move_to(x, y)
    cr.set_source_rgba(*rgb, a)
    cr.show_text(s)


def _stage(cr, w, h):
    m = 14
    sx, sy, sw, sh = m, m, w - 2 * m, h - 2 * m
    _rrect(cr, sx, sy, sw, sh, 18)
    cr.set_source_rgba(*STAGE, 1.0)
    cr.fill_preserve()
    cr.set_source_rgba(*STAGE_BORDER, 1.0)
    cr.set_line_width(1.0)
    cr.stroke()
    return sx, sy, sw, sh


def _shadow(cr, x, y, w, h, r, blur=0.10):
    _rrect(cr, x + 1.5, y + 3, w, h, r)
    cr.set_source_rgba(0, 0, 0, blur)
    cr.fill()


# ── the guide character: "Pip" ───────────────────────────────────────────────
def _blink_at(t, period=3.4):
    p = t % period
    return _clamp(_bump(_seg(p, 0.0, 0.16))) if p < 0.16 else 0.0


def _mascot(cr, cx, cy, s=1.0, look=(0.0, 0.0), blink=0.0,
            mouth="smile", tilt=0.0, bob=0.0):
    cr.save()
    cr.translate(cx, cy + bob)
    cr.rotate(tilt)
    cr.scale(s, s)
    # soft drop shadow
    cr.save()
    cr.translate(0, 30)
    cr.scale(1, 0.32)
    cr.arc(0, 0, 24, 0, 2 * math.pi)
    cr.set_source_rgba(0, 0, 0, 0.10)
    cr.fill()
    cr.restore()
    # body — a rounded blob with the brand gradient + a little popup-tail
    bw, bh = 31, 28
    cr.move_to(0, bh + 9)                       # tail tip (popup pointer)
    cr.line_to(-8, bh - 3)
    cr.line_to(8, bh - 3)
    cr.close_path()
    _rrect(cr, -bw, -bh, 2 * bw, 2 * bh, 19)
    g = cairo.LinearGradient(-bw, -bh, bw, bh)
    g.add_color_stop_rgb(0, *BLUE)
    g.add_color_stop_rgb(1, *VIOLET)
    cr.set_source(g)
    cr.fill()
    # glossy highlight
    cr.save()
    cr.translate(-9, -13)
    cr.scale(1.0, 0.62)
    cr.arc(0, 0, 11, 0, 2 * math.pi)
    cr.set_source_rgba(1, 1, 1, 0.16)
    cr.fill()
    cr.restore()
    # eyes
    lx, ly = look[0] * 3.0, look[1] * 3.0
    happy = mouth == "happy"
    for sgn in (-1, 1):
        cr.save()
        cr.translate(sgn * 12, -3)
        if happy:                                # ^_^ curved-happy eyes
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(2.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.arc(0, 2, 5, math.pi * 1.15, math.pi * 1.85)
            cr.stroke()
        else:
            cr.scale(1.0, 1.0 - 0.92 * blink)
            cr.arc(0, 0, 7, 0, 2 * math.pi)
            cr.set_source_rgb(1, 1, 1)
            cr.fill()
            cr.arc(lx, ly + 0.5, 3.4, 0, 2 * math.pi)
            cr.set_source_rgb(*INK)
            cr.fill()
            cr.arc(lx - 1.1, ly - 1.1, 1.1, 0, 2 * math.pi)
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.fill()
        cr.restore()
    # cheeks
    for sgn in (-1, 1):
        cr.arc(sgn * 19, 6, 3.4, 0, 2 * math.pi)
        cr.set_source_rgba(*PINK, 0.35)
        cr.fill()
    # mouth
    cr.set_source_rgb(1, 1, 1)
    if mouth == "open" or mouth == "happy":
        cr.arc(0, 9, 5.2, 0, math.pi)
        cr.fill()
    else:
        cr.set_line_width(2.4)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.arc(0, 6, 6, 0.16 * math.pi, 0.84 * math.pi)
        cr.stroke()
    cr.restore()


def _speech(cr, x, y, text, a=1.0, tail_x=18):
    if a <= 0.01:
        return
    cr.save()
    cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL,
                        cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(14)
    ext = cr.text_extents(text)
    pad = 12
    w = ext.width + 2 * pad
    h = 30
    cr.translate(0, (1 - _eob(a)) * 6)
    _shadow(cr, x, y, w, h, 12)
    _rrect(cr, x, y, w, h, 12)
    cr.set_source_rgba(1, 1, 1, a)
    cr.fill_preserve()
    cr.set_source_rgba(*BLUE, 0.30 * a)
    cr.set_line_width(1.0)
    cr.stroke()
    # tail
    cr.move_to(x + tail_x, y + h - 1)
    cr.line_to(x + tail_x + 9, y + h - 1)
    cr.line_to(x + tail_x + 2, y + h + 9)
    cr.close_path()
    cr.set_source_rgba(1, 1, 1, a)
    cr.fill()
    _text(cr, x + pad, y + h / 2 + 5, text, 14, INK, a, bold=True)
    cr.restore()


def _pointer(cr, x, y, scale=1.0, click=0.0):
    if click > 0:
        cr.arc(x, y, 7 + 12 * click, 0, 2 * math.pi)
        cr.set_source_rgba(*BLUE, 0.30 * (1 - click))
        cr.fill()
    cr.save()
    cr.translate(x, y)
    cr.scale(scale * (1 - 0.12 * click), scale * (1 - 0.12 * click))
    cr.move_to(0, 0)
    cr.line_to(0, 17)
    cr.line_to(4.5, 12.5)
    cr.line_to(7.5, 19)
    cr.line_to(10, 18)
    cr.line_to(7, 11.5)
    cr.line_to(12, 11.5)
    cr.close_path()
    cr.set_source_rgb(1, 1, 1)
    cr.set_line_width(2.6)
    cr.stroke_preserve()
    cr.set_source_rgb(*INK)
    cr.fill()
    cr.restore()


def _appwin(cr, x, y, w, h):
    _shadow(cr, x, y, w, h, 11, 0.13)
    _rrect(cr, x, y, w, h, 11)
    cr.set_source_rgb(1, 1, 1)
    cr.fill_preserve()
    cr.set_source_rgba(*STAGE_BORDER, 1)
    cr.set_line_width(1.0)
    cr.stroke()
    cr.save()
    _rrect(cr, x, y, w, h, 11)
    cr.clip()
    cr.rectangle(x, y, w, 22)
    cr.set_source_rgb(0.945, 0.955, 0.975)
    cr.fill()
    cr.restore()
    for i, c in enumerate([(0.96, 0.46, 0.45), (0.97, 0.78, 0.36),
                           (0.40, 0.80, 0.46)]):
        cr.arc(x + 13 + i * 13, y + 11, 3.6, 0, 2 * math.pi)
        cr.set_source_rgb(*c)
        cr.fill()


def _popupbar(cr, x, y, n, a, icons=None):
    if a <= 0.01:
        return
    w, h = 28 + n * 40, 40
    _shadow(cr, x, y, w, h, 11)
    _rrect(cr, x, y, w, h, 11)
    cr.set_source_rgba(1, 1, 1, a)
    cr.fill_preserve()
    cr.set_source_rgba(*BLUE, 0.18 * a)
    cr.set_line_width(1.0)
    cr.stroke()
    for i in range(n):
        cxx = x + 24 + i * 40
        appear = _eob(_clamp((a - 0.2) * 1.2) ) if a < 1 else 1.0
        cr.arc(cxx, y + h / 2, 12 * appear, 0, 2 * math.pi)
        cr.set_source_rgba(*_ACCENTS[i % 4], a)
        cr.fill()
        cr.set_source_rgba(1, 1, 1, a)
        cr.set_line_width(2.0)
        cr.arc(cxx, y + h / 2, 5 * appear, 0, 2 * math.pi)
        cr.stroke()
    return w


def _keycap(cr, x, y, label, press, accent=BLUE):
    kw = 26 + 15 * len(label)
    dy = 4 * press
    _rrect(cr, x, y + dy, kw, 40, 9)
    g = cairo.LinearGradient(x, y, x, y + 40)
    if press > 0.2:
        cr.set_source_rgba(*accent, 0.18 + 0.8 * press)
    else:
        g.add_color_stop_rgb(0, 1, 1, 1)
        g.add_color_stop_rgb(1, 0.92, 0.93, 0.96)
        cr.set_source(g)
    cr.fill_preserve()
    cr.set_source_rgba(*accent, 0.5)
    cr.set_line_width(1.4)
    cr.stroke()
    _text(cr, x + kw / 2, y + dy + 26, label, 15,
          (1, 1, 1) if press > 0.45 else INK, bold=True, center=True)
    return kw


def _plugcard(cr, x, y, w, h, accent, name, a=1.0, lift=0.0, check=0.0):
    cr.save()
    cr.translate(0, -lift)
    _shadow(cr, x, y, w, h, 12, 0.10 + 0.10 * lift / 10)
    _rrect(cr, x, y, w, h, 12)
    cr.set_source_rgba(1, 1, 1, a)
    cr.fill_preserve()
    cr.set_source_rgba(*(accent if lift > 0.5 else STAGE_BORDER),
                       (0.7 if lift > 0.5 else 1.0) * a)
    cr.set_line_width(1.0 + lift / 8)
    cr.stroke()
    cr.arc(x + 22, y + h / 2, 12, 0, 2 * math.pi)
    cr.set_source_rgba(*accent, a)
    cr.fill()
    _text(cr, x + 42, y + h / 2 - 4, name, 12.5, INK, a, bold=True)
    _bar(cr, x + 42, y + h / 2 + 6, w - 64, 5, (0.84, 0.86, 0.92), a)
    if check > 0:
        cr.arc(x + w - 15, y + 14, 8 * _eob(check), 0, 2 * math.pi)
        cr.set_source_rgba(*GREEN, a)
        cr.fill()
        if check > 0.55:
            cr.set_source_rgb(1, 1, 1)
            cr.set_line_width(1.8)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(x + w - 18.5, y + 14)
            cr.line_to(x + w - 16, y + 17)
            cr.line_to(x + w - 11.5, y + 11)
            cr.stroke()
    cr.restore()


def _gear(cr, cx, cy, R, ang, rgb):
    teeth = 8
    cr.save()
    cr.translate(cx, cy)
    cr.rotate(ang)
    cr.set_source_rgba(*rgb, 1)
    for i in range(teeth):
        a = i * 2 * math.pi / teeth
        cr.save()
        cr.rotate(a)
        _rrect(cr, -3.2, -R - 4, 6.4, 8, 2)
        cr.fill()
        cr.restore()
    cr.arc(0, 0, R, 0, 2 * math.pi)
    cr.fill()
    cr.set_source_rgb(1, 1, 1)
    cr.arc(0, 0, R * 0.42, 0, 2 * math.pi)
    cr.fill()
    cr.restore()


def _confetti(cr, cx, cy, t, n=16):
    for i in range(n):
        ph = (i * 0.137) % 1.0
        life = (t * 0.6 + ph) % 1.0
        ang = i * 2.399
        spd = 60 + 50 * ((i * 7) % 5) / 4.0
        x = cx + math.cos(ang) * spd * life
        y = cy + math.sin(ang) * spd * life + 70 * life * life
        a = 1 - life
        cr.save()
        cr.translate(x, y)
        cr.rotate(t * 3 + i)
        col = _ACCENTS[i % 4]
        cr.rectangle(-3, -3, 6, 6)
        cr.set_source_rgba(*col, a)
        cr.fill()
        cr.restore()


# ── scene 1: select text -> actions popup ────────────────────────────────────
def _draw_how(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    T = 6.8
    lt = t % T
    fade = _eo(_seg(lt, 0, 0.3)) * (1 - _seg(lt, T - 0.4, T))
    # mock app window on the left
    ax, ay, aw, ah = sx + 26, sy + 30, sw * 0.60, sh - 60
    _appwin(cr, ax, ay, aw, ah)
    lx = ax + 18
    ys = [ay + 40, ay + 66, ay + 92, ay + 118]
    wid = [aw - 56, aw - 38, aw - 74, aw - 30]
    for yy, ww in zip(ys, wid):
        _bar(cr, lx, yy, ww, 9, (0.81, 0.84, 0.90), fade)
    # cursor glides in and drags a selection across line 2 (index 1)
    sel = _eio(_seg(lt, 0.9, 1.9))
    selw = wid[1] * sel
    if sel > 0:
        _rrect(cr, lx - 4, ys[1] - 5, selw + 8, 19, 4)
        cr.set_source_rgba(*BLUE, 0.28 * fade)
        cr.fill()
        _bar(cr, lx, ys[1], selw, 9, BLUE, fade)
    # popup springs up under the selection
    pa = _seg(lt, 2.0, 2.5)
    popup_w = 0
    if pa > 0:
        spr = _eob(pa)
        px = lx - 6
        py = ys[1] + 22 + (1 - spr) * 10
        cr.save()
        cr.translate(px + 70, py + 20)
        cr.scale(0.6 + 0.4 * spr, 0.6 + 0.4 * spr)
        cr.translate(-(px + 70), -(py + 20))
        popup_w = _popupbar(cr, px, py, 4, fade)
        cr.restore()
        # cursor moves onto first action and clicks
        click = _bump(_seg(lt, 3.6, 4.3))
        cxp = px + 24 + 18 * (1 - _eo(_seg(lt, 2.9, 3.7)))
        cyp = py + 20 + 30 * (1 - _eo(_seg(lt, 2.9, 3.7)))
        if lt > 2.7:
            _pointer(cr, cxp, cyp, 1.05, click)
        # a little result chip flies out after the click
        rr = _seg(lt, 4.2, 5.0)
        if rr > 0:
            ry = py - 8 - 16 * _eo(rr)
            _rrect(cr, px + 6, ry, 78, 22, 8)
            cr.set_source_rgba(*GREEN, 0.16 * fade * (1 - _seg(lt, T - 1.0, T)))
            cr.fill()
            cr.set_source_rgba(*GREEN, fade)
            cr.set_line_width(2)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(px + 16, ry + 11)
            cr.line_to(px + 21, ry + 16)
            cr.line_to(px + 30, ry + 6)
            cr.stroke()
            _text(cr, px + 38, ry + 15, "done", 11, GREEN, fade, bold=True)
    # the guide enters from the right and points at the popup
    mx = sx + sw - 70
    enter = _eo(_seg(lt, 2.3, 3.1))
    my = sy + sh / 2 - 6
    look = (-0.8, 0.2)
    _mascot(cr, mx, my, 1.0 * fade if fade > 0 else 0.0,
            look=look, blink=_blink_at(t),
            mouth="open" if 3.0 < lt < 4.6 else "smile",
            bob=2 * math.sin(t * 2.2), )
    _speech(cr, mx - 150, my - 56,
            "Pick anything!" if lt < 4.0 else "Nice!",
            a=fade * _eo(_seg(lt, 3.0, 3.5)) * (1 - _seg(lt, T - 0.8, T)))


# ── scene 2: summon it your way (hotkey + clipboard) ─────────────────────────
def _draw_summon(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    keys1 = getattr(_draw_summon, "keys", ["Super", "Shift", "Y"])
    keys2 = getattr(_draw_summon, "clip_keys", ["Ctrl", "Super", "V"])
    T = 8.0
    lt = t % T
    beat2 = lt >= 4.0
    keys = keys2 if beat2 else keys1
    base = (lt - 4.0) if beat2 else lt
    label = "Clipboard history" if beat2 else "Popup hotkey"
    accent = VIOLET if beat2 else BLUE
    swap = _bump(_seg(lt % 4.0, 0.0, 0.25))  # tiny flash on switch
    cx = sx + sw / 2 + 18
    cy = sy + 50
    # keycaps press in sequence
    widths = [26 + 15 * len(k) for k in keys]
    total = sum(widths) + 26 * (len(keys) - 1)
    x = cx - total / 2
    for i, k in enumerate(keys):
        press = (_seg(base, 0.5 + i * 0.30, 0.72 + i * 0.30)
                 - _seg(base, 1.5, 1.72))
        kw = _keycap(cr, x, cy, k, press, accent)
        if i < len(keys) - 1:
            _text(cr, x + kw + 6, cy + 26, "+", 17, MUTE, center=False)
        x += kw + 26
    _text(cr, cx, cy - 18, label, 13, accent, bold=True, center=True)
    # result of the combo
    if not beat2:
        blip = _bump(_seg(base, 1.6, 2.9))
        if blip > 0:
            _popupbar(cr, cx - 64, cy + 64, 3, blip)
    else:
        rise = _eo(_seg(base, 1.6, 2.4))
        if rise > 0:
            lx = cx - 90
            ly = cy + 60
            _shadow(cr, lx, ly, 180, 70, 10)
            _rrect(cr, lx, ly, 180, 70, 10)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(*STAGE_BORDER, 1)
            cr.set_line_width(1)
            cr.stroke()
            for r in range(3):
                ry = ly + 12 + r * 18
                ra = _eo(_seg(base, 1.7 + r * 0.18, 2.3 + r * 0.18))
                cr.arc(lx + 16, ry + 4, 4, 0, 2 * math.pi)
                cr.set_source_rgba(*_ACCENTS[r], ra)
                cr.fill()
                _bar(cr, lx + 28, ry + 1, (140) * ra, 7, (0.82, 0.85, 0.91))
    # guide on the left, hops with the key presses
    hop = abs(math.sin(base * 3.0)) * 6 if base < 1.6 else 0
    _mascot(cr, sx + 64, sy + sh / 2 + 6, 1.0,
            look=(0.6, -0.2), blink=_blink_at(t),
            mouth="open" if base < 1.8 else "smile", bob=-hop)
    _speech(cr, sx + 30, sy + 20,
            "Press it!" if not beat2 else "...or this one",
            a=_eo(_seg(base, 0.2, 0.7)) * (1 - _seg(base, 3.4, 4.0)))


# ── scene 3: add ready-made plugins (a little store) ─────────────────────────
_PLUGINS = [("Send to AI", BLUE), ("Clipboard", VIOLET), ("Transforms", PINK),
            ("Word count", GREEN), ("QR code", AMBER), ("Search", BLUE)]


def _draw_plugins(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    T = 7.6
    lt = t % T
    # shelf of cards (top), installed tray (bottom)
    cols = 3
    gap = 12
    cw = (sw - 150 - (cols - 1) * gap) / cols
    ch = 38
    ox = sx + 24
    oy = sy + 26
    # which card is being grabbed/installed this loop
    picks = [0, 3, 4]
    stagei = int(_clamp(lt / 2.4, 0, 2.999))
    pick = picks[stagei]
    local = (lt - stagei * 2.4)
    installed_count = stagei + (1 if local > 1.9 else 0)
    for idx, (name, acc) in enumerate(_PLUGINS):
        r, c = divmod(idx, cols)
        x = ox + c * (cw + gap)
        y = oy + r * (ch + gap)
        appear = _eo(_seg(lt, 0.05 * idx, 0.4 + 0.05 * idx))
        already = idx in picks[:stagei]
        if already:
            continue
        lift = 0.0
        fly = 0.0
        if idx == pick:
            lift = _bump(_seg(local, 0.2, 1.9)) * 10
            fly = _eio(_seg(local, 1.1, 1.9))
        if fly > 0:
            # animate toward the installed tray slot
            tx = sx + sw - 118
            ty = sy + sh - 54 + stagei * 0
            x = x + (tx - x) * fly
            y = y + (ty - y) * fly
            sc = 1 - 0.35 * fly
            cr.save()
            cr.translate(x + cw / 2, y + ch / 2)
            cr.scale(sc, sc)
            cr.translate(-(x + cw / 2), -(y + ch / 2))
            _plugcard(cr, x, y, cw, ch, acc, name, appear, lift,
                      check=_seg(local, 1.7, 2.0))
            cr.restore()
        else:
            _plugcard(cr, x, y, cw, ch, acc, name, appear, lift,
                      check=0.0)
    # installed tray
    tx, ty, tw, th = sx + sw - 130, sy + 28, 108, sh - 56
    _rrect(cr, tx, ty, tw, th, 12)
    cr.set_source_rgba(*PANEL, 1)
    cr.fill_preserve()
    cr.set_source_rgba(*GREEN, 0.35)
    cr.set_line_width(1.2)
    cr.stroke()
    _text(cr, tx + tw / 2, ty + 18, "Installed", 11.5, GREEN, bold=True,
          center=True)
    for i in range(installed_count):
        iy = ty + 30 + i * 26
        acc = _PLUGINS[picks[i]][1]
        _rrect(cr, tx + 10, iy, tw - 20, 20, 6)
        cr.set_source_rgb(1, 1, 1)
        cr.fill()
        cr.arc(tx + 22, iy + 10, 6, 0, 2 * math.pi)
        cr.set_source_rgba(*acc, 1)
        cr.fill()
        _bar(cr, tx + 34, iy + 7, tw - 56, 6, (0.84, 0.86, 0.92))
    # guide shops along the shelf
    mx = ox + (pick % cols) * (cw + gap) + cw / 2
    my = oy - 30 + _bump(_seg(local, 0.0, 1.0)) * -4
    _mascot(cr, sx + 40, sy + sh - 36, 0.92, look=(0.5, -0.4),
            blink=_blink_at(t), mouth="open" if local > 1.6 else "smile",
            bob=2 * math.sin(t * 2))
    _speech(cr, sx + 18, sy + sh - 100, "One click!",
            a=_eo(_seg(local, 1.6, 2.0)) * (1 - _seg(local, 2.2, 2.4)))


# ── scene 4: build your own (recipe workshop) ────────────────────────────────
def _draw_recipe(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    T = 7.4
    lt = t % T
    blocks = [("When", "text is a URL", BLUE),
              ("Then", "open in browser", VIOLET),
              ("Run", "your action", GREEN)]
    bw, bh = 132, 56
    gap = 18
    total = len(blocks) * bw + (len(blocks) - 1) * gap
    bx = sx + (sw - total) / 2
    by = sy + 44
    # connectors
    for i in range(len(blocks) - 1):
        x0 = bx + (i + 1) * bw + i * gap
        ln = _eo(_seg(lt, 0.9 + i * 0.25, 1.4 + i * 0.25))
        cr.set_source_rgba(*MUTE, 0.7)
        cr.set_line_width(2.2)
        cr.move_to(x0, by + bh / 2)
        cr.line_to(x0 + gap * ln, by + bh / 2)
        cr.stroke()
    snap = 0.0
    for i, (head, sub, acc) in enumerate(blocks):
        appear = _eo(_seg(lt, 0.15 * i, 0.55 + 0.15 * i))
        if appear <= 0.01:
            continue
        # slide in from the side + a snap settle
        sd = (1 - _eob(_seg(lt, 0.15 * i, 0.85 + 0.15 * i))) * (40 + i * 10)
        x = bx + i * (bw + gap) + sd
        snap = max(snap, _bump(_seg(lt, 0.7 + i * 0.15, 1.1 + i * 0.15)))
        cr.save()
        _shadow(cr, x, by, bw, bh, 12)
        _rrect(cr, x, by, bw, bh, 12)
        cr.set_source_rgba(1, 1, 1, appear)
        cr.fill_preserve()
        cr.set_source_rgba(*acc, 0.55 * appear)
        cr.set_line_width(1.6)
        cr.stroke()
        _rrect(cr, x, by, 6, bh, 3)
        cr.set_source_rgba(*acc, appear)
        cr.fill()
        _text(cr, x + 18, by + 24, head, 14, INK, appear, bold=True)
        _text(cr, x + 18, by + 42, sub, 11.5, MUTE, appear)
        cr.restore()
    # a gear turns while it "compiles", then a new button drops into a popup
    gear_a = _seg(lt, 1.8, 2.4) * (1 - _seg(lt, 3.4, 3.8))
    if gear_a > 0:
        _gear(cr, sx + sw / 2, by + bh + 34, 12, lt * 4, (*MUTE,))
        _gear(cr, sx + sw / 2 + 20, by + bh + 42, 8, -lt * 5, (*MUTE,))
    built = _seg(lt, 3.4, 4.0)
    if built > 0:
        py = by + bh + 26 + (1 - _eob(built)) * 14
        pw = _popupbar(cr, sx + sw / 2 - 64, py, 3, built)
        # the new custom button glows
        glow = _bump(_seg(lt, 4.0, 5.4))
        cr.arc(sx + sw / 2 - 40, py + 20, 12 + 8 * glow, 0, 2 * math.pi)
        cr.set_source_rgba(*GREEN, 0.30 * glow)
        cr.fill()
    _mascot(cr, sx + sw - 58, by + bh + 30, 0.92,
            look=(-0.5, -0.3), blink=_blink_at(t),
            mouth="happy" if lt > 3.6 else "smile", bob=2 * math.sin(t * 2.4))
    _speech(cr, sx + sw - 218, by + bh - 4, "You built a plugin!",
            a=_eo(_seg(lt, 3.8, 4.3)) * (1 - _seg(lt, T - 0.8, T)))


# ── scene 5: you're all set ──────────────────────────────────────────────────
def _draw_done(cr, w, h, t):
    sx, sy, sw, sh = _stage(cr, w, h)
    cx, cy = sx + sw / 2, sy + sh / 2 + 6
    # tray hint: an arrow points up to a little "Settings & Plugins" tag top-right
    tagx, tagy = sx + sw - 150, sy + 16
    _rrect(cr, tagx, tagy, 134, 26, 8)
    cr.set_source_rgba(*PANEL, 1)
    cr.fill_preserve()
    cr.set_source_rgba(*BLUE, 0.30)
    cr.set_line_width(1)
    cr.stroke()
    _text(cr, tagx + 12, tagy + 17, "Settings & Plugins", 11.5, INK, bold=True)
    ap = _bump((t % 2.0) / 2.0)
    cr.set_source_rgba(*BLUE, 0.8)
    cr.set_line_width(2.4)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    axx = tagx + 30
    cr.move_to(axx, tagy + 40 + ap * 4)
    cr.line_to(axx, tagy + 30)
    cr.move_to(axx - 5, tagy + 35)
    cr.line_to(axx, tagy + 30)
    cr.line_to(axx + 5, tagy + 35)
    cr.stroke()
    # confetti + the guide waving
    if t > 0.3:
        _confetti(cr, cx, cy - 6, t)
    wave = math.sin(t * 4) * 0.18
    _mascot(cr, cx, cy, 1.5, look=(0, 0.05), blink=_blink_at(t),
            mouth="happy", tilt=wave, bob=3 * math.sin(t * 2.2))
    _text(cr, cx, cy + 68, "Now select some text and try it!", 14,
          INK, bold=True, center=True)


def _build_pages(settings):
    def _g(k, d):
        try:
            v = settings.get(k)
            return v if v else d
        except Exception:
            return d
    popup_key = _g("hotkey", "super+shift+y")
    clip_key = _g("clipboard_hotkey", "ctrl+super+v")

    def _fmt(combo):
        return "+".join(p.strip().capitalize()
                        for p in combo.split("+") if p.strip())

    _draw_summon.keys = [p.strip().capitalize()
                         for p in popup_key.split("+") if p.strip()]
    _draw_summon.clip_keys = [p.strip().capitalize()
                              for p in clip_key.split("+") if p.strip()]
    return [
        dict(title="Select text, get instant actions",
             body="Highlight anything in any app — a link, an error, a "
                  "paragraph — and LinuxPop pops up a little bar of actions "
                  "made for what you picked.",
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
                  "snap together your own action: match some text, then run a "
                  "command or open a URL. That's a real plugin.",
             draw=_draw_recipe),
        dict(title="You're all set",
             body="Everything lives in the tray icon (top-right): Settings, "
                  "Plugins, and your own buttons. Off you go!",
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

        self.set_default_size(700, 600)
        self.set_resizable(False)
        self.set_position(Gtk.WindowPosition.CENTER)
        try:
            self.set_icon_name("linuxpop")
        except Exception:
            pass

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._area = Gtk.DrawingArea()
        self._area.set_size_request(-1, 280)
        self._area.connect("draw", self._on_draw)
        self._area.set_margin_top(16)
        self._area.set_margin_start(22)
        self._area.set_margin_end(22)
        root.pack_start(self._area, False, False, 0)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(300)
        self._stack.set_homogeneous(True)
        for i, pg in enumerate(self._pages):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            box.set_margin_top(12)
            box.set_margin_start(44)
            box.set_margin_end(44)
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
            body.set_max_width_chars(54)
            box.pack_start(body, False, False, 0)
            self._stack.add_named(box, f"p{i}")
        root.pack_start(self._stack, True, True, 0)

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

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_margin_top(14)
        controls.set_margin_bottom(16)
        controls.set_margin_start(22)
        controls.set_margin_end(22)
        self._dont = Gtk.CheckButton(label="Don't show this again")
        controls.pack_start(self._dont, False, False, 0)
        self._skip = Gtk.Button(label="Skip")
        self._skip.get_style_context().add_class("flat")
        self._skip.connect("clicked", lambda _b: self._finish(open_plugins=False))
        controls.pack_start(self._skip, False, False, 0)
        controls.pack_start(Gtk.Label(), True, True, 0)
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

    def _go(self, delta):
        ni = self._index + delta
        if ni >= len(self._pages):
            self._finish(open_plugins=False)
            return
        if ni < 0:
            return
        self._stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT if delta > 0
            else Gtk.StackTransitionType.SLIDE_RIGHT)
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
