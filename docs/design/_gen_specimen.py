#!/usr/bin/env python3
"""Render the Indigo Syntax specimen sheet: the two formatting families.

Rich-text (linuxpop-format-*) keeps the plain letterforms; markdown
(linuxpop-md-*) is the same base glyph plus a red ".md" earmark. Shown as
colour plates, popup glyphs, and a real-size popup row.
"""
import importlib.util
import os

import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "mdicons", os.path.join(HERE, "_gen_markdown_icons.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

SYM = M.GLYPH_SYM
W, H = 1480, 1060
BG, PANEL, RULE = "#0E1016", "#161922", "#262B38"
INK, DIM, ACC, RED = "#E8EAF2", "#6B7184", "#8AA0FF", "#E5484D"
THIN = 'font-family="Helvetica Neue,Arial,sans-serif" font-weight="200"'
REG = 'font-family="Helvetica Neue,Arial,sans-serif" font-weight="400"'
MONO = 'font-family="DejaVu Sans Mono,monospace"'


def place_color(glyph, earmark, x, y, s, uid):
    inner = M.COLOR_HEAD[M.COLOR_HEAD.index(">") + 1:] + glyph + earmark
    inner = inner.replace('id="g"', f'id="g{uid}"').replace("url(#g)", f"url(#g{uid})")
    return f'<g transform="translate({x},{y}) scale({s/48:.4f})">{inner}</g>'


def place_sym(glyph, earmark, x, y, s):
    return f'<g transform="translate({x},{y}) scale({s/48:.4f})">{glyph}{earmark}</g>'


def sym_swatch(glyph, earmark, cx, cy, tile=52, gs=48):
    return (f'<rect x="{cx-tile/2:.1f}" y="{cy}" width="{tile}" height="{tile}" '
            f'rx="13" fill="{PANEL}" stroke="{RULE}" stroke-width="1"/>'
            + place_sym(glyph, earmark, cx - gs/2, cy + (tile-gs)/2, gs))


p = [f'<rect width="{W}" height="{H}" fill="{BG}"/>']

# --- masthead -------------------------------------------------------------
p.append(f'<text x="80" y="116" font-size="70" {THIN} fill="{INK}" letter-spacing="2">Indigo Syntax</text>')
p.append(f'<text x="84" y="148" font-size="17" {REG} fill="{DIM}" letter-spacing="5">TWO FORMATTING LANGUAGES · ONE FAMILY</text>')
p.append(f'<text x="{W-80}" y="116" font-size="17" {MONO} fill="{ACC}" text-anchor="end">linuxpop-format-* · linuxpop-md-*</text>')
p.append(f'<text x="{W-80}" y="144" font-size="14" {MONO} fill="{DIM}" text-anchor="end">48 × 48 · symbolic + colour</text>')
p.append(f'<line x1="80" y1="176" x2="{W-80}" y2="176" stroke="{RULE}" stroke-width="1"/>')

# --- the core distinction, stated once, large ----------------------------
hy = 210
p.append(f'<text x="80" y="{hy+24}" font-size="14" {REG} fill="{DIM}" letter-spacing="3">THE DIFFERENCE</text>')
# rich-text B
p.append(place_color(M.g_bold(M.GLYPH_COL, True), "", 150, hy + 42, 96, "hb"))
p.append(f'<text x="266" y="{hy+82}" font-size="22" {REG} fill="{INK}">Rich-text</text>')
p.append(f'<text x="266" y="{hy+106}" font-size="14" {MONO} fill="{DIM}">presses Ctrl+B · styles in place</text>')
p.append(f'<text x="266" y="{hy+126}" font-size="13" {MONO} fill="{DIM}">linuxpop-format-bold</text>')
# markdown B
p.append(place_color(M.g_bold(M.GLYPH_COL, True), M.badge(None, True), 800, hy + 42, 96, "mb"))
p.append(f'<text x="916" y="{hy+82}" font-size="22" {REG} fill="{INK}">Markdown</text>')
p.append(f'<text x="916" y="{hy+106}" font-size="14" {MONO} fill="{DIM}">wraps in **bold** · the red .md tag</text>')
p.append(f'<text x="916" y="{hy+126}" font-size="13" {MONO} fill="{DIM}">linuxpop-md-bold</text>')
p.append(f'<line x1="80" y1="{hy+168}" x2="{W-80}" y2="{hy+168}" stroke="{RULE}" stroke-width="1"/>')

# --- two columns: families ------------------------------------------------
colA, colB = 80, 800
ty = hy + 210
p.append(f'<text x="{colA}" y="{ty}" font-size="14" {REG} fill="{DIM}" letter-spacing="3">RICH-TEXT · linuxpop-format-*</text>')
p.append(f'<text x="{colB}" y="{ty}" font-size="14" {REG} fill="{DIM}" letter-spacing="3">MARKDOWN · linuxpop-md-*</text>')

row_y = ty + 34
rh = 74

# rich-text rows
for i, (key, label, fn) in enumerate(M.FORMAT_DEF):
    y = row_y + i * rh
    p.append(sym_swatch(fn(SYM, False), "", colA + 30, y))
    p.append(place_color(fn(M.GLYPH_COL, True), "", colA + 74, y, 52, f"f{i}"))
    p.append(f'<text x="{colA+150}" y="{y+24}" font-size="22" {REG} fill="{INK}">{label}</text>')
    p.append(f'<text x="{colA+150}" y="{y+46}" font-size="14" {MONO} fill="{DIM}">Ctrl+{label[0]}</text>')

# markdown rows
for i, (key, label, token, fn) in enumerate(M.MD_DEF):
    y = row_y + i * rh
    amber = key == "highlight"
    p.append(sym_swatch(fn(SYM, amber), M.badge(None, False), colB + 30, y))
    p.append(place_color(fn(M.GLYPH_COL, amber), M.badge(None, True), colB + 74, y, 52, f"m{i}"))
    p.append(f'<text x="{colB+150}" y="{y+24}" font-size="22" {REG} fill="{INK}">{label}</text>')
    tok = token.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sample = f'{tok}text{tok[::-1]}' if key not in ("quote", "link") else (
        '&gt; text' if key == "quote" else '[text](url)')
    p.append(f'<text x="{colB+150}" y="{y+46}" font-size="14" {MONO} fill="{DIM}">{sample}</text>')

# --- footer: real-size popup row -----------------------------------------
fy = H - 96
p.append(f'<line x1="80" y1="{fy-18}" x2="{W-80}" y2="{fy-18}" stroke="{RULE}" stroke-width="1"/>')
p.append(f'<text x="80" y="{fy+8}" font-size="13" {REG} fill="{DIM}" letter-spacing="2">IN THE POPUP, AT SIZE — the red tag picks out the markdown actions</text>')
bar_x, bar_y, g = 80, fy + 22, 34
mix = [(M.g_bold, False, ""), (M.g_italic, False, ""), (M.g_underline, False, ""),
       (M.g_bold, False, M.badge(None, False)), (M.g_highlight, True, M.badge(None, False)),
       (M.g_quote, False, M.badge(None, False)), (M.g_code, False, M.badge(None, False)),
       (M.g_link, False, M.badge(None, False))]
bw = g * len(mix) + 30
p.append(f'<rect x="{bar_x}" y="{bar_y}" width="{bw}" height="50" rx="13" fill="{PANEL}" stroke="{RULE}" stroke-width="1"/>')
gx = bar_x + 15
for fn, amber, em in mix:
    p.append(place_sym(fn(SYM, amber), em, gx, bar_y + 9, g))
    gx += g
p.append(f'<text x="{bar_x+bw+28}" y="{bar_y+32}" font-size="13" {MONO} fill="{DIM}">'
         f'3 rich-text · 5 markdown</text>')

svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
       f'viewBox="0 0 {W} {H}">' + "".join(p) + "</svg>")
out = os.path.join(HERE, "markdown-icons-specimen.png")
cairosvg.svg2png(bytestring=svg.encode(), write_to=out, output_width=W * 2, output_height=H * 2)
print("wrote", out)
