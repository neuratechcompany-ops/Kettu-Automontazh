"""EDL -> final.mp4. Parallel clip render, cached, two-pass loudness, burn-in."""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
from pathlib import Path

from .core import (OUT, AUDIO_ENC, FFMPEG, audio_chain, die, encode_args, ff, ff_stderr,
                   hhmmss, load_json, log, probe, quantize, save_json, video_chain,
                   words_path, workdir)
from . import captions
from . import enhance
from . import reframe
from . import voice

# --------------------------------------------------------------- validation ---

LEAD, TAIL = 0.08, 0.14   # must match asr.cmd_autocut defaults


def _snap_in(t, words, tol):
    """Move an in-point just *before* the nearest word start -- never onto it,
    or the first consonant gets clipped."""
    if not words:
        return t
    i = min(range(len(words)), key=lambda k: abs(words[k]["s"] - t))
    if abs(words[i]["s"] - t) > tol:
        return t
    lo = words[i - 1]["e"] + 0.02 if i else 0.0
    return round(max(lo, words[i]["s"] - LEAD), 3)


def _snap_out(t, words, tol):
    """Move an out-point just *after* the nearest word end, keeping the tail."""
    if not words:
        return t
    i = min(range(len(words)), key=lambda k: abs(words[k]["e"] - t))
    if abs(words[i]["e"] - t) > tol:
        return t
    hi = words[i + 1]["s"] - 0.02 if i + 1 < len(words) else 1e9
    return round(min(hi, words[i]["e"] + TAIL), 3)


def validate(edl, root=".", snap_tol=0.0, fix=True):
    problems, probes = [], {}
    cv = edl.setdefault("canvas", {"w": 1920, "h": 1080, "fps": 30})
    for k, d in (("w", 1920), ("h", 1080), ("fps", 30)):
        cv[k] = int(cv.get(k) or d)
    if cv["w"] % 2 or cv["h"] % 2:
        problems.append(f"canvas {cv['w']}x{cv['h']} must be even; h264 will refuse it")

    keep = []
    for i, c in enumerate(edl.get("clips", [])):
        src = c.get("src")
        if not src or not Path(src).exists():
            problems.append(f"clip[{i}]: missing source {src!r}")
            continue
        if src not in probes:
            probes[src] = probe(src)
        info = probes[src]
        vs = c.get("v_src")
        if vs:
            if not Path(vs).exists():
                problems.append(f"clip[{i}]: missing v_src {vs!r} — cutaway dropped")
                c.pop("v_src", None)
            else:
                if vs not in probes:
                    probes[vs] = probe(vs)
                need = float(c.get("v_in", 0.0)) + (c["out"] - c["in"])
                if need > probes[vs]["duration"] + 0.05:
                    problems.append(
                        f"clip[{i}]: cutaway {Path(vs).name} is too short "
                        f"({probes[vs]['duration']:.2f}s) for {need:.2f}s — it will freeze")
        c["in"], c["out"] = float(c["in"]), float(c["out"])
        if c["out"] <= c["in"]:
            problems.append(f"clip[{i}]: out <= in ({c['in']} -> {c['out']})")
            continue
        if c["out"] > info["duration"] + 0.05:
            problems.append(f"clip[{i}]: out {c['out']:.2f} past end {info['duration']:.2f} — trimmed")
            c["out"] = round(info["duration"], 3)
        if snap_tol > 0:
            wp = words_path(src, root)
            if Path(wp).exists():
                ws = load_json(wp)["words"]
                c["in"] = _snap_in(c["in"], ws, snap_tol)
                c["out"] = _snap_out(c["out"], ws, snap_tol)
                c["out"] = min(c["out"], info["duration"])
        if c["out"] - c["in"] < 0.08:
            problems.append(f"clip[{i}]: shorter than 80 ms after snapping — dropped")
            continue
        sp = float(c.get("speed", 1.0) or 1.0)
        if not 0.25 <= sp <= 4.0:
            problems.append(f"clip[{i}]: speed {sp} outside 0.25–4.0 — clamped")
            c["speed"] = min(4.0, max(0.25, sp))
        keep.append(c)
    if fix:
        edl["clips"] = keep
    if not keep:
        problems.append("no usable clips")
    return edl, problems


# ------------------------------------------------------------------ render ---

def _key(clip, edl):
    stats = []
    for f in (clip["src"], clip.get("v_src")):
        if f:
            st = os.stat(f)
            stats += [f, int(st.st_mtime), st.st_size]
    blob = json.dumps([clip, edl["canvas"], edl.get("grade"), edl.get("fit"),
                       bool(edl.get("reframe")),
                       bool(edl.get("interpolate")),
                       edl.get("audio", {}).get("voice"),
                       edl.get("audio", {}).get("voice_strength", 1.0),
                       edl.get("audio", {}).get("fade_ms", 30),
                       edl.get("audio", {}).get("denoise", False), stats],
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def render_clip(idx, clip, edl, wd):
    """One normalised A/V segment. Picture may come from a different file than
    the sound (`v_src`) -- that is a b-roll cutaway over the speaker's take."""
    a_info = probe(clip["src"])
    v_src = clip.get("v_src") or clip["src"]
    cutaway = v_src != clip["src"]
    v_info = probe(v_src) if cutaway else a_info
    v_in = float(clip.get("v_in", 0.0 if cutaway else clip["in"]))

    speed = float(clip.get("speed", 1.0) or 1.0)
    fps = float(edl["canvas"]["fps"])
    # Work in whole output frames: n frames of picture, exactly n/fps of sound.
    n = max(1, round((clip["out"] - clip["in"]) / speed * fps))
    out_dur = n / fps
    src_dur = min(out_dur * speed, max(0.04, a_info["duration"] - clip["in"]))
    out = wd / "cache" / f"clip_{idx:04d}_{_key(clip, edl)}.mp4"
    if out.exists():
        return idx, out, True

    # Face-tracked reframe: crop box follows the speaker instead of sitting
    # dead centre. Times are clip-local because setpts zeroes them just below.
    rf_box = None
    if edl.get("reframe") and not clip.get("no_reframe"):
        tr = reframe.build_track(v_src, edl["canvas"]["w"], edl["canvas"]["h"],
                                    edl.get("_root", "."))
        if not tr.get("identity"):
            cmds = wd / "cache" / f"clip_{idx:04d}_{_key(clip, edl)}.cmds"
            x0, y0 = reframe.sample(tr, v_in)
            reframe.write_cmds(tr, v_in, v_in + src_dur, cmds, fps, speed)
            rf_box = (cmds, tr["crop_w"], tr["crop_h"], round(x0), round(y0))

    grade = clip.get("grade") or edl.get("grade", "neutral")
    pre = None
    if grade == "auto":
        prof = enhance.profile(v_src, edl.get("_root", "."),
                                  strength=float(edl.get("enhance_strength", 1.0)))
        grade, pre = prof["grade"], prof["pre"]

    vf = video_chain(v_info, edl["canvas"], grade,
                     clip.get("fit") or edl.get("fit", "pad"), clip.get("vf"), rf_box, pre,
                     bool(edl.get("interpolate")))
    # STARTPTS normalisation is mandatory after a seek: without it the first
    # frame keeps a non-zero pts and -t silently drops a frame per clip.
    vf = (f"setpts=(PTS-STARTPTS)/{speed:.6f}," if abs(speed - 1.0) > 1e-3
          else "setpts=PTS-STARTPTS,") + vf
    mute = bool(clip.get("mute")) or not a_info.get("has_audio")
    voice_chain = None
    if (edl.get("audio") or {}).get("voice") == "auto" and not mute:
        voice_chain = voice.profile(clip["src"], edl.get("_root", "."),
                                 strength=float(edl["audio"].get("voice_strength", 1.0)))["chain"]
    af = "asetpts=PTS-STARTPTS," + audio_chain(
        src_dur, edl.get("audio", {}).get("fade_ms", 30), speed,
        edl.get("audio", {}).get("denoise", False),
        ",".join(x for x in (voice_chain, clip.get("af")) if x) or None)

    # -t belongs on the INPUT: as an output option it means "emit N seconds",
    # which under setpts speed-up pulls source from beyond the out point.
    args, vmap, amap = [], "0:v:0", None
    args += ["-ss", f"{v_in:.4f}", "-t", f"{src_dur + 1.0 / fps:.4f}", "-i", v_src]
    if mute:
        args += ["-f", "lavfi", "-t", f"{out_dur:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
        amap = "1:a:0"
    elif cutaway:
        args += ["-ss", f"{clip['in']:.4f}", "-t", f"{src_dur:.4f}", "-i", clip["src"]]
        amap = "1:a:0"
    else:
        amap = "0:a:0"

    ff([*args, "-map", vmap, "-map", amap, "-vf", vf, "-af", af,
        "-frames:v", str(n), "-t", f"{out_dur:.4f}",
        *encode_args("intermediate"), *AUDIO_ENC, "-video_track_timescale", "90000",
        "-movflags", "+faststart", out])
    return idx, out, False


def assemble(parts, edl, wd):
    """concat demuxer when there are no transitions; xfade graph when there are."""
    trans = [c.get("transition") or {} for c in edl["clips"]]
    use_x = any(float(t.get("dur", 0) or 0) > 0 for t in trans[1:])
    out = wd / "cache" / "_assembled.mp4"

    if not use_x:
        lst = wd / "cache" / "_concat.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        ff(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy",
            "-movflags", "+faststart", out])
        return out

    ins, fg, vlab, alab = [], [], "0:v", "0:a"
    total = probe(parts[0])["duration"]
    for p in parts:
        ins += ["-i", str(p)]
    for i in range(1, len(parts)):
        t = trans[i] or {}
        d = min(float(t.get("dur", 0.5) or 0.5), total - 0.05,
                probe(parts[i])["duration"] - 0.05)
        d = max(0.05, d)
        kind = t.get("type", "fade")
        off = total - d
        fg.append(f"[{vlab}][{i}:v]xfade=transition={kind}:duration={d:.3f}:"
                  f"offset={off:.3f}[vx{i}]")
        fg.append(f"[{alab}][{i}:a]acrossfade=d={d:.3f}:c1=tri:c2=tri[ax{i}]")
        vlab, alab = f"vx{i}", f"ax{i}"
        total = total + probe(parts[i])["duration"] - d
    ff([*ins, "-filter_complex", ";".join(fg), "-map", f"[{vlab}]", "-map", f"[{alab}]",
        *encode_args("intermediate"), *AUDIO_ENC, "-movflags", "+faststart", out])
    return out


def measure_loudness(path, target):
    """EBU R128 pass 1: measure, so pass 2 can normalise linearly (no pumping)."""
    err = ff_stderr(["-i", str(path), "-af",
                     f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json",
                     "-f", "null", "-"])
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
    if not m:
        log("loudness measurement failed — falling back to single-pass")
        return None
    return json.loads(m[-1])


def cmd_render(args):
    edl = load_json(args.edl)
    root = args.root
    edl["_root"] = root
    if args.reframe:
        edl["reframe"] = True
    if args.no_reframe:
        edl["reframe"] = False
    if getattr(args, "grade", None):
        edl["grade"] = args.grade
    if getattr(args, "cards", False):
        edl["auto_cards"] = True
    if getattr(args, "interpolate", False):
        edl["interpolate"] = True
    if getattr(args, "no_interpolate", False):
        edl["interpolate"] = False
    if getattr(args, "voice", False):
        edl.setdefault("audio", {})["voice"] = "auto"
    if getattr(args, "no_voice", False):
        edl.setdefault("audio", {})["voice"] = None
    wd = workdir(root)
    edl, problems = validate(edl, root, args.snap)
    for p in problems:
        log(f"fixup: {p}")
    if not edl["clips"]:
        die("nothing to render")

    save_json(wd / "edl.resolved.json", edl)
    n = len(edl["clips"])
    total_src = sum(c["out"] - c["in"] for c in edl["clips"])
    log(f"{n} clips, {hhmmss(total_src, ms=False)} of source -> {edl['output']}")
    if not edl.get("interpolate"):
        slow = {c["src"] for c in edl["clips"]
                if (probe(c["src"]).get("fps") or 99) < edl["canvas"]["fps"] - 4}
        if slow:
            log(f"исходник снят на {probe(list(slow)[0])['fps']} к/с при выводе "
                f"{edl['canvas']['fps']} — движение будет дёргаться. "
                f"`--interpolate` достроит кадры (примерно 4x времени рендера)")
    if args.dry_run:
        for i, c in enumerate(edl["clips"]):
            log(f"[{i:3d}] {hhmmss(c['in'])}–{hhmmss(c['out'])}  "
                f"{Path(c['src']).name}  grade={c.get('grade') or edl.get('grade')}")
        return

    if edl.get("grade") == "auto" or any(c.get("grade") == "auto" for c in edl["clips"]):
        for src in dict.fromkeys(c.get("v_src") or c["src"] for c in edl["clips"]):
            enhance.profile(src, root, strength=float(edl.get("enhance_strength", 1.0)))

    if edl.get("reframe"):
        for src in dict.fromkeys(c.get("v_src") or c["src"] for c in edl["clips"]):
            reframe.build_track(src, edl["canvas"]["w"], edl["canvas"]["h"], root)

    workers = max(1, min(args.jobs or (os.cpu_count() or 4) // 2, 10))
    parts, cached = [None] * n, 0
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(render_clip, i, c, edl, wd) for i, c in enumerate(edl["clips"])]
        for done, f in enumerate(cf.as_completed(futs), 1):
            i, p, hit = f.result()
            parts[i] = p
            cached += hit
            if done % 10 == 0 or done == n:
                log(f"clips {done}/{n}")
    log(f"clip render done ({cached} from cache, {workers} parallel jobs)")

    asm = assemble([str(p) for p in parts], edl, wd)

    # ---- final pass: captions burn-in + loudness normalisation + delivery encode
    aud = edl.get("audio", {})
    target = float(aud.get("target_lufs", -16))
    af = "anull"
    if aud.get("loudnorm", True):
        m = measure_loudness(asm, target)
        if m:
            af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:"
                  f"measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
                  f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:"
                  f"offset={m['target_offset']}:linear=true")
            log(f"loudness {m['input_i']} LUFS -> {target} LUFS (linear)")
        else:
            af = f"loudnorm=I={target}:TP=-1.5:LRA=11"

    vf = "null"
    caps = edl.get("captions") or {}
    if caps.get("enabled") and not args.no_captions:
        ass = captions.build_ass(edl, root, args.caption_style)
        esc = str(ass).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")
        fdir = str(Path(__file__).parent / "fonts")
        vf = (f"subtitles=filename='{esc}'"
              f":fontsdir='{fdir}'")

    out = Path(edl["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    log(f"final encode ({'fast/videotoolbox' if args.fast else 'libx264 slow'})")
    ff(["-i", asm, "-vf", vf, "-af", af,
        *encode_args("fast" if args.fast else "quality", args.crf),
        *AUDIO_ENC, "-movflags", "+faststart", "-shortest", out])

    info = probe(out)
    OUT.emit(output=str(out), duration=round(info["duration"], 3),
             w=info.get("w"), h=info.get("h"), fps=info.get("fps"),
             size_mb=info.get("size_mb"), clips=len(edl["clips"]))
    log(f"DONE {out}  {hhmmss(info['duration'], ms=False)}  "
        f"{info['w']}x{info['h']}@{info['fps']}  {info['size_mb']} MB")
    OUT.say(out)
