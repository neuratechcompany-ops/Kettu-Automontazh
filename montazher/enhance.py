"""Measured auto-grade: look at the footage, then derive the correction from it.

Every number below comes from the actual pixels -- no fixed "make it pop" preset.
Ranges are clamped so a bad measurement degrades to a mild correction, never to a
destroyed image.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .core import OUT, FFMPEG, hhmmss, load_json, log, probe, save_json, stem_of, workdir

SAMPLE_W = 360


def measure(src, max_samples=120):
    import numpy as np

    info = probe(src)
    dur = max(info["duration"], 0.1)
    fps = min(2.0, max_samples / dur)
    w = SAMPLE_W
    h = int(round(info["h"] * w / info["w"])) // 2 * 2

    p = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-vf", f"fps={fps:.4f},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True)
    n = len(p.stdout) // (w * h * 3)
    if n < 2:
        raise RuntimeError(f"could not sample {src}")
    fr = np.frombuffer(p.stdout[:n * w * h * 3], np.uint8).reshape(n, h, w, 3).astype(np.float32)
    y = 0.114 * fr[..., 0] + 0.587 * fr[..., 1] + 0.299 * fr[..., 2]

    # white balance is read off the brightest *unclipped* band -- walls, paper,
    # anything that should be neutral. Mid-tones are dominated by skin, which is
    # legitimately warm, and measuring there over-corrects into a blue cast.
    ref = (y > 200) & (y < 245)
    if ref.mean() < 0.01:
        ref = (y > 120) & (y < 200)
    b, g, r = (float(fr[..., i][ref].mean()) for i in range(3))

    import cv2
    sat = float(cv2.cvtColor(fr[n // 2].astype("uint8"), cv2.COLOR_BGR2HSV)[..., 1].mean())
    step = max(1, n // 12)
    sharp = float(np.mean([cv2.Laplacian(fr[i].astype("uint8"), cv2.CV_64F).var()
                           for i in range(0, n, step)]))
    tnoise = float(np.abs(np.diff(y[:min(n, 24)], axis=0)).mean())

    return {
        "samples": n,
        "black_point": float(np.percentile(y, 0.1)),
        "p1": float(np.percentile(y, 1)),
        "median": float(np.percentile(y, 50)),
        "clipped_pct": float((y >= 250).mean() * 100),
        "crushed_pct": float((y <= 5).mean() * 100),
        "wb_r": r, "wb_g": g, "wb_b": b,
        "saturation": sat, "sharpness": sharp, "temporal_noise": tnoise,
    }


def build(m, strength=1.0):
    """Turn measurements into two filter chains: before scaling and after."""
    clamp = lambda v, lo, hi: max(lo, min(hi, v))
    pre, post, notes = [], [], []

    # --- denoise at native resolution, before any upscale ---------------------
    tn = m["temporal_noise"]
    if tn > 6.5:
        pre.append("hqdn3d=3:2:6:6")
        notes.append(f"шум {tn:.1f} — сильный шумодав")
    elif tn > 2.5:
        pre.append("hqdn3d=2:1.5:4:4")
        notes.append(f"шум {tn:.1f} — умеренный шумодав")

    # --- white balance, deliberately partial ---------------------------------
    gr = clamp((m["wb_g"] / m["wb_r"] - 1) * 0.6 * strength + 1, 0.93, 1.07)
    gb = clamp((m["wb_g"] / m["wb_b"] - 1) * 0.6 * strength + 1, 0.93, 1.07)
    if abs(gr - 1) > 0.005 or abs(gb - 1) > 0.005:
        post.append(f"colorchannelmixer=rr={gr:.3f}:gg=1.0:bb={gb:.3f}")
        notes.append(f"баланс белого R×{gr:.3f} B×{gb:.3f}")

    # --- tone curve: reclaim the black point, never push clipped highlights ---
    bp = clamp(m["black_point"] / 255.0 * 0.95 * strength, 0.0, 0.16)
    if m["clipped_pct"] > 15:
        hi_in, hi_out = 0.95, 0.955          # highlights already gone; leave them
        notes.append(f"выбито в белое {m['clipped_pct']:.0f}% — света не поднимаю")
    else:
        hi_in, hi_out = 0.92, 0.95
    mid_lo_in, mid_lo_out = 0.30, clamp(0.30 - 0.045 * strength, 0.20, 0.30)
    post.append(f"curves=all='0/0 {bp:.3f}/0.0 {mid_lo_in}/{mid_lo_out:.3f} "
                f"0.55/0.56 0.80/0.82 {hi_in}/{hi_out} 1/1'")
    if bp > 0.01:
        notes.append(f"чёрная точка {m['black_point']:.0f}/255 → 0 (контраст)")

    # --- saturation toward a natural target ----------------------------------
    sat_gain = clamp(30.0 / max(m["saturation"], 1.0), 1.0, 1.32)
    sat_gain = 1 + (sat_gain - 1) * strength
    if sat_gain > 1.02:
        post.append(f"eq=saturation={sat_gain:.2f}:contrast=1.02")
        notes.append(f"насыщенность {m['saturation']:.0f}/255 → ×{sat_gain:.2f}")

    # --- sharpen last, after the upscale -------------------------------------
    sh = m["sharpness"]
    amt = 0.0
    if sh < 30:
        amt = 1.0
    elif sh < 70:
        amt = 0.8
    elif sh < 140:
        amt = 0.5
    if amt:
        amt *= strength
        post.append(f"unsharp=5:5:{amt:.2f}:5:5:0.0")
        notes.append(f"резкость {sh:.0f} — контурная ×{amt:.2f}")

    return ",".join(pre), ",".join(post), notes


def profile(src, root=".", force=False, strength=1.0):
    p = workdir(root) / f"{stem_of(src)}.enhance.json"
    if p.exists() and not force:
        d = load_json(p)
        if abs(d.get("strength", 1.0) - strength) < 1e-6:
            return d
    m = measure(src)
    pre, post, notes = build(m, strength)
    d = {"source": str(src), "strength": strength, "measured": m,
         "pre": pre, "grade": post, "notes": notes}
    save_json(p, d)
    return d


def cmd_enhance(args):
    d = profile(args.source, args.root, args.force, args.strength)
    m = d["measured"]
    OUT.emit(measured=m, grade=d["grade"], pre=d["pre"], notes=d["notes"])
    log(f"{m['samples']} кадров измерено")
    OUT.say(f"чёрная точка   {m['black_point']:6.1f}/255   (0% ниже 5 = молочная картинка)"
          if m["crushed_pct"] < 0.01 else
          f"чёрная точка   {m['black_point']:6.1f}/255")
    OUT.say(f"выбито в белое {m['clipped_pct']:6.1f}%")
    OUT.say(f"насыщенность   {m['saturation']:6.1f}/255")
    OUT.say(f"резкость       {m['sharpness']:6.0f}")
    OUT.say(f"временной шум  {m['temporal_noise']:6.2f}")
    OUT.say("\nчто будет сделано:")
    for n in d["notes"]:
        OUT.say(f"  · {n}")
    OUT.say(f"\npre : {d['pre'] or '—'}")
    OUT.say(f"post: {d['grade']}")
