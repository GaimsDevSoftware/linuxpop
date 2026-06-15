#!/usr/bin/env python3
"""Latent Charge — a single-plate expression of LinuxPop's visual system.
Rendered at 2x and downsampled (LANCZOS) for crisp edges + type."""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONTS = Path("/home/robert/.config/Claude/local-agent-mode-sessions/skills-plugin/"
             "023ffcbe-b5e5-44cf-b821-e3d9dce9aefb/7062840c-29fc-4380-a5c5-8934961e3bd6/"
             "skills/canvas-design/canvas-fonts")
OUT = Path("/home/robert/linuxpop-wl/docs/design/latent-charge-plate.png")

S = 2                      # supersample
W, H = 2000, 2800
def px(v): return int(round(v * S))

# ---- palette (LinuxPop real tokens) -------------------------------------
PAPER  = (240, 243, 250)
PAPER2 = (233, 237, 246)
INK    = (26, 31, 46)      # near-black ink
INK2   = (92, 101, 122)    # secondary label
FAINT  = (197, 204, 219)   # latency marks
HAIR   = (205, 211, 225)   # hairlines
G0     = (91, 125, 245)    # #5B7DF5 charge start
G1     = (124, 58, 237)    # #7C3AED charge end
PINK   = (236, 72, 153)    # #EC4899 stray ion
DEEP   = (22, 26, 36)      # #161A24 popup ink

img = Image.new("RGB", (W * S, H * S), PAPER)
d = ImageDraw.Draw(img, "RGBA")

def font(name, size):
    return ImageFont.truetype(str(FONTS / name), px(size))

F_DISP   = lambda s: font("Italiana-Regular.ttf", s)
F_MONO   = lambda s: font("DMMono-Regular.ttf", s)
F_GEO    = lambda s: font("Jura-Light.ttf", s)
F_GEOM   = lambda s: font("Jura-Medium.ttf", s)

# ---- helpers ------------------------------------------------------------
def tracked(xy, text, fnt, fill, track, anchor_left=True, center_x=None):
    """Draw text with letter-spacing (track in logical px). Returns width."""
    t = px(track)
    widths = [d.textlength(c, font=fnt) for c in text]
    total = sum(widths) + t * (len(text) - 1 if text else 0)
    x = px(xy[0]); y = px(xy[1])
    if center_x is not None:
        x = px(center_x) - total / 2
    elif not anchor_left:
        x = px(xy[0]) - total
    for c, w in zip(text, widths):
        d.text((x, y), c, font=fnt, fill=fill)
        x += w + t
    return total / S

def vgrad(w, h, c0, c1, angle=90):
    """Linear gradient image, angle degrees (0=L→R, 90=T→B, 45=diag)."""
    base = Image.new("RGB", (w, h))
    pa = base.load()
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    proj = [(x * dx + y * dy) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    lo, hi = min(proj), max(proj)
    for y in range(h):
        for x in range(w):
            t = (x * dx + y * dy - lo) / (hi - lo)
            pa[x, y] = tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
    return base

# ---- 0. latency field: a fine grid of tiny I-beam carets ---------------
# The quiet potential from which the charge surfaces. Extremely subtle.
def caret(cx, cy, h, col):
    half = h / 2
    d.line([(px(cx), px(cy - half)), (px(cx), px(cy + half))], fill=col, width=max(1, S))
    for s in (-1, 1):
        d.line([(px(cx - 3), px(cy + s * half)), (px(cx + 3), px(cy + s * half))],
               fill=col, width=max(1, S))

# contained to the upper OBSERVATION zone only - the lower documentation
# (spectrum, samples, anchor) sits on clean, undisturbed paper.
field_l, field_r, field_t, field_b = 200, 1800, 556, 1150
step = 64
faint_col = FAINT + (78,)
rows = []
y = field_t
while y <= field_b:
    rows.append(y); y += step
for ry in rows:
    x = field_l
    while x <= field_r:
        caret(x, ry, 18, faint_col)
        x += step

# ---- 1. selection band (the charged fragment) --------------------------
# One row of the latency field is "selected" - a faint periwinkle wash,
# the moment a fragment becomes actionable. The specimen surfaces from it.
sel_row = 1092       # directly beneath the tail: the mark surfaces from it
sel_x0, sel_x1 = 596, 1404
sel_h = 46
d.rounded_rectangle([px(sel_x0), px(sel_row - sel_h/2), px(sel_x1), px(sel_row + sel_h/2)],
                    radius=px(7), fill=G0 + (34,))
d.rounded_rectangle([px(sel_x0), px(sel_row - sel_h/2), px(sel_x1), px(sel_row + sel_h/2)],
                    radius=px(7), outline=G0 + (120,), width=max(1, S))
# re-draw carets within the band in the charge colour (the fragment lit up)
x = field_l
while x <= field_r:
    if sel_x0 + 10 <= x <= sel_x1 - 10:
        caret(x, sel_row, 18, G1 + (150,))
    x += step

# ---- 2. THE SPECIMEN: the LinuxPop mark in the charge gradient ----------
# pill + downward tail + three punched dots, from linuxpop-tray-symbolic.svg
# (viewBox 16). Rendered large, gradient-filled, paper showing through dots.
MARK_W = 560
sx = MARK_W / 16.0                       # unit scale
mcx = 1000                               # centre x (logical)
mtop = 560                               # top y (logical)
def u(ux, uy): return (px(mcx - MARK_W/2 + ux * sx), px(mtop + uy * sx))

# build a hires mask of the mark
mw = px(MARK_W); mh = px(15 * sx)
mask = Image.new("L", (mw, mh), 0)
md = ImageDraw.Draw(mask)
def um(ux, uy): return (ux * sx * S, uy * sx * S)
# pill body (rounded rect 3.25,2 -> 12.75,11.5 ; r=2.25)
md.rounded_rectangle([um(3.25, 2)[0], um(3.25, 2)[1], um(12.75, 11.5)[0], um(12.75, 11.5)[1]],
                     radius=2.25 * sx * S, fill=255)
# tail triangle (9.2,11.5)-(8,14)-(6.8,11.5)
md.polygon([um(9.2, 11.3), um(8, 14), um(6.8, 11.3)], fill=255)
# punch three dots (r=1.15 at y 6.9)
for cxu in (4.6, 8.0, 11.4):
    cx_, cy_ = um(cxu, 6.9)
    r_ = 1.15 * sx * S
    md.ellipse([cx_ - r_, cy_ - r_, cx_ + r_, cy_ + r_], fill=0)
mask = mask.filter(ImageFilter.GaussianBlur(0.6))   # hairline AA

# soft shadow on the paper (the specimen sits just above the field)
shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
ox, oy = px(mcx - MARK_W/2), px(mtop)
sh_mask = mask.point(lambda v: int(v * 0.20))
sh_layer = Image.new("RGBA", (mw, mh), (40, 50, 90, 255))
sh_layer.putalpha(sh_mask)
sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(px(9)))
img.paste(sh_layer, (ox, oy + px(10)), sh_layer)

grad = vgrad(mw, mh, G0, G1, angle=58).convert("RGBA")
grad.putalpha(mask)
img.paste(grad, (ox, oy), grad)
d = ImageDraw.Draw(img, "RGBA")    # rebind after paste

# ---- 3. specimen annotations (measurement, clinical) --------------------
mark_bottom = mtop + 14 * sx
mark_left   = mcx - MARK_W/2
mark_right  = mcx + MARK_W/2
# top dimension line (width)
ty = mtop - 46
d.line([(px(mark_left), px(ty)), (px(mark_right), px(ty))], fill=INK2 + (200,), width=max(1, S))
for ex in (mark_left, mark_right):
    d.line([(px(ex), px(ty - 9)), (px(ex), px(ty + 9))], fill=INK2 + (200,), width=max(1, S))
tracked((0, ty - 44), "FORM  ·  CORNER RADIUS 2.25", F_MONO(15), INK2, 5, center_x=mcx)
# left leader to a dot
lead_y = mtop + 6.9 * sx
d.line([(px(mark_left - 120), px(lead_y)), (px(mark_left - 18), px(lead_y))],
       fill=INK2 + (180,), width=max(1, S))
d.ellipse([px(mark_left-124), px(lead_y-4), px(mark_left-116), px(lead_y+4)], fill=INK2)
tracked((200, lead_y - 64), "THREE", F_MONO(15), INK2, 5)
tracked((200, lead_y - 40), "ACTIONS", F_MONO(15), INK2, 5)
# right leader (the tail)
d.line([(px(mark_right + 18), px(mark_bottom - 1.6*sx)), (px(mark_right + 120), px(mark_bottom - 1.6*sx))],
       fill=INK2 + (180,), width=max(1, S))
d.ellipse([px(mark_right+116), px(mark_bottom-1.6*sx-4), px(mark_right+124), px(mark_bottom-1.6*sx+4)], fill=PINK)
tracked((1800, lead_y - 64), "SURFACES", F_MONO(15), INK2, 5, anchor_left=False)
tracked((1800, lead_y - 40), "ON  SELECT", F_MONO(15), INK2, 5, anchor_left=False)
# specimen tag (below the selection band)
tracked((0, 1232), "FIG. 01   THE MARK   ·   CAPSULE · TAIL · TRIAD", F_MONO(16),
        INK, 6, center_x=mcx)

# ---- header -------------------------------------------------------------
tracked((200, 196), "LINUXPOP", F_GEOM(17), INK, 9)
tracked((200, 224), "VISUAL  SYSTEM", F_MONO(13.5), INK2, 7)
tracked((1800, 196), "PLATE  I  /  IV", F_MONO(15), INK2, 6, anchor_left=False)
tracked((1800, 224), "EST. 0.9.0", F_MONO(13.5), INK2, 6, anchor_left=False)
d.line([(px(200), px(286)), (px(1800), px(286))], fill=HAIR, width=max(1, S))

# ---- 4. charge spectrum (calibrated band) ------------------------------
spec_y0, spec_h = 1470, 92
spec_x0, spec_x1 = 360, 1640
sw = px(spec_x1 - spec_x0)
band = vgrad(sw, px(spec_h), G0, G1, angle=0)
img.paste(band, (px(spec_x0), px(spec_y0)))
d = ImageDraw.Draw(img, "RGBA")
d.rectangle([px(spec_x0), px(spec_y0), px(spec_x1), px(spec_y0 + spec_h)],
            outline=INK + (60,), width=max(1, S))
# ticks across the spectrum
n = 16
for i in range(n + 1):
    tx = spec_x0 + (spec_x1 - spec_x0) * i / n
    h2 = 16 if i % 4 == 0 else 9
    d.line([(px(tx), px(spec_y0 - h2)), (px(tx), px(spec_y0))], fill=INK2 + (170,), width=max(1, S))
tracked((spec_x0, spec_y0 - 50), "5B7DF5", F_MONO(15), G0, 5)
tracked((spec_x1, spec_y0 - 50), "7C3AED", F_MONO(15), G1, 5, anchor_left=False)
tracked((0, spec_y0 + spec_h + 22), "CHARGE  SPECTRUM   ·   THE ONLY ENERGY PERMITTED",
        F_MONO(14), INK2, 6, center_x=1000)

# ---- 5. calibration swatches (the real system colours) -----------------
sw_y = 1820
swatches = [
    ("#5B7DF5", G0,  "CHARGE 0"),
    ("#7C3AED", G1,  "CHARGE 1"),
    ("#EC4899", PINK,"ION"),
    ("#161A24", DEEP,"INK / POPUP"),
    ("#E8ECF4", (232,236,244), "ON-DARK"),
    ("#9AA3B8", (154,163,184), "MUTE"),
    ("#F0F3FA", PAPER, "FIELD"),
]
cols = len(swatches)
gap = 40
total_w = 1600
cell = (total_w - gap * (cols - 1)) / cols
x0 = 200
box = 150
for i, (hexc, col, label) in enumerate(swatches):
    cx = x0 + i * (cell + gap) + cell / 2
    bx0, bx1 = cx - box/2, cx + box/2
    # firmer border on near-white samples so they read as deliberate
    lum = 0.299*col[0] + 0.587*col[1] + 0.114*col[2]
    ob = INK + (110,) if lum > 215 else INK + (55,)
    d.rounded_rectangle([px(bx0), px(sw_y), px(bx1), px(sw_y + box)], radius=px(10),
                        fill=col, outline=ob, width=max(1, S))
    tracked((0, sw_y + box + 22), hexc, F_MONO(15), INK, 3, center_x=cx)
    tracked((0, sw_y + box + 48), label, F_MONO(12.5), INK2, 3, center_x=cx)
tracked((0, sw_y - 40), "FIG. 02   CALIBRATED SAMPLES", F_MONO(16), INK, 6, center_x=1000)

# ---- 6. anchor + footer -------------------------------------------------
# the one quiet monumental word
tracked((0, 2300), "LATENT CHARGE", F_DISP(116), INK, 14, center_x=1000)
tracked((0, 2452), "QUIET FIELDS  /  THE LUMINOUS INSTANT OF EMERGENCE",
        F_MONO(15), INK2, 7, center_x=1000)
d.line([(px(200), px(2560)), (px(1800), px(2560))], fill=HAIR, width=max(1, S))
tracked((200, 2576), "A VISUAL PHILOSOPHY", F_MONO(13), INK2, 6)
tracked((1800, 2576), "OBSERVED & CALIBRATED  ·  PLATE I", F_MONO(13), INK2, 6, anchor_left=False)

# ---- registration corner marks -----------------------------------------
def reg(cx, cy):
    L = 22
    d.line([(px(cx - L), px(cy)), (px(cx + L), px(cy))], fill=INK2 + (200,), width=max(1, S))
    d.line([(px(cx), px(cy - L)), (px(cx), px(cy + L))], fill=INK2 + (200,), width=max(1, S))
    d.ellipse([px(cx-9), px(cy-9), px(cx+9), px(cy+9)], outline=INK2 + (200,), width=max(1, S))
for cx, cy in ((118, 118), (1882, 118), (118, 2682), (1882, 2682)):
    reg(cx, cy)

# ---- finish -------------------------------------------------------------
img = img.resize((W, H), Image.LANCZOS)
img.save(OUT)
print("wrote", OUT, img.size)
