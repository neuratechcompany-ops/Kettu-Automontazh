"""Caption style `chip`: white bold caps, emphasis as a filled rounded pill.

The pill has to fit the word exactly, so the word is measured with the very TTF
libass will render (Pillow reads the same file). Text lines are drawn once per
chunk and only the pill moves -- that reads as a slide, not a flicker.
"""
from __future__ import annotations

from pathlib import Path

from . import cards

FONTS = Path(__file__).parent / "fonts"


def ts(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):d}:{int(m):02d}:{s:05.2f}"


def esc(s):
    return s.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def font_for(st, size):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONTS / st["font_file"]), size)


def layout(disp, font, max_w):
    """Wrap into centred lines, keeping every word's measured advance width."""
    space = font.getlength(" ")
    lines, cur, curw = [], [], 0.0
    for w in disp:
        ww = font.getlength(w)
        nxt = ww if not cur else curw + space + ww
        if cur and nxt > max_w:
            lines.append((cur, curw))
            cur, curw = [(w, ww)], ww
        else:
            cur.append((w, ww))
            curw = nxt
    if cur:
        lines.append((cur, curw))
    return lines, space


def rounded(w, h, r):
    """ASS drawing path for a rounded rectangle, origin top-left."""
    r = int(max(1, min(r, h / 2, w / 2)))
    w, h = int(w), int(h)
    return (f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} "
            f"l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
            f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} "
            f"l 0 {r} b 0 0 0 0 {r} 0")


def events(chunks, st, W, H, size, marginv_px):
    """marginv_px is in PIXELS -- the same value that goes into the ASS style."""
    font = font_for(st, size)
    max_w = W * 0.86
    lh = size * 1.18
    pad = size * float(st.get("pill_pad", 0.30))
    ph = size * float(st.get("pill_h", 1.34))
    pill = cards.hex_ass(st.get("pill", "#4B41A5"))
    radius = ph * float(st.get("pill_r", 0.30))
    ev = []

    for ch in chunks:
        disp = [esc(w["w"]).upper() if st.get("upper") else esc(w["w"]) for w in ch]
        c0 = ch[0]["s"]
        c1 = max(ch[-1]["e"], c0 + 0.35)
        lines, space = layout(disp, font, max_w)
        bottom = H - marginv_px
        top = bottom - lh * len(lines)

        pos, k = {}, 0
        for li, (items, lw) in enumerate(lines):
            x = W / 2 - lw / 2
            for _word, ww in items:
                pos[k] = (li, x, x + ww)
                x += ww + space
                k += 1

        for j, w in enumerate(ch):
            if j not in pos:
                continue
            li, x0, x1 = pos[j]
            y = top + lh * (li + 0.5)
            a = c0 if j == 0 else w["s"]
            b = c1 if j == len(ch) - 1 else ch[j + 1]["s"]
            if b - a < 0.02:
                continue
            px = x0 - pad
            py = y - ph / 2 + size * 0.06
            ev.append(
                "Dialogue: 3,{},{},VE,,0,0,0,,".format(ts(a), ts(b))
                + "{" + "\\an7\\pos({:.0f},{:.0f})\\bord0\\shad0\\1c{}\\fad(60,60)\\p1".format(
                    px, py, pill) + "}"
                + rounded(x1 - x0 + 2 * pad, ph, radius)
                + "{\\p0}")

        for li, (items, _lw) in enumerate(lines):
            y = top + lh * (li + 0.5)
            ev.append(
                "Dialogue: 4,{},{},VE,,0,0,0,,".format(ts(c0), ts(c1))
                + "{" + "\\an5\\pos({},{:.0f})\\fad(80,80)".format(W // 2, y) + "}"
                + " ".join(w for w, _ in items))
    return ev
