#!/usr/bin/env python3
"""Generate the LinuxPop text-formatting icon set.

Two families, one house style (48x48, rounded indigo plate + hairline bezel
for colour; bare #f0f0f0 glyph for symbolic):

  linuxpop-format-*  rich-text formatting (Ctrl+B/I/U). The styled letter.
  linuxpop-md-*      markdown wrapping (**, ==, > ...). Same base glyph PLUS
                     a small monospace corner badge of the literal syntax it
                     inserts, so the user can tell the two mechanisms apart.

Renders a comparison specimen PNG via _gen_specimen.py separately.
"""
import glob
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.normpath(os.path.join(HERE, "..", "..", "icons"))

# --- house style ----------------------------------------------------------
TOP, BOT = "#6E8BF5", "#3F3AD0"          # formatting family gradient (indigo)
AMBER = "#FFD773"                         # the one sanctioned warm accent
GLYPH_SYM = "#f0f0f0"                      # symbolic ink (white-on-dark popup)
GLYPH_COL = "#ffffff"                      # ink on the colour plate

COLOR_HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" '
    'viewBox="0 0 48 48"><defs><linearGradient id="g" x1="0" y1="0" x2="0" '
    f'y2="1"><stop offset="0" stop-color="{TOP}"/><stop offset="1" '
    f'stop-color="{BOT}"/></linearGradient></defs>'
    '<rect x="2" y="2" width="44" height="44" rx="12" fill="url(#g)"/>'
    '<rect x="2.75" y="2.75" width="42.5" height="42.5" rx="11.3" fill="none" '
    'stroke="#ffffff" stroke-opacity="0.18" stroke-width="1.2"/>'
)
SYM_HEAD = ('<svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" '
            'viewBox="0 0 48 48">')
TAIL = "</svg>"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --- glyphs (c = ink colour; amber = highlight accent) --------------------
def g_bold(c, _a):
    return (f'<text x="24" y="34" font-family="Georgia,\'Times New Roman\',serif" '
            f'font-size="30" font-weight="700" fill="{c}" '
            f'text-anchor="middle">B</text>')


def g_italic(c, _a):
    return (f'<text x="24" y="34" font-family="Georgia,serif" font-style="italic" '
            f'font-size="30" font-weight="600" fill="{c}" '
            f'text-anchor="middle">I</text>')


def g_underline(c, _a):
    return (f'<text x="24" y="31" font-family="Georgia,serif" font-size="26" '
            f'font-weight="600" fill="{c}" text-anchor="middle">U</text>'
            f'<line x1="15" y1="37" x2="33" y2="37" stroke="{c}" '
            f'stroke-width="3" stroke-linecap="round"/>')


def g_strike(c, _a):
    return (f'<text x="24" y="33" font-family="Georgia,serif" font-size="27" '
            f'font-weight="600" fill="{c}" text-anchor="middle">S</text>'
            f'<line x1="13" y1="24" x2="35" y2="24" stroke="{c}" '
            f'stroke-width="3" stroke-linecap="round"/>')


def g_highlight(c, amber):
    if amber:
        band = f'<rect x="11" y="19.5" width="26" height="11" rx="3.2" fill="{AMBER}"/>'
    else:
        band = (f'<rect x="11" y="19.5" width="26" height="11" rx="3.2" '
                f'fill="{c}" fill-opacity="0.42"/>')
    return band + (f'<line x1="13" y1="35.5" x2="35" y2="35.5" stroke="{c}" '
                   f'stroke-width="2.6" stroke-linecap="round"/>')


def g_quote(c, _a):
    return (f'<rect x="12" y="15" width="4.6" height="18" rx="2.3" fill="{c}"/>'
            f'<g stroke="{c}" stroke-width="3" stroke-linecap="round">'
            f'<line x1="22.5" y1="20" x2="36" y2="20"/>'
            f'<line x1="22.5" y1="28" x2="32" y2="28"/></g>')


def g_code(c, _a):
    return (f'<g fill="none" stroke="{c}" stroke-width="3.2" '
            f'stroke-linecap="round" stroke-linejoin="round">'
            f'<polyline points="19,17 11,24 19,31"/>'
            f'<polyline points="29,17 37,24 29,31"/></g>')


def g_link(c, _a):
    ring = ('<rect x="-10.5" y="-6.5" width="21" height="13" rx="6.5" '
            f'fill="none" stroke="{c}" stroke-width="3.1"/>')
    return (f'<g transform="translate(18.5,29.5) rotate(-45)">{ring}</g>'
            f'<g transform="translate(29.5,18.5) rotate(-45)">{ring}</g>')


# --- category-chip glyphs -------------------------------------------------
# Each category carries its own signature accent so it's recognisable even at
# 16px: teal for Formatting, red for Markdown. Kept clean (no caret) for small
# legibility; the chip's tooltip and expand behaviour signal that it opens.
ACCENT_FORMAT = "#46C8B8"


def _page(c):
    return (f'<path d="M15 9 H29 L36 16 V37 a2 2 0 0 1 -2 2 H15 a2 2 0 0 1 -2 -2 '
            f'V11 a2 2 0 0 1 2 -2 Z" fill="none" stroke="{c}" stroke-width="2.4" '
            f'stroke-linejoin="round"/>'
            f'<path d="M29 9 V16 H36" fill="none" stroke="{c}" stroke-width="2.4" '
            f'stroke-linejoin="round"/>')


def g_cat_format(c, _a):
    # the word-processor "A" over a teal format bar (the classic font-format mark)
    return (f'<text x="24" y="32" font-family="Georgia,\'Times New Roman\',serif" '
            f'font-size="29" font-weight="700" fill="{c}" text-anchor="middle">A</text>'
            f'<rect x="14" y="36" width="20" height="4.6" rx="2.3" fill="{ACCENT_FORMAT}"/>')


def g_cat_md(c, _a):
    # a document with the red ".md" extension tag -- "a markdown file"
    tag = (f'<rect x="14.5" y="26" width="20" height="11" rx="2.6" fill="{RED}"/>'
           f'<text x="24.5" y="34.3" font-family="DejaVu Sans Mono,monospace" '
           f'font-size="8.5" font-weight="700" fill="#ffffff" text-anchor="middle">.md</text>')
    return _page(c) + tag


# --- corner earmark: a small red ".md" tag -------------------------------
RED = "#E5484D"


def badge(_token, _is_color):
    """A small red ".md" pill in the bottom-right.

    Uniform across the whole md family. The base glyph says *which* action;
    this red file-extension tag says, unmistakably, "markdown". Drawn the same
    on the symbolic and colour versions so it reads identically in the popup
    row and in the catalogue.
    """
    bw, bh = 21, 13
    bx, by = 44 - bw, 45 - bh            # bottom-right, inside the bezel
    pill = (f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="3.4" fill="{RED}"/>'
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="3.4" fill="none" '
            f'stroke="#ffffff" stroke-opacity="0.22" stroke-width="0.8"/>')
    txt = (f'<text x="{bx + bw/2:.1f}" y="{by + 9.7:.1f}" '
           f'font-family="DejaVu Sans Mono,monospace" font-size="9.5" '
           f'font-weight="700" fill="#ffffff" text-anchor="middle" '
           f'letter-spacing="0.2">.md</text>')
    return f'<g>{pill}{txt}</g>'


# (key, label, glyph fn)  -- rich-text formatting, NO badge
FORMAT_DEF = [
    ("bold", "Bold", g_bold),
    ("italic", "Italic", g_italic),
    ("underline", "Underline", g_underline),
]

# (key, label, syntax token, glyph fn) -- markdown, WITH corner badge
MD_DEF = [
    ("bold", "Bold", "**", g_bold),
    ("italic", "Italic", "*", g_italic),
    ("strikethrough", "Strikethrough", "~~", g_strike),
    ("highlight", "Highlight", "==", g_highlight),
    ("quote", "Quote", ">", g_quote),
    ("code", "Code", "`", g_code),
    ("link", "Link", "[]", g_link),
]


def _write(name, inner_sym, inner_col):
    with open(os.path.join(ICONS, f"{name}-symbolic.svg"), "w") as f:
        f.write(SYM_HEAD + inner_sym + TAIL)
    with open(os.path.join(ICONS, f"{name}.svg"), "w") as f:
        f.write(COLOR_HEAD + inner_col + TAIL)


def write_all():
    # clean any earlier generation so renames don't leave stale files
    for f in glob.glob(os.path.join(ICONS, "linuxpop-md-*.svg")) + \
            glob.glob(os.path.join(ICONS, "linuxpop-format-*.svg")):
        os.remove(f)

    for key, _label, fn in FORMAT_DEF:
        _write(f"linuxpop-format-{key}", fn(GLYPH_SYM, False), fn(GLYPH_COL, True))

    for key, _label, token, fn in MD_DEF:
        sym = fn(GLYPH_SYM, False) + badge(token, False)
        col = fn(GLYPH_COL, True) + badge(token, True)
        _write(f"linuxpop-md-{key}", sym, col)

    # category-chip icons (no per-item badge)
    for key, fn in (("format", g_cat_format), ("md", g_cat_md)):
        _write(f"linuxpop-{key}", fn(GLYPH_SYM, False), fn(GLYPH_COL, True))

    n = (len(FORMAT_DEF) + len(MD_DEF)) * 2 + 4
    print(f"wrote {n} icons to {ICONS}")


if __name__ == "__main__":
    write_all()
