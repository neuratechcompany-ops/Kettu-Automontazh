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

BG = (11, 18, 32)
FG = (255, 255, 255)
ACCENT = (75, 65, 165)


def ease_out(t):
    """Cubic ease-out: fast start, soft landing. Linear motion reads as robotic."""
    return 1 - (1 - t) ** 3


def _font(name, size):
    from PIL import ImageFont
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


def _canvas(W, H):
    from PIL import Image
    return Image.new("RGB", (W, H), BG)


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
            d.text((W / 2 - lw / 2, ly), label, font=lf, fill=FG)
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
            d.text((left, y - lf.size * 1.25), name, font=lf, fill=(190, 198, 214))
            d.rounded_rectangle([left, y, right, y + bh],
                                radius=int(bh / 2), fill=(28, 36, 54))
            w = (right - left) * (val / top) * prog
            if w > bh * 0.6:
                colour = ACCENT if j == (args.highlight if args.highlight is not None
                                         else len(pairs) - 1) else (58, 74, 110)
                d.rounded_rectangle([left, y, left + w, y + bh],
                                    radius=int(bh / 2), fill=colour)
                shown = f"{val * prog:,.0f}".replace(",", " ")
                sw = d.textlength(shown, font=vf)
                if sw + bh * 0.6 < w:
                    d.text((left + w - sw - bh * 0.4, y + bh / 2 - vf.size * 0.62),
                           shown, font=vf, fill=FG)
        frames.append(img)
    return frames


def cmd_chart(args):
    W = args.width or 1080
    H = args.height or 1920
    fps = args.fps
    if args.kind == "counter":
        frames = render_counter(args, W, H, fps)
    elif args.kind == "bars":
        frames = render_bars(args, W, H, fps)
    else:
        die(f"неизвестный тип графика: {args.kind}")

    out = Path(args.out or f"edit/chart_{args.kind}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    _pipe(frames, W, H, fps, out)
    OUT.emit(path=str(out), kind=args.kind, frames=len(frames),
             duration=round(len(frames) / fps, 3), w=W, h=H)
    log(f"{args.kind}: {len(frames)} кадров, {len(frames)/fps:.1f}с -> {out}")
    OUT.say(out)
