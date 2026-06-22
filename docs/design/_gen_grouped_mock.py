#!/usr/bin/env python3
"""Mock of the category-grouped popup (collapsed vs expanded), built from the
real symbolic icon files so it reflects what ships."""
import os
import cairosvg

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.normpath(os.path.join(HERE, "..", "..", "icons"))


def icon_inner(name):
    svg = open(os.path.join(ICONS, name + ".svg")).read()
    return svg[svg.index(">", svg.index("<svg")) + 1: svg.rindex("</svg>")]


def place(name, x, y, s=34):
    return f'<g transform="translate({x},{y}) scale({s/48:.4f})">{icon_inner(name)}</g>'


W, H = 1180, 720
BG, PANEL, RULE = "#0E1016", "#161922", "#262B38"
INK, DIM, ACC = "#E8EAF2", "#6B7184", "#8AA0FF"
THIN = 'font-family="Helvetica Neue,Arial,sans-serif" font-weight="200"'
REG = 'font-family="Helvetica Neue,Arial,sans-serif" font-weight="400"'

p = [f'<rect width="{W}" height="{H}" fill="{BG}"/>']
p.append(f'<text x="80" y="104" font-size="58" {THIN} fill="{INK}" letter-spacing="1">Category grouping</text>')
p.append(f'<text x="84" y="136" font-size="16" {REG} fill="{DIM}" letter-spacing="4">FORMATTING &amp; MARKDOWN COLLAPSE BEHIND A CHIP · CLICK TO EXPAND</text>')
p.append(f'<line x1="80" y1="164" x2="{W-80}" y2="164" stroke="{RULE}" stroke-width="1"/>')


def pill(x, y, w, h):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h/2 if h<60 else 16}" '
            f'fill="{PANEL}" stroke="{RULE}" stroke-width="1.2"/>')


g = 44  # cell pitch

# --- collapsed -----------------------------------------------------------
p.append(f'<text x="80" y="232" font-size="15" {REG} fill="{DIM}" letter-spacing="3">COLLAPSED — what you see first</text>')
row = ["linuxpop-clipboard-symbolic", "linuxpop-format-symbolic", "linuxpop-md-symbolic", "linuxpop-dictionary-symbolic"]
bx, by, bh = 80, 256, 64
bw = g * len(row) + 24
p.append(pill(bx, by, bw, bh))
x = bx + 16
for name in row:
    p.append(place(name, x, by + 15))
    x += g
p.append(f'<text x="{bx+bw+30}" y="{by+30}" font-size="15" {REG} fill="{DIM}">copy · </text>')
p.append(f'<text x="{bx+bw+92}" y="{by+30}" font-size="15" {REG} fill="{ACC}">A▾ Formatting</text>')
p.append(f'<text x="{bx+bw+92}" y="{by+52}" font-size="15" {REG} fill="#E5484D">.md▾ Markdown</text>')

# --- expanded ------------------------------------------------------------
p.append(f'<text x="80" y="400" font-size="15" {REG} fill="{DIM}" letter-spacing="3">EXPANDED — after clicking the Markdown chip</text>')
ex, ey = 80, 424
row2 = ["linuxpop-md-bold-symbolic", "linuxpop-md-italic-symbolic", "linuxpop-md-strikethrough-symbolic",
        "linuxpop-md-highlight-symbolic", "linuxpop-md-quote-symbolic", "linuxpop-md-code-symbolic",
        "linuxpop-md-link-symbolic"]
ew = max(g * len(row) + 24, g * len(row2) + 24)
eh = 64 * 2 + 6
p.append(f'<rect x="{ex}" y="{ey}" width="{ew}" height="{eh}" rx="18" fill="{PANEL}" stroke="{RULE}" stroke-width="1.2"/>')
# row 1 (collapsed view, markdown chip now "active")
x = ex + 16
for name in row:
    box = ''
    if name == "linuxpop-md-symbolic":
        box = f'<rect x="{x-4}" y="{ey+11}" width="42" height="42" rx="11" fill="#ffffff" fill-opacity="0.07" stroke="{ACC}" stroke-opacity="0.5" stroke-width="1.2"/>'
    p.append(box + place(name, x, ey + 15))
    x += g
# divider
p.append(f'<line x1="{ex+14}" y1="{ey+66}" x2="{ex+ew-14}" y2="{ey+66}" stroke="{RULE}" stroke-width="1"/>')
# row 2 (members)
x = ex + 16
for name in row2:
    p.append(place(name, x, ey + 78))
    x += g
p.append(f'<text x="{ex+ew+30}" y="{ey+45}" font-size="15" {REG} fill="{DIM}">the 7 markdown</text>')
p.append(f'<text x="{ex+ew+30}" y="{ey+67}" font-size="15" {REG} fill="{DIM}">actions reveal on</text>')
p.append(f'<text x="{ex+ew+30}" y="{ey+89}" font-size="15" {REG} fill="{DIM}">the second row</text>')

p.append(f'<line x1="80" y1="{H-58}" x2="{W-80}" y2="{H-58}" stroke="{RULE}" stroke-width="1"/>')
p.append(f'<text x="80" y="{H-30}" font-size="13" {REG} fill="{DIM}" letter-spacing="2">popup_group_categories · the red .md tag marks every markdown action</text>')

svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">' + "".join(p) + "</svg>"
out = os.path.join(HERE, "category-grouping-mock.png")
cairosvg.svg2png(bytestring=svg.encode(), write_to=out, output_width=W * 2, output_height=H * 2)
print("wrote", out)
