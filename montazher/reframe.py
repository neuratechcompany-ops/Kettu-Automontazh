"""Face-tracked auto-reframe: 16:9 -> 9:16 that follows the speaker.

Fully local: OpenCV YuNet (232 KB ONNX, shipped in the skill). No network at
run time, no cloud, no per-minute cost.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .core import OUT, FFMPEG, die, hhmmss, load_json, log, probe, save_json, stem_of, workdir

MODEL = Path(__file__).parent / "models" / "yunet.onnx"
SAMPLE_FPS = 6.0        # detection rate; the track is interpolated back up later
DETECT_W = 512          # detector input width -- plenty for a face, cheap to decode


def _detector(w, h, thresh=0.6):
    import cv2
    if not MODEL.exists():
        die(f"face model missing: {MODEL}\n  re-run the skill's install step")
    return cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), thresh, 0.3, 5000)


def crop_box(sw, sh, tw, th):
    """Largest box of the target aspect that fits inside the source frame."""
    ar = tw / th
    if sw / sh > ar:
        ch, cw = sh, int(round(sh * ar))
    else:
        cw, ch = sw, int(round(sw / ar))
    return min(sw, cw - cw % 2), min(sh, ch - ch % 2)


def _median(xs, k=5):
    if len(xs) < k:
        return list(xs)
    h, out = k // 2, []
    for i in range(len(xs)):
        w = xs[max(0, i - h):i + h + 1]
        out.append(sorted(w)[len(w) // 2])
    return out


def _smooth(raw, dead, ease=0.14, avg=5):
    """Hysteresis + easing: hold still for small drift, glide for real moves.

    A camera operator does not micro-correct; neither should we. Jitter below
    the dead zone is ignored outright, and anything above it is eased into so
    the move reads as a slow pan rather than a snap.
    """
    if not raw:
        return raw
    vals = _median(raw)
    out, cur = [], vals[0]
    for v in vals:
        if abs(v - cur) > dead:
            cur += (v - cur) * ease
        out.append(cur)
    if avg > 1:                       # take the corners off the easing steps
        h, sm = avg // 2, []
        for i in range(len(out)):
            w = out[max(0, i - h):i + h + 1]
            sm.append(sum(w) / len(w))
        out = sm
    return out


def build_track(src, tw, th, root=".", force=False, thresh=0.6):
    info = probe(src)
    sw, sh = info["w"], info["h"]
    cw, ch = crop_box(sw, sh, tw, th)
    out = workdir(root) / f"{stem_of(src)}.reframe_{tw}x{th}.json"
    if out.exists() and not force:
        return load_json(out)

    if (cw, ch) == (sw, sh):
        log("source already matches the target aspect — no reframe needed")
        track = {"source": str(src), "src_w": sw, "src_h": sh, "crop_w": cw,
                 "crop_h": ch, "fps": SAMPLE_FPS, "identity": True, "points": []}
        save_json(out, track)
        return track

    import cv2
    import numpy as np

    dw = min(DETECT_W, sw)
    dh = int(round(sh * dw / sw)) // 2 * 2
    det = _detector(dw, dh, thresh)
    scale = sw / dw

    log(f"tracking faces in {info['name']} ({sw}x{sh}, {hhmmss(info['duration'], ms=False)})"
        f" at {SAMPLE_FPS} fps")
    proc = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-vf", f"fps={SAMPLE_FPS},scale={dw}:{dh}", "-f", "rawvideo",
         "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)

    fsz = dw * dh * 3
    raw_x, raw_y, hits, i = [], [], 0, 0
    last = (sw / 2, sh / 2)
    while True:
        buf = proc.stdout.read(fsz)
        if len(buf) < fsz:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
        _, faces = det.detect(frame)
        if faces is not None and len(faces):
            # prefer the face nearest to where we already are, weighted by size:
            # keeps us on the speaker instead of hopping to whoever is bigger
            def rank(f):
                fx = (f[0] + f[2] / 2) * scale
                area = f[2] * f[3] * scale * scale
                return area / (1 + ((fx - last[0]) / max(sw, 1)) ** 2 * 8)
            f = max(faces, key=rank)
            cx = (f[0] + f[2] / 2) * scale
            cy = (f[1] + f[3] / 2) * scale
            # frame the head slightly high -- eyeline near the upper third
            cy = cy + f[3] * scale * 0.35
            last = (float(cx), float(cy))
            hits += 1
        cx, cy = last
        raw_x.append(float(cx))
        raw_y.append(float(cy))
        i += 1
    proc.stdout.close()
    proc.wait()

    if not raw_x:
        die("could not read any frames for tracking")
    cover = hits / len(raw_x)
    log(f"{len(raw_x)} samples, face found in {cover:.0%}")
    if cover < 0.25:
        log("WARNING: face rarely detected — falling back to a static centre crop")
        raw_x = [sw / 2] * len(raw_x)
        raw_y = [sh / 2] * len(raw_x)

    sx = _smooth(raw_x, dead=cw * 0.045)
    sy = _smooth(raw_y, dead=ch * 0.045) if ch < sh else [sh / 2] * len(raw_x)

    pts = []
    for n, (x, y) in enumerate(zip(sx, sy)):
        px = min(max(0.0, x - cw / 2), sw - cw)
        py = min(max(0.0, y - ch / 2), sh - ch)
        pts.append({"t": round(n / SAMPLE_FPS, 3),
                    "x": round(float(px), 1), "y": round(float(py), 1)})

    track = {"source": str(src), "src_w": sw, "src_h": sh, "crop_w": cw, "crop_h": ch,
             "target": [tw, th], "fps": SAMPLE_FPS, "coverage": round(cover, 3),
             "identity": False, "points": pts}
    save_json(out, track)
    log(f"crop {cw}x{ch}, {len(pts)} keyframes -> {out.name}")
    return track


def sample(track, t):
    """Linear interpolation of the smoothed track at an arbitrary time."""
    pts = track["points"]
    if not pts:
        return 0.0, 0.0
    if t <= pts[0]["t"]:
        return pts[0]["x"], pts[0]["y"]
    if t >= pts[-1]["t"]:
        return pts[-1]["x"], pts[-1]["y"]
    i = min(int(t * track["fps"]), len(pts) - 2)
    while i + 1 < len(pts) - 1 and pts[i + 1]["t"] < t:
        i += 1
    a, b = pts[i], pts[i + 1]
    span = max(1e-6, b["t"] - a["t"])
    k = (t - a["t"]) / span
    return a["x"] + (b["x"] - a["x"]) * k, a["y"] + (b["y"] - a["y"]) * k


def write_cmds(track, t_in, t_out, dest, fps=30, speed=1.0):
    """sendcmd script driving the crop filter, in clip-local time."""
    lines, n = [], max(1, int((t_out - t_in) * fps))
    px = py = None
    for k in range(n + 1):
        st = t_in + k * (t_out - t_in) / n
        x, y = sample(track, st)
        x, y = round(x), round(y)
        if (x, y) == (px, py):
            continue
        lines.append(f"{(st - t_in) / speed:.3f} crop x {x}, crop y {y};")
        px, py = x, y
    Path(dest).write_text("\n".join(lines) + "\n")
    return dest, len(lines)


def cmd_reframe(args):
    tw, th = (int(x) for x in str(args.to).lower().split("x"))
    tr = build_track(args.source, tw, th, args.root, args.force, args.threshold)
    if tr.get("identity"):
        OUT.say("no reframe needed")
        return
    xs = [p["x"] for p in tr["points"]]
    OUT.emit(crop_w=tr["crop_w"], crop_h=tr["crop_h"],
             coverage=tr.get("coverage"), keyframes=len(tr["points"]))
    log(f"pan range {min(xs):.0f}–{max(xs):.0f} px of {tr['src_w'] - tr['crop_w']} available")
    OUT.say(workdir(args.root) / f"{stem_of(args.source)}.reframe_{tw}x{th}.json")


def face_band(src, root=".", force=False):
    """Vertical extent the face occupies, as fractions of frame height.

    Used to place graphics where the speaker's face is not. Cached, because it
    costs a detection pass.
    """
    import numpy as np

    out = workdir(root) / f"{stem_of(src)}.faceband.json"
    if out.exists() and not force:
        return load_json(out)

    info = probe(src)
    sw, sh = info["w"], info["h"]
    dw = min(DETECT_W, sw)
    dh = int(round(sh * dw / sw)) // 2 * 2
    det = _detector(dw, dh, 0.6)
    scale = sh / dh

    proc = subprocess.Popen(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(src),
         "-vf", f"fps=3,scale={dw}:{dh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE)
    fsz = dw * dh * 3
    tops, bots = [], []
    while True:
        buf = proc.stdout.read(fsz)
        if len(buf) < fsz:
            break
        _, faces = det.detect(np.frombuffer(buf, np.uint8).reshape(dh, dw, 3))
        if faces is not None and len(faces):
            f = max(faces, key=lambda f: f[2] * f[3])
            tops.append(float(f[1]) * scale / sh)
            bots.append(float(f[1] + f[3]) * scale / sh)
    proc.stdout.close()
    proc.wait()

    if len(tops) < 4:
        band = {"source": str(src), "found": False, "top": 0.18, "bottom": 0.72}
    else:
        band = {"source": str(src), "found": True,
                "top": round(float(np.percentile(tops, 5)), 4),
                "bottom": round(float(np.percentile(bots, 95)), 4),
                "samples": len(tops)}
    save_json(out, band)
    return band
