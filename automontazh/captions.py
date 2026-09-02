"""Word-level caption burning: builds .ass straight from the ASR word list."""
from __future__ import annotations

import re
from pathlib import Path

from .core import load_json, log, words_path, workdir
from . import cards
from . import chip

FONTS = Path(__file__).parent / "fonts"

# ASS colours are &HBBGGRR (and &HAABBGGRR with alpha).
STYLES = {
    "hormozi": dict(font="Montserrat", font_file="Montserrat-ExtraBold.ttf", h_div=11.0, bold=-1, outline=6, shadow=2,
                    marginv=0.17, upper=True, max_words=3, max_dur=2.2,
                    primary="&H00FFFFFF", hi="&H0000D7FF", outline_c="&H00000000",
                    border=1, pop=True),
    "karaoke": dict(font="Montserrat", font_file="Montserrat-ExtraBold.ttf", h_div=13.0, bold=-1, outline=5, shadow=2,
                    marginv=0.15, upper=False, max_words=5, max_dur=3.0,
                    primary="&H00FFFFFF", hi="&H0000D7FF", outline_c="&H00000000",
                    border=1, pop=False, kf=True),
    "standard": dict(font="Montserrat", font_file="Montserrat-SemiBold.ttf", h_div=22.0, bold=-1, outline=3, shadow=1,
                     marginv=0.09, upper=False, max_words=8, max_dur=4.0,
                     primary="&H00FFFFFF", hi=None, outline_c="&H00000000",
                     border=1, pop=False),
    # Reference look: clean bold sans, white caps, emphasis as a filled pill.
    "chip": dict(font="Onest", font_file="Onest-ExtraBold.ttf",
                 h_div=13.5, bold=-1, outline=0, shadow=2, marginv=0.155,
                 upper=True, max_words=3, max_dur=2.4,
                 primary="&H00FFFFFF", hi=None, outline_c="&H00000000",
                 border=1, pop=False, pill="#4B41A5", pill_pad=0.30,
                 pill_h=1.34, pill_r=0.30),
    "minimal": dict(font="Montserrat", font_file="Montserrat-SemiBold.ttf", h_div=26.0, bold=0, outline=0, shadow=0,
                    marginv=0.07, upper=False, max_words=9, max_dur=4.5,
                    primary="&H00FFFFFF", hi=None, outline_c="&H80000000",
                    border=3, pop=False),
}

SENT_END = tuple(".!?…:;")


def ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):d}:{int(m):02d}:{s:05.2f}"


def esc(s):
    return s.replace("{", "(").replace("}", ")").replace("\n", " ").strip()


def build_timeline(edl):
    """Map every clip onto the output timeline, honouring per-clip speed."""
    t, out = 0.0, []
    for c in edl["clips"]:
        speed = float(c.get("speed", 1.0) or 1.0)
        dur = (float(c["out"]) - float(c["in"])) / speed
        out.append({"src": c["src"], "in": float(c["in"]), "out": float(c["out"]),
                    "speed": speed, "t0": t, "t1": t + dur,
                    "captions": c.get("captions", True)})
        t += dur
    return out, t


def timeline_words(edl, root="."):
    """Every ASR word that survived the cut, retimed to the output timeline."""
    cache, out = {}, []
    for seg in build_timeline(edl)[0]:
        if not seg["captions"]:
            continue
        src = seg["src"]
        if src not in cache:
            p = words_path(src, root)
            cache[src] = load_json(p)["words"] if Path(p).exists() else []
        for w in cache[src]:
            if w["e"] <= seg["in"] or w["s"] >= seg["out"]:
                continue
            s = max(w["s"], seg["in"])
            e = min(w["e"], seg["out"])
            out.append({"w": w["w"],
                        "s": seg["t0"] + (s - seg["in"]) / seg["speed"],
                        "e": seg["t0"] + (e - seg["in"]) / seg["speed"]})
    out.sort(key=lambda x: x["s"])
    # Whisper splits hyphenated words in two ("Во" + "-первых,"); glue them back
    # or the caption reads "ВО -ПЕРВЫХ".
    glued = []
    for w in out:
        if glued and len(w["w"]) > 1 and w["w"].startswith("-"):
            glued[-1] = {"w": glued[-1]["w"] + w["w"], "s": glued[-1]["s"], "e": w["e"]}
        else:
            glued.append(dict(w))
    out = glued
    # ASR occasionally emits zero-length or overlapping words; make them monotonic
    for a, b in zip(out, out[1:]):
        a["e"] = max(a["s"] + 0.05, min(a["e"], b["s"]))
    return out


def chunk(words, st):
    chunks, cur = [], []
    for w in words:
        if cur:
            gap = w["s"] - cur[-1]["e"]
            long = w["e"] - cur[0]["s"] > st["max_dur"]
            if gap > 0.55 or len(cur) >= st["max_words"] or long \
                    or cur[-1]["w"].endswith(SENT_END):
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(edl, root=".", style_name=None):
    cfg = edl.get("captions") or {}
    st = dict(STYLES.get(style_name or cfg.get("style") or "hormozi", STYLES["hormozi"]))
    for k in ("font", "font_file", "max_words", "marginv", "h_div", "pill"):
        if cfg.get(k) is not None:
            st[k] = cfg[k]
    W, H = edl["canvas"]["w"], edl["canvas"]["h"]
    # scale off the short side -- in 9:16 the height is the long one
    size = int(cfg.get("size") or round(min(W, H) / st["h_div"]))
    mv = float(st["marginv"])
    if H > W:                      # vertical: lift captions above the shorts UI
        mv = max(mv, 0.20)
    marginv = int(round(H * mv))
    margin_h = int(round(W * 0.08))

    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        f"Style: VE,{st['font']},{size},{st['primary']},{st.get('hi') or st['primary']},"
        f"{st['outline_c']},&H90000000,{st['bold']},0,0,0,100,100,0,0,"
        f"{st['border']},{st['outline']},{st['shadow']},2,{margin_h},{margin_h},{marginv},1",
        "", "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    words = timeline_words(edl, root)
    if st.get("pill"):
        ev = chip.events(chunk(words, st), st, W, H, size, marginv)
        tl, _ = build_timeline(edl)
        card_styles, card_events = cards.events(edl, tl, W, H, root)
        if card_styles:
            i = head.index("[Events]")
            head[i - 1:i - 1] = card_styles
        out = workdir(root) / "captions.ass"
        out.write_text("\n".join(head + ev + card_events) + "\n")
        log(f"captions: {len(words)} words -> {len(ev)} events, style chip -> {out.name}")
        return out

    ev = []
    for ch in chunk(words, st):
        toks = [esc(w["w"]).upper() if st["upper"] else esc(w["w"]) for w in ch]
        c0, c1 = ch[0]["s"], max(ch[-1]["e"], ch[0]["s"] + 0.35)

        if st.get("kf"):
            body = "".join(f"{{\\kf{max(1, int((w['e'] - w['s']) * 100))}}}{t} "
                           for w, t in zip(ch, toks)).strip()
            ev.append(f"Dialogue: 4,{ts(c0)},{ts(c1)},VE,,0,0,0,,"
                      f"{{\\fad(50,50)}}{body}")
            continue

        if not st.get("hi"):
            ev.append(f"Dialogue: 4,{ts(c0)},{ts(c1)},VE,,0,0,0,,"
                      f"{{\\fad(80,80)}}{' '.join(toks)}")
            continue

        # one event per word so the spoken word lights up in sync
        for j, w in enumerate(ch):
            s = c0 if j == 0 else w["s"]
            e = c1 if j == len(ch) - 1 else ch[j + 1]["s"]
            if e - s < 0.02:
                continue
            parts = list(toks)
            parts[j] = (f"{{\\c{st['hi']}\\fscx108\\fscy108}}{parts[j]}"
                        f"{{\\c{st['primary']}\\fscx100\\fscy100}}")
            pre = "{\\fad(60,0)}" if (j == 0 and st.get("pop")) else ""
            ev.append(f"Dialogue: 4,{ts(s)},{ts(e)},VE,,0,0,0,,{pre}{' '.join(parts)}")

    tl, _ = build_timeline(edl)
    card_styles, card_events = cards.events(edl, tl, W, H, root)
    if card_styles:
        i = head.index("[Events]")
        head[i - 1:i - 1] = card_styles          # styles block ends just before [Events]

    ass = "\n".join(head + ev + card_events) + "\n"
    out = workdir(root) / "captions.ass"
    out.write_text(ass)
    log(f"captions: {len(words)} words -> {len(ev)} events, style "
        f"{style_name or cfg.get('style', 'hormozi')} -> {out.name}")
    return out
