"""Cover / preview still.

Picking the frame matters more than the typography: a thumbnail with a small,
soft or half-turned face loses to one where the speaker fills the frame and the
image is crisp. So candidates are scored on measured face size and sharpness
rather than taken from a fixed timestamp.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .core import FFMPEG, OUT, ff, die, hhmmss, log, probe, workdir, stem_of

FONTS = Path(__file__).parent / "fonts"
FONTMAP = {
    "montserrat": "Montserrat-ExtraBold.ttf",
    "onest": "Onest-ExtraBold.ttf",
    "golos": "GolosText-ExtraBold.ttf",
}

SAMPLE_FPS = 2.0
DETECT_W = 512


def _score_frames(src, start=None, end=None, fps=SAMPLE_FPS):
    import cv2
    import numpy as np
    from . import reframe

    info = probe(src)
    sw, sh = info["w"], info["h"]
    dw = min(DETECT_W, sw)
    dh = int(round(sh * dw / sw)) // 2 * 2
    det = reframe._detector(dw, dh, 0.6)

    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if end is not None and start is not None:
        args += ["-t", f"{max(0.2, end - start):.3f}"]
    args += ["-i", str(src), "-vf", f"fps={fps},scale={dw}:{dh}",
             "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(args, stdout=subprocess.PIPE)

    fsz = dw * dh * 3
    cands, i = [], 0
    while True:
        buf = p.stdout.read(fsz)
        if len(buf) < fsz:
            break
        fr = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
        t = (start or 0.0) + i / fps
        i += 1
        _, faces = det.detect(fr)
        if faces is None or not len(faces):
            continue
        f = max(faces, key=lambda f: f[2] * f[3])
        x, y, w, h = (float(v) for v in f[:4])
        area = (w * h) / (dw * dh)
        pad = 4
        crop = fr[max(0, int(y) - pad):int(y + h) + pad,
                  max(0, int(x) - pad):int(x + w) + pad]
        if crop.size == 0:
            continue
        sharp = float(cv2.Laplacian(crop, cv2.CV_64F).var())
        cands.append({"t": round(t, 3), "face_area": round(area, 4),
                      "sharpness": round(sharp, 1)})
    p.stdout.close()
    p.wait()
    if not cands:
        return []

    amax = max(c["face_area"] for c in cands) or 1.0
    smax = max(c["sharpness"] for c in cands) or 1.0
    for c in cands:
        c["score"] = round(0.6 * c["face_area"] / amax + 0.4 * c["sharpness"] / smax, 4)
    cands.sort(key=lambda c: -c["score"])
    return cands


def _draw(img, text, accent, W, H, band, fontname="onest", scale=None):
    from PIL import Image, ImageDraw, ImageFont

    size = int(min(W, H) / float(scale or 13.0))
    font = ImageFont.truetype(str(FONTS / FONTMAP.get(fontname, FONTMAP["onest"])), size)
    d = ImageDraw.Draw(img, "RGBA")

    words = text.upper().split()
    max_w = W * 0.86
    lines, cur = [], []
    for w in words:
        trial = " ".join(cur + [w])
        if cur and font.getlength(trial) > max_w:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)

    lh = size * 1.16
    block = lh * len(lines)
    # sit in whichever band the face leaves free
    if band and band.get("found"):
        below = (1.0 - 0.04) - band["bottom"]
        if below * H >= block + 0.02 * H:
            top = band["bottom"] * H + 0.02 * H
        else:
            top = max(0.03 * H, band["top"] * H - block - 0.03 * H)
    else:
        top = H - block - 0.10 * H

    acc = (accent or "").upper()
    pad, radius = size * 0.28, size * 0.42
    sp = font.getlength(" ")
    for li, line in enumerate(lines):
        lw = sum(font.getlength(w) for w in line) + sp * (len(line) - 1)
        x = W / 2 - lw / 2
        y = top + lh * li
        for w in line:
            ww = font.getlength(w)
            if acc and w.strip(".,!?:;") == acc:
                d.rounded_rectangle([x - pad, y - size * 0.16,
                                     x + ww + pad, y + size * 1.16],
                                    radius=int(radius), fill=(75, 65, 165, 255))
            else:
                d.text((x + 3, y + 4), w, font=font, fill=(0, 0, 0, 130))
            d.text((x, y), w, font=font, fill=(255, 255, 255, 255))
            x += ww + sp
    return img


def cmd_cover(args):
    from PIL import Image
    from . import reframe

    src = str(Path(args.source).resolve())
    if not Path(src).exists():
        die(f"no such file: {src}")
    info = probe(src)
    W = args.width or (1080 if info["h"] >= info["w"] else 1920)
    H = args.height or (1920 if info["h"] >= info["w"] else 1080)

    if args.at is not None:
        t, cands = float(args.at), []
        log(f"кадр задан вручную: {hhmmss(t)}")
    else:
        cands = _score_frames(src, args.start, args.end)
        if not cands:
            die("лица не найдено — задай кадр вручную через --at")
        t = cands[0]["t"]
        log(f"лучший кадр {hhmmss(t)} из {len(cands)} с лицом "
            f"(лицо {cands[0]['face_area']:.1%} кадра, резкость {cands[0]['sharpness']:.0f})")

    wd = workdir(args.root)

    # A machine can rank face size and sharpness; it cannot judge an expression.
    # --shortlist hands the top candidates back so a person picks the face.
    if args.shortlist and cands:
        tmp = wd / "cache" / "shortlist"
        tmp.mkdir(parents=True, exist_ok=True)
        for old_png in tmp.glob("c_*.png"):
            old_png.unlink()
        picks = cands[:args.shortlist]
        for i, c in enumerate(picks):
            ff(["-ss", f"{c['t']:.3f}", "-i", src, "-frames:v", "1", "-vf",
                f"scale=360:-2:flags=lanczos,drawtext=fontfile='{FONTS}/Montserrat-SemiBold.ttf'"
                f":text='{i + 1}  {hhmmss(c['t']).replace(':', '.')}'"
                f":x=8:y=8:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.75:boxborderw=8",
                tmp / f"c_{i:03d}.png"])
        cols = min(len(picks), 4)
        rows = (len(picks) + cols - 1) // cols
        sheet = Path(args.out or "edit/cover.png").with_name("cover_shortlist.png")
        sheet.parent.mkdir(parents=True, exist_ok=True)
        ff(["-framerate", "1", "-i", tmp / "c_%03d.png", "-vf",
            f"tile={cols}x{rows}:margin=8:padding=8:color=0x0d1117", "-frames:v", "1", sheet])
        OUT.emit(shortlist=[{"t": c["t"], "face_area": c["face_area"],
                             "sharpness": c["sharpness"]} for c in picks],
                 shortlist_sheet=str(sheet))
        log(f"шорт-лист из {len(picks)} кадров -> {sheet}  "
            f"(выбери время и передай --at)")
        OUT.say(sheet)
        return

    raw = wd / "cache" / f"{stem_of(src)}.cover_raw.png"
    raw.parent.mkdir(parents=True, exist_ok=True)
    fit = ("scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,crop=%d:%d"
           % (W, H, W, H))
    chain = [fit]
    if not args.no_grade:
        from . import enhance
        prof = enhance.profile(src, args.root, strength=float(args.grade_strength))
        if prof["pre"]:
            chain.insert(0, prof["pre"])
        if prof["grade"]:
            chain.append(prof["grade"])
        log("кадр проходит тот же авто-грейд, что и ролик")
    ff(["-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-vf", ",".join(chain), raw])

    img = Image.open(raw).convert("RGB")
    if args.text:
        band = reframe.face_band(src, args.root) if not args.no_face_band else None
        img = _draw(img, args.text, args.accent, W, H, band,
                    args.font, args.text_scale)

    out = Path(args.out or f"edit/cover.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=95)
    OUT.emit(path=str(out), at=round(t, 3), w=W, h=H,
             candidates=len(cands), best=cands[0] if cands else None)
    log(f"обложка -> {out}")
    OUT.say(out)
