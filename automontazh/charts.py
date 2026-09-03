"""Data graphics rendered as video clips.

Drawn with Pillow in the same visual language as the captions — same font, same
accent pill — so a chart reads as part of the film rather than an import from a
plotting library. Output is a plain MP4, which drops into an EDL as a `v_src`
cutaway: the speaker keeps talking underneath.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .core import AUDIO_ENC, FFMPEG, OUT, die, encode_args, log

FONTS = Path(__file__).parent / "fonts"
FONTMAP = {"onest": "Onest-ExtraBold.ttf",
           "golos": "GolosText-ExtraBold.ttf",
           "montserrat": "Montserrat-ExtraBold.ttf"}

BG = (11, 18, 32)          # #0B1220 — the chart surface
FG = (255, 255, 255)

# Categorical slots for the dark surface, in fixed order. Validated with the
# dataviz skill's checker against #0B1220: lightness band, chroma floor, CVD
# separation, normal-vision floor and >=3:1 contrast all pass.
#
# The caption pill's #4B41A5 is NOT usable here: L 0.444 falls outside the dark
# band and it sits at 2.32:1 against this surface. It stays a text container,
# where white type on top carries the contrast; a data mark has to stand against
# the surface on its own.
# The ORDER is the safety mechanism, not decoration: re-ordering these hues
# breaks the adjacent-pair gates (putting violet next to blue drops CVD ΔE to
# 1.9). Leave the sequence alone.
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500",
               "#d55181", "#008300", "#9085e9", "#e66767"]
SERIES = SERIES_DARK
# A lone series has no adjacent pair to clear, so the brand violet is free to
# carry single-series charts; it passes band, chroma and contrast on its own.
SOLO = "#9085e9"
SCATTER_CAP = 3            # past three slots the all-pairs floors fail


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


ACCENT = rgb(SOLO)


def ease_out(t):
    """Cubic ease-out: fast start, soft landing. Linear motion reads as robotic."""
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    """Soft at both ends — how an element should arrive, not slam into place."""
    t = max(0.0, min(1.0, t))
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def lerp(a, b, t):
    """Blend two colours. Fading toward the surface reads as a fade-in."""
    t = max(0.0, min(1.0, t))
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def _font(name, size):
    from PIL import ImageFont
    size = max(8, int(round(size * type_scale())))
    return ImageFont.truetype(str(FONTS / FONTMAP.get(name, FONTMAP["onest"])), size)


def _pipe(frames, W, H, fps, out):
    """Feed rendered frames straight into ffmpeg; no PNG sequence on disk.

    The silent track must be as long as the picture: generating a token 0.1 s of
    silence and adding -shortest truncates the whole clip to a tenth of a second.
    """
    dur = len(frames) / float(fps)
    p = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-nostdin", "-y",
         "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{W}x{H}",
         "-framerate", str(fps), "-i", "pipe:0",
         "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
         "-map", "0:v:0", "-map", "1:a:0",
         *encode_args("quality", 18), *AUDIO_ENC, "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for f in frames:
            p.stdin.write(f.tobytes())
        p.stdin.close()
    except BrokenPipeError:
        pass
    err = p.stderr.read().decode(errors="replace")
    if p.wait() != 0:
        die("ffmpeg refused the rendered frames:\n" +
            "\n".join(err.strip().splitlines()[-12:]))


# Pillow draws without antialiasing, so circles and rounded corners come out with
# stepped edges. Rendering at a multiple and downscaling with Lanczos is the cheap
# fix and the single biggest difference between "jagged" and "smooth".
SS = 2

# Light-mode steps of the same eight hues, in the same load-bearing order.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
                "#e87ba4", "#008300", "#4a3aa7", "#e34948"]

# A theme is surface + ink + treatment. The palette is never invented per theme:
# it is the documented order, stepped for that surface. Four of the light slots
# sit under 3:1 contrast, which is legal only because every chart here carries
# direct labels -- do not strip them from a light theme.
THEMES = {
    "dash": dict(on_accent="#FFFFFF", bg="#0B1220", ink="#FFFFFF", muted="#BEC6D6", track="#1C2436",
                 solo="#9085e9", series="dark", card=None, glow=False),
    "night": dict(on_accent="#FFFFFF", bg=("#171338", "#05060F"), ink="#FFFFFF", muted="#A9B0C7",
                  track="#221E42", solo="#9085e9", series="dark",
                  card=None, glow=True),
    "glass": dict(on_accent="#FFFFFF", bg=("#1B1640", "#070912"), ink="#FFFFFF", muted="#B4BACF",
                  track="#2A2550", solo="#9085e9", series="dark",
                  card=dict(fill="#FFFFFF", alpha=16, radius=0.055, inset=0.055),
                  glow=True),
    "paper": dict(on_accent="#FFFFFF", bg="#F4F1EA", ink="#14151A", muted="#5A5C66", track="#DFDACE",
                  solo="#4a3aa7", series="light", card=None, glow=False),
    "bold": dict(on_accent="#FFFFFF", bg="#08080C", ink="#FFFFFF", muted="#8E93A6", track="#17181F",
                 solo="#9085e9", series="dark", card=None, glow=False,
                 type_scale=1.18),
}

_T = dict(THEMES["dash"])


def apply_theme(name):
    """Swap the whole visual system. Palette order is never touched."""
    global BG, FG, MUTED, TRACK, ACCENT, SERIES, SOLO, _T
    if name not in THEMES:
        die(f"тема {name!r} неизвестна; есть: {', '.join(THEMES)}")
    _T = dict(THEMES[name])
    BG = rgb(_T["bg"][1] if isinstance(_T["bg"], tuple) else _T["bg"])
    FG = rgb(_T["ink"])
    MUTED = rgb(_T["muted"])
    TRACK = rgb(_T["track"])
    SOLO = _T["solo"]
    ACCENT = rgb(SOLO)
    SERIES = SERIES_LIGHT if _T["series"] == "light" else SERIES_DARK


def type_scale():
    return float(_T.get("type_scale", 1.0))


def on_accent():
    return rgb(_T.get("on_accent", "#FFFFFF"))


def _canvas(W, H):
    """Background per theme: flat, vertical gradient, and an optional glass card."""
    from PIL import Image, ImageDraw, ImageFilter
    bg = _T["bg"]
    if isinstance(bg, tuple):
        import numpy as np
        top, bot = np.array(rgb(bg[0]), float), np.array(rgb(bg[1]), float)
        t = np.linspace(0, 1, H)[:, None, None] ** 0.85
        img = Image.fromarray((top + (bot - top) * t).repeat(W, 1).astype("uint8"), "RGB")
    else:
        img = Image.new("RGB", (W, H), rgb(bg))

    if _T.get("glow"):
        layer = Image.new("RGB", (W, H), (0, 0, 0))
        gd = ImageDraw.Draw(layer)
        r = min(W, H) * 0.42
        gd.ellipse([W / 2 - r, H * 0.40 - r, W / 2 + r, H * 0.40 + r], fill=rgb(SOLO))
        layer = layer.filter(ImageFilter.GaussianBlur(min(W, H) * 0.13))
        img = Image.blend(img, Image.blend(img, layer, 0.28), 1.0)

    card = _T.get("card")
    if card:
        ins = card["inset"]
        box = [W * ins, H * 0.16, W * (1 - ins), H * 0.84]
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle(box, radius=int(min(W, H) * card["radius"]),
                             fill=(*rgb(card["fill"]), card["alpha"]),
                             outline=(*rgb(card["fill"]), card["alpha"] + 22),
                             width=max(2, int(W / 500)))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img


def _downscale(frames, W, H):
    from PIL import Image
    if frames and frames[0].size == (W, H):
        return frames
    return [f.resize((W, H), Image.LANCZOS) for f in frames]


def _fit(draw, text, font_name, max_w, start_size):
    """Shrink until it fits: a chart that runs off frame is worse than a small one."""
    size = start_size
    while size > 12:
        f = _font(font_name, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size = int(size * 0.94)
    return _font(font_name, 12)


def render_counter(args, W, H, fps):
    from PIL import ImageDraw

    n = max(1, int(args.dur * fps))
    frames = []
    label = (args.label or "").upper()
    suffix = args.suffix or ""
    for i in range(n):
        t = ease_out(min(1.0, (i / max(1, n - 1)) / 0.75))   # land before the end
        val = args.frm + (args.to - args.frm) * t
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        txt = f"{val:,.0f}".replace(",", " ") + suffix
        f = _fit(d, txt, args.font, W * 0.84, int(min(W, H) / 2.6))
        tw = d.textlength(txt, font=f)
        asc, desc = f.getmetrics()
        y = H / 2 - (asc + desc) / 2 - (H * 0.04 if label else 0)
        d.text((W / 2 - tw / 2, y), txt, font=f, fill=FG)
        if label:
            lf = _fit(d, label, args.font, W * 0.84, int(min(W, H) / 14))
            lw = d.textlength(label, font=lf)
            ly = y + asc + desc + H * 0.03
            pad = lf.size * 0.34
            d.rounded_rectangle([W / 2 - lw / 2 - pad, ly - lf.size * 0.18,
                                 W / 2 + lw / 2 + pad, ly + lf.size * 1.18],
                                radius=int(lf.size * 0.42), fill=ACCENT)
            d.text((W / 2 - lw / 2, ly), label, font=lf, fill=on_accent())
        frames.append(img)
    return frames


def render_bars(args, W, H, fps):
    from PIL import ImageDraw

    pairs = []
    for chunk in args.data.split(","):
        if "=" not in chunk:
            die(f"каждый столбец задаётся как Название=Число, получено {chunk!r}")
        k, v = chunk.rsplit("=", 1)
        pairs.append((k.strip().upper(), float(v)))
    if not pairs:
        die("нет данных")
    top = max(v for _, v in pairs) or 1.0

    n = max(1, int(args.dur * fps))
    lf = _font(args.font, int(min(W, H) / 20))
    vf = _font(args.font, int(min(W, H) / 17))
    left, right = W * 0.09, W * 0.91
    bh = min(H * 0.10, (H * 0.62) / len(pairs))
    gap = bh * 0.55
    total = len(pairs) * bh + (len(pairs) - 1) * gap
    top_y = H / 2 - total / 2 + (H * 0.05 if args.title else 0)

    frames = []
    for i in range(n):
        prog = ease_out(min(1.0, (i / max(1, n - 1)) / 0.8))
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        if args.title:
            tf = _fit(d, args.title.upper(), args.font, W * 0.84, int(min(W, H) / 13))
            tw = d.textlength(args.title.upper(), font=tf)
            d.text((W / 2 - tw / 2, top_y - H * 0.13), args.title.upper(), font=tf, fill=FG)
        for j, (name, val) in enumerate(pairs):
            y = top_y + j * (bh + gap)
            d.text((left, y - lf.size * 1.25), name, font=lf, fill=MUTED)
            d.rounded_rectangle([left, y, right, y + bh],
                                radius=int(bh / 2), fill=TRACK)
            w = (right - left) * (val / top) * prog
            if w > bh * 0.6:
                colour = ACCENT if j == (args.highlight if args.highlight is not None
                                         else len(pairs) - 1) else lerp(TRACK, ACCENT, 0.34)
                d.rounded_rectangle([left, y, left + w, y + bh],
                                    radius=int(bh / 2), fill=colour)
                shown = f"{val * prog:,.0f}".replace(",", " ")
                sw = d.textlength(shown, font=vf)
                if sw + bh * 0.6 < w:
                    d.text((left + w - sw - bh * 0.4, y + bh / 2 - vf.size * 0.62),
                           shown, font=vf, fill=on_accent())
        frames.append(img)
    return frames


def cmd_chart(args):
    W = args.width or 1080
    H = args.height or 1920
    fps = args.fps
    render = {"counter": render_counter, "bars": render_bars, "list": render_list,
              "line": render_line, "donut": render_donut, "timeline": render_timeline,
              "swarm": render_swarm}
    if args.kind not in render:
        die(f"неизвестный тип графика: {args.kind}")
    apply_theme(getattr(args, "theme", "dash") or "dash")
    ss = max(1, int(getattr(args, "ss", SS)))
    frames = _downscale(render[args.kind](args, W * ss, H * ss, fps), W, H)

    out = Path(args.out or f"edit/chart_{args.kind}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    _pipe(frames, W, H, fps, out)
    OUT.emit(path=str(out), kind=args.kind, frames=len(frames),
             duration=round(len(frames) / fps, 3), w=W, h=H)
    log(f"{args.kind}: {len(frames)} кадров, {len(frames)/fps:.1f}с -> {out}")
    OUT.say(out)


MUTED = (190, 198, 214)
TRACK = (28, 36, 54)


def _stagger(i, n, prog, overlap=0.55):
    """Per-item progress so things appear one after another, slightly overlapping."""
    if n <= 1:
        return ease_out(min(1.0, prog))
    step = (1.0 - overlap) / n
    start = i * step
    span = 1.0 - start
    return ease_out(max(0.0, min(1.0, (prog - start) / max(span * overlap + step, 1e-6))))


def render_list(args, W, H, fps):
    """Points arriving one by one — the shape of half the shorts ever made."""
    from PIL import ImageDraw

    items = [x.strip() for x in (args.data or "").split(";") if x.strip()]
    if not items:
        die("список задаётся как --data 'Первое;Второе;Третье'")
    n = max(1, int(args.dur * fps))
    tf = _font(args.font, int(min(W, H) / 15))
    nf = _font(args.font, int(min(W, H) / 22))
    line_h = min(H * 0.13, (H * 0.66) / len(items))
    total = line_h * len(items)
    top0 = H / 2 - total / 2 + (H * 0.06 if args.title else 0)
    bullet = line_h * 0.42

    # one size for every row, chosen by the longest: per-row shrinking makes the
    # list look ragged
    from PIL import Image
    probe_d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    longest = max(items, key=len).upper()
    item_f = _fit(probe_d, longest, args.font, W * 0.72, tf.size)

    frames = []
    for k in range(n):
        prog = min(1.0, (k / max(1, n - 1)) / 0.9)
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        if args.title:
            hf = _fit(d, args.title.upper(), args.font, W * 0.84, int(min(W, H) / 13))
            hw = d.textlength(args.title.upper(), font=hf)
            d.text((W / 2 - hw / 2, top0 - H * 0.15), args.title.upper(), font=hf, fill=FG)
        for i, item in enumerate(items):
            a = ease_in_out(_stagger(i, len(items), prog))
            if a <= 0.01:
                continue
            y = top0 + i * line_h + (1 - a) * line_h * 0.35     # slide up into place
            x = W * 0.11
            d.ellipse([x, y, x + bullet, y + bullet], fill=lerp(BG, ACCENT, a))
            num = str(i + 1)
            nw = d.textlength(num, font=nf)
            d.text((x + bullet / 2 - nw / 2, y + bullet / 2 - nf.size * 0.62),
                   num, font=nf, fill=on_accent())
            d.text((x + bullet + W * 0.045, y + bullet / 2 - item_f.size * 0.62),
                   item.upper(), font=item_f, fill=lerp(BG, FG, a))
        frames.append(img)
    return frames


def render_line(args, W, H, fps):
    """A growth line drawn left to right, with the head marked."""
    from PIL import ImageDraw

    try:
        vals = [float(x) for x in (args.data or "").split(",") if x.strip()]
    except ValueError:
        die("линия задаётся как --data '10,25,40,70'")
    if len(vals) < 2:
        die("для линии нужно хотя бы две точки")
    labels = [x.strip() for x in (args.labels or "").split(",")] if args.labels else []

    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    left, right = W * 0.12, W * 0.88
    bottom, top = H * 0.66, H * 0.34
    pts = [(left + (right - left) * i / (len(vals) - 1),
            bottom - (bottom - top) * (v - lo) / span) for i, v in enumerate(vals)]

    n = max(1, int(args.dur * fps))
    lf = _font(args.font, int(min(W, H) / 26))
    vf = _font(args.font, int(min(W, H) / 13))
    frames = []
    for k in range(n):
        prog = ease_out(min(1.0, (k / max(1, n - 1)) / 0.85))
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        if args.title:
            hf = _fit(d, args.title.upper(), args.font, W * 0.84, int(min(W, H) / 14))
            hw = d.textlength(args.title.upper(), font=hf)
            d.text((W / 2 - hw / 2, top - H * 0.12), args.title.upper(), font=hf, fill=FG)
        d.line([(left, bottom), (right, bottom)], fill=TRACK, width=max(2, int(H / 500)))

        # how far along the polyline we are
        seg = prog * (len(pts) - 1)
        idx = min(int(seg), len(pts) - 2)
        frac = seg - idx
        drawn = pts[:idx + 1] + [(pts[idx][0] + (pts[idx + 1][0] - pts[idx][0]) * frac,
                                  pts[idx][1] + (pts[idx + 1][1] - pts[idx][1]) * frac)]
        if len(drawn) > 1:
            d.line(drawn, fill=ACCENT, width=max(4, int(H / 190)), joint="curve")
        hx, hy = drawn[-1]
        r = max(6, H / 130)
        d.ellipse([hx - r, hy - r, hx + r, hy + r], fill=FG)

        cur = lo + (vals[idx] + (vals[idx + 1] - vals[idx]) * frac - lo)
        txt = f"{cur:,.0f}".replace(",", " ") + (args.suffix or "")
        tw = d.textlength(txt, font=vf)
        d.text((min(max(hx - tw / 2, W * 0.05), W * 0.95 - tw), hy - H * 0.075),
               txt, font=vf, fill=FG)
        for i, lab in enumerate(labels[:len(pts)]):
            lw = d.textlength(lab.upper(), font=lf)
            d.text((pts[i][0] - lw / 2, bottom + H * 0.02), lab.upper(), font=lf, fill=MUTED)
        frames.append(img)
    return frames


def render_donut(args, W, H, fps):
    """One share, stated once and clearly."""
    from PIL import ImageDraw

    n = max(1, int(args.dur * fps))
    R = min(W, H) * 0.20
    cx, cy = W / 2, H / 2 - (H * 0.03 if args.label else 0)
    width = int(R * 0.34)
    vf = _font(args.font, int(R * 0.62))
    frames = []
    for k in range(n):
        prog = ease_out(min(1.0, (k / max(1, n - 1)) / 0.8))
        val = args.frm + (args.to - args.frm) * prog
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        box = [cx - R, cy - R, cx + R, cy + R]
        d.arc(box, 0, 360, fill=TRACK, width=width)
        if val > 0.4:
            d.arc(box, -90, -90 + 360 * min(val, 100) / 100.0,
                  fill=ACCENT, width=width)
        txt = f"{val:,.0f}".replace(",", " ") + (args.suffix or "%")
        tw = d.textlength(txt, font=vf)
        d.text((cx - tw / 2, cy - vf.size * 0.62), txt, font=vf, fill=FG)
        if args.label:
            lab = args.label.upper()
            lf = _fit(d, lab, args.font, W * 0.8, int(min(W, H) / 18))
            lw = d.textlength(lab, font=lf)
            ly = cy + R + H * 0.045
            pad = lf.size * 0.34
            d.rounded_rectangle([W / 2 - lw / 2 - pad, ly - lf.size * 0.18,
                                 W / 2 + lw / 2 + pad, ly + lf.size * 1.18],
                                radius=int(lf.size * 0.42), fill=ACCENT)
            d.text((W / 2 - lw / 2, ly), lab, font=lf, fill=on_accent())
        frames.append(img)
    return frames


def render_timeline(args, W, H, fps):
    """Stages down the frame — reads better than a horizontal one in 9:16."""
    from PIL import ImageDraw

    steps = []
    for chunk in (args.data or "").split(";"):
        if not chunk.strip():
            continue
        k, _, v = chunk.partition("=")
        steps.append((k.strip().upper(), v.strip().upper()))
    if not steps:
        die("этапы задаются как --data '2023=Консалтинг;2024=Продукт'")

    n = max(1, int(args.dur * fps))
    kf = _font(args.font, int(min(W, H) / 24))
    tf = _font(args.font, int(min(W, H) / 17))
    step_h = min(H * 0.15, (H * 0.62) / len(steps))
    total = step_h * len(steps)
    top0 = H / 2 - total / 2 + (H * 0.05 if args.title else 0)
    rail = W * 0.16
    dot = step_h * 0.22

    from PIL import Image
    probe_d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    body_f = _fit(probe_d, max((t for _, t in steps), key=len), args.font,
                  W - (rail + dot + W * 0.05) - W * 0.06, tf.size)

    frames = []
    for f_i in range(n):
        prog = min(1.0, (f_i / max(1, n - 1)) / 0.9)
        img = _canvas(W, H)
        d = ImageDraw.Draw(img)
        if args.title:
            hf = _fit(d, args.title.upper(), args.font, W * 0.84, int(min(W, H) / 13))
            hw = d.textlength(args.title.upper(), font=hf)
            d.text((W / 2 - hw / 2, top0 - H * 0.13), args.title.upper(), font=hf, fill=FG)
        d.line([(rail, top0 + step_h * 0.5), (rail, top0 + total - step_h * 0.5)],
               fill=TRACK, width=max(3, int(W / 220)))
        for i, (key, text) in enumerate(steps):
            a = ease_in_out(_stagger(i, len(steps), prog))
            if a <= 0.01:
                continue
            y = top0 + i * step_h + step_h * 0.5
            if i:
                py = top0 + (i - 1) * step_h + step_h * 0.5
                d.line([(rail, py), (rail, py + (y - py) * a)],
                       fill=ACCENT, width=max(3, int(W / 220)))
            d.ellipse([rail - dot, y - dot, rail + dot, y + dot], fill=lerp(BG, ACCENT, a))
            x = rail + dot + W * 0.05
            d.text((x, y - kf.size * 1.15), key, font=kf, fill=lerp(BG, MUTED, a))
            d.text((x, y + body_f.size * 0.05), text, font=body_f, fill=lerp(BG, FG, a))
        frames.append(img)
    return frames


def render_swarm(args, W, H, fps):
    """A swarm that self-organises into the words.

    Three phases: the particles wander under plain boid rules, then they are drawn
    toward points sampled from the rendered text, then they settle. The point is
    the transition from noise to order -- that is what "swarm intelligence" looks
    like, and a bar chart cannot say it.
    """
    import numpy as np
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(args.seed)
    N = int(args.particles)
    n = max(1, int(args.dur * fps))
    text = (args.text or args.title or "").upper()

    # target formation: sample the filled pixels of the rendered text
    targets = None
    if text:
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        words, lines, cur = text.split(), [], []
        f = _font(args.font, int(min(W, H) / 7))
        for w in words:
            if cur and md.textlength(" ".join(cur + [w]), font=f) > W * 0.82:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        lh = f.size * 1.12
        y0 = H / 2 - lh * len(lines) / 2
        for i, ln in enumerate(lines):
            lw = md.textlength(ln, font=f)
            md.text((W / 2 - lw / 2, y0 + i * lh), ln, font=f, fill=255)
        filled = np.argwhere(np.asarray(mask) > 128)
        if len(filled):
            pick = rng.choice(len(filled), size=min(N, len(filled)), replace=len(filled) < N)
            targets = filled[pick][:, ::-1].astype(np.float64)   # (y,x) -> (x,y)
            N = len(targets)

    pos = rng.uniform([0, 0], [W, H], size=(N, 2))
    vel = rng.normal(0, W * 0.004, size=(N, 2))
    link_r = W * 0.085
    dot_r = max(1.8, W / 460)

    frames = []
    for k in range(n):
        t = k / max(1, n - 1)
        d2 = ((pos[:, None, :] - pos[None, :, :]) ** 2).sum(-1)

        if t < 0.42 or targets is None:
            near = (d2 < (W * 0.16) ** 2)
            np.fill_diagonal(near, False)
            cnt = near.sum(1, keepdims=True).clip(1)
            centre = (near[..., None] * pos[None, :, :]).sum(1) / cnt
            match = (near[..., None] * vel[None, :, :]).sum(1) / cnt
            too_close = (d2 < (W * 0.035) ** 2)
            np.fill_diagonal(too_close, False)
            push = (too_close[..., None] * (pos[:, None, :] - pos[None, :, :])).sum(1)
            vel += (centre - pos) * 0.004 + (match - vel) * 0.05 + push * 0.0016
            vel += (np.array([W / 2, H / 2]) - pos) * 0.0009      # keep them on screen
        else:
            pull = ease_out(min(1.0, (t - 0.42) / 0.45))
            vel += (targets - pos) * 0.055 * pull
            vel *= 1 - 0.28 * pull

        sp = np.linalg.norm(vel, axis=1, keepdims=True).clip(1e-6)
        vel = np.where(sp > W * 0.012, vel / sp * W * 0.012, vel)
        pos += vel

        img = _canvas(W, H)
        dr = ImageDraw.Draw(img)
        # links say "network" while the swarm is loose; once it spells something
        # they only smear the letters, so fade them out as the text forms
        link_a = 1.0 if t < 0.42 else max(0.0, 1.0 - (t - 0.42) / 0.22)
        if link_a > 0.02:
            ii, jj = np.where(np.triu(d2 < link_r ** 2, 1))
            if len(ii) > 1400:
                sel = rng.choice(len(ii), 1400, replace=False)
                ii, jj = ii[sel], jj[sel]
            for a, b in zip(ii, jj):
                f_ = (1 - (d2[a, b] ** 0.5) / link_r) * link_a
                c = int(40 + 90 * f_)
                dr.line([tuple(pos[a]), tuple(pos[b])],
                        fill=(int(c * 0.5), int(c * 0.5), min(255, c + 60)), width=1)
        r = dot_r * (1.0 + 0.55 * (0.0 if t < 0.42 else min(1.0, (t - 0.42) / 0.3)))
        col = lerp(BG, ACCENT, 0.85) if t < 0.42 else FG
        for x, y in pos:
            dr.ellipse([x - r, y - r, x + r, y + r], fill=col)
        frames.append(img)
    return frames
