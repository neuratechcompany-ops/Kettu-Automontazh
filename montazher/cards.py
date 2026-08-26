"""Graphic overlays, drawn entirely in ASS -- no extra render pass, no assets.

Two rules learned the hard way on real footage:
  * a full-frame card is a SCRIM over the live picture, not a black slide. Cutting
    to a black screen with big type reads as a 2000s title card.
  * anything with a background bar is placed from the measured face band, never
    from a fixed fraction -- a "safe" constant lands on the speaker's chin.

Times are given in SOURCE seconds (what the transcript shows) and mapped onto the
cut timeline here.
"""
from __future__ import annotations

import re
from pathlib import Path

from .core import load_json, log, words_path

FONT = "Montserrat"

ORDINALS = [
    (r"^во?-?перв", 1), (r"^во?-?втор", 2), (r"^в-?трет", 3), (r"^в-?четв", 4),
    (r"^в-?пят", 5), (r"^в-?шест", 6), (r"^в-?седьм", 7),
    (r"^first", 1), (r"^second", 2), (r"^third", 3), (r"^fourth", 4), (r"^fifth", 5),
]


def hex_ass(h, alpha="00"):
    """#RRGGBB -> &HAABBGGRR (ASS stores colour backwards)."""
    h = (h or "#000000").lstrip("#")
    if len(h) != 6:
        h = "000000"
    return f"&H{alpha}{h[4:6]}{h[2:4]}{h[0:2]}"


def alpha_tag(opacity):
    """0.0 (invisible) .. 1.0 (solid) -> ASS alpha override."""
    a = int(round((1.0 - max(0.0, min(1.0, float(opacity)))) * 255))
    return "\\1a&H%02X&" % a


def glued_words(src, root="."):
    p = words_path(src, root)
    if not Path(p).exists():
        return []
    out = []
    for w in load_json(p)["words"]:
        if out and len(w["w"]) > 1 and w["w"].startswith("-"):
            out[-1] = {"w": out[-1]["w"] + w["w"], "s": out[-1]["s"], "e": w["e"]}
        else:
            out.append(dict(w))
    return out


def src_to_out(timeline, src, st):
    """Source seconds -> output seconds, or None if that moment was cut."""
    for seg in timeline:
        if seg["src"] == src and seg["in"] - 1e-6 <= st < seg["out"]:
            return seg["t0"] + (st - seg["in"]) / seg["speed"]
    return None


def wrap(text, per_line=14):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > per_line:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines)


def ts(t):
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):d}:{int(m):02d}:{s:05.2f}"


def safe_y(src, root, strip_h, caption_top):
    """Pick a band the face does not occupy. Below it if the captions leave room,
    otherwise above it. Returns a fraction of frame height."""
    try:
        from . import reframe
        band = reframe.face_band(src, root)
    except Exception as e:                                   # noqa: BLE001
        log(f"лицо не измерено ({str(e)[:60]}) — ставлю плашку над кадром")
        return 0.24
    below = caption_top - band["bottom"]
    if below >= strip_h + 0.02:
        return round(band["bottom"] + 0.012, 4)
    above = band["top"] - strip_h - 0.02
    if above >= 0.02:
        return round(above, 4)
    log("свободного места нет ни над лицом, ни под ним — плашка пойдёт поверх")
    return round(max(0.02, band["top"] - strip_h), 4)


def auto_cards(edl, root="."):
    """Derive cards from the transcript: number every ordinal in a listicle."""
    src = edl["clips"][0]["src"]
    words = glued_words(src, root)
    if not words:
        return []
    hits = []
    for w in words:
        low = w["w"].lower().strip(".,!?:;»«\"'")
        for pat, num in ORDINALS:
            if re.match(pat, low):
                hits.append((num, w["s"], w["e"]))
                break
    if len(hits) < 2:
        log("auto-cards: перечисления не найдено — карточек нет")
        return []
    total = max(n for n, _, _ in hits)
    cards = []
    for i, (num, s, _e) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else s + 3.2
        cards.append({"type": "counter", "src": src, "at": round(s - 0.15, 3),
                      "dur": round(min(3.4, max(1.2, end - s - 0.15)), 3),
                      "text": f"{num}/{total}"})
    log(f"auto-cards: перечисление из {total} пунктов -> {len(cards)} счётчиков")
    return cards


def events(edl, timeline, W, H, root="."):
    """Return (style_lines, event_lines) to splice into the caption .ass."""
    cards = edl.get("cards") or []
    if cards == "auto" or edl.get("auto_cards"):
        cards = auto_cards(edl, root)
    if not cards:
        return [], []

    default_src = edl["clips"][0]["src"]
    styles = [f"Style: CARD,{FONT},{int(H / 16)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
              f"&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"]
    ev, skipped = [], 0
    caption_top = 1.0 - float((edl.get("captions") or {}).get("marginv", 0.21)) - 0.15

    for c in cards:
        src = c.get("src") or default_src
        t0 = src_to_out(timeline, src, float(c["at"]))
        if t0 is None:
            skipped += 1
            continue
        t1 = t0 + float(c.get("dur", 2.0))
        kind = c.get("type", "full")
        text = str(c.get("text", "")).strip()
        fg = hex_ass(c.get("fg", "#FFFFFF"))
        bg = hex_ass(c.get("bg", "#0B1220"))

        if kind == "full":
            # a scrim, not a slide: the speaker stays visible underneath
            op = float(c.get("opacity", 0.55))
            ev.append("Dialogue: 1,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an7\\pos(0,0)\\bord0\\shad0\\1c{}{}\\fad(120,120)\\p1".format(
                          bg, alpha_tag(op)) + "}"
                      + "m 0 0 l {} 0 {} {} 0 {}".format(W, W, H, H) + "{\\p0}")
            size = int(c.get("size") or H / 13)
            ev.append("Dialogue: 2,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an5\\pos({},{})\\fs{}\\1c{}\\bord0\\shad3\\fad(140,120)"
                              "\\t(0,180,\\fscx104\\fscy104)".format(
                                  W // 2, int(H * 0.42), size, fg) + "}"
                      + wrap(text, int(c.get("wrap", 14))))

        elif kind == "counter":
            bw, bh = int(W * 0.165), int(H * 0.058)
            mx, my = int(W * 0.055), int(H * 0.055)
            ev.append("Dialogue: 1,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an7\\pos({},{})\\bord0\\shad0\\1c{}\\1a&H30&"
                              "\\fad(120,120)\\p1".format(W - mx - bw, my, bg) + "}"
                      + "m 0 0 l {} 0 {} {} 0 {}".format(bw, bw, bh, bh) + "{\\p0}")
            ev.append("Dialogue: 2,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an5\\pos({},{})\\fs{}\\1c{}\\bord0\\shad0\\fad(120,120)".format(
                          W - mx - bw // 2, my + bh // 2, int(bh * 0.74),
                          hex_ass(c.get("fg", "#FFD400"))) + "}" + text)

        elif kind == "lower":
            sh = float(c.get("h", 0.075))
            bh = int(H * sh)
            yf = c.get("y")
            if yf is None:
                yf = safe_y(src, root, sh, caption_top)
            y = int(H * float(yf))
            ev.append("Dialogue: 1,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an7\\pos(0,{})\\bord0\\shad0\\1c{}\\1a&H28&"
                              "\\fad(120,120)\\p1".format(y, bg) + "}"
                      + "m 0 0 l {} 0 {} {} 0 {}".format(W, W, bh, bh) + "{\\p0}")
            ev.append("Dialogue: 2,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an5\\pos({},{})\\fs{}\\1c{}\\bord0\\shad0\\fad(140,120)".format(
                          W // 2, y + bh // 2, int(bh * 0.46), fg) + "}" + text)

        elif kind == "pop":
            size = int(c.get("size") or H / 20)
            yf = c.get("y")
            if yf is None:
                yf = safe_y(src, root, size / H * 1.6, caption_top)
            y = int(H * float(yf))
            ev.append("Dialogue: 2,{},{},CARD,,0,0,0,,".format(ts(t0), ts(t1))
                      + "{" + "\\an5\\pos({},{})\\fs{}\\1c{}\\bord{}\\3c{}\\shad0"
                              "\\fad(90,90)\\t(0,140,\\fscx112\\fscy112)"
                              "\\t(140,260,\\fscx100\\fscy100)".format(
                                  W // 2, y, size, fg, max(2, int(size * 0.09)), bg) + "}"
                      + text)
        else:
            log(f"неизвестный тип карточки: {kind!r} — пропускаю")

    if skipped:
        log(f"{skipped} карточк(и) попали в вырезанный кусок — пропущены")
    log(f"карточек отрисовано: {len(cards) - skipped}")
    return styles, ev
