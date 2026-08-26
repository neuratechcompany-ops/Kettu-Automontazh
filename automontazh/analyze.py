"""Analysis + on-demand visual context + post-render self-check."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .core import (OUT, filter_path, die, ff, ff_stderr, hhmmss, load_json, log, probe, quantize,
                   save_json, stem_of, workdir)


LABEL_FONT = Path(__file__).parent / "fonts" / "Montserrat-SemiBold.ttf"


def _spans(err, tag_s, tag_e):
    starts = [float(x) for x in re.findall(tag_s, err)]
    ends = [float(x) for x in re.findall(tag_e, err)]
    return [{"s": round(a, 2), "e": round(b, 2), "d": round(b - a, 2)}
            for a, b in zip(starts, ends)]


def cmd_silence(args):
    err = ff_stderr(["-i", args.source, "-af",
                     f"silencedetect=noise={args.noise}dB:d={args.min_dur}",
                     "-f", "null", "-"])
    sp = _spans(err, r"silence_start: ([\d.\-]+)", r"silence_end: ([\d.\-]+)")
    dur = probe(args.source)["duration"]
    dead = sum(s["d"] for s in sp)
    log(f"{len(sp)} silences >= {args.min_dur}s, {dead:.1f}s dead air "
        f"({100 * dead / max(dur, .01):.0f}% of {hhmmss(dur, ms=False)})")
    for s in sp[:args.limit]:
        OUT.say(f"{hhmmss(s['s'])}  {hhmmss(s['e'])}  {s['d']:6.2f}s")
    if len(sp) > args.limit:
        OUT.say(f"... {len(sp) - args.limit} more")
    save_json(workdir(args.root) / f"{stem_of(args.source)}.silence.json",
              {"source": args.source, "spans": sp, "dead_total": round(dead, 2)})


def cmd_scenes(args):
    err = ff_stderr(["-i", args.source, "-vf", f"scdet=threshold={args.threshold}",
                     "-f", "null", "-"])
    cuts = sorted({round(float(t), 2) for t in
                   re.findall(r"lavfi\.scd\.time: ([\d.]+)", err)})
    log(f"{len(cuts)} scene changes at threshold {args.threshold}")
    for t in cuts[:args.limit]:
        OUT.say(hhmmss(t))
    save_json(workdir(args.root) / f"{stem_of(args.source)}.scenes.json",
              {"source": args.source, "cuts": cuts})


def cmd_frames(args):
    """Filmstrip + waveform contact sheet -- the agent's eyes, on demand only."""
    src = args.source
    info = probe(src)
    wd = workdir(args.root)
    tmp = wd / "cache" / "sheet"
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("f_*.png"):
        old.unlink()

    if args.at:
        times = [float(x) for x in str(args.at).split(",") if x.strip()]
    else:
        a = args.start or 0.0
        b = args.end if args.end is not None else info["duration"]
        n = max(2, args.n)
        step = (b - a) / n
        times = [a + step * (i + 0.5) for i in range(n)]

    for i, t in enumerate(times):
        label = hhmmss(t).replace(":", ".")
        ff(["-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-vf",
            f"scale={args.tile_w}:-2:flags=lanczos,"
            f"drawtext=fontfile='{filter_path(LABEL_FONT)}':text='{label}':"
            f"x=10:y=10:fontsize=26:"
            f"fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=8",
            tmp / f"f_{i:03d}.png"])

    cols = args.cols
    rows = (len(times) + cols - 1) // cols
    sheet = Path(args.out or (wd / f"{stem_of(src)}.sheet.png"))
    ff(["-framerate", "1", "-i", tmp / "f_%03d.png", "-vf",
        f"tile={cols}x{rows}:margin=8:padding=8:color=0x0d1117",
        "-frames:v", "1", sheet])

    if args.waveform and info.get("has_audio"):
        a = times[0] if args.at else (args.start or 0.0)
        b = times[-1] if args.at else (args.end if args.end is not None else info["duration"])
        wav = tmp / "wave.png"
        ff(["-ss", f"{a:.3f}", "-t", f"{max(0.5, b - a):.3f}", "-i", src,
            "-filter_complex", "showwavespic=s=1600x180:colors=0x7dd3fc|0x38bdf8:split_channels=0",
            "-frames:v", "1", wav])
        comb = sheet.with_name(sheet.stem + ".png")
        ff(["-i", sheet, "-i", wav, "-filter_complex",
            "[1:v]scale=iw*0:-1[x];[0:v][1:v]scale2ref=w=iw:h=ow/iw*ih[wv][sh];"
            "[sh][wv]vstack=inputs=2", "-frames:v", "1", wd / "_tmp_sheet.png"])
        (wd / "_tmp_sheet.png").replace(comb)
    log(f"{len(times)} frames -> {sheet}")
    OUT.say(sheet)


def cmd_verify(args):
    out = Path(args.output)
    if not out.exists():
        die(f"no output at {out}")
    info = probe(out)
    rep = {"output": str(out), **{k: info.get(k) for k in
           ("duration", "w", "h", "fps", "size_mb", "vcodec", "acodec", "sample_rate")}}
    fails, warns = [], []

    if args.edl and Path(args.edl).exists():
        edl = load_json(args.edl)
        fps = edl.get("canvas", {}).get("fps", 30)
        exp = sum(quantize((c["out"] - c["in"]) / float(c.get("speed", 1) or 1), fps)
                  for c in edl["clips"])
        exp -= sum(float((c.get("transition") or {}).get("dur", 0) or 0)
                   for c in edl["clips"][1:])
        rep["expected_duration"] = round(exp, 2)
        rep["drift"] = round(info["duration"] - exp, 2)
        if abs(rep["drift"]) > 0.4:
            fails.append(f"duration drift {rep['drift']:+.2f}s vs EDL — clips lost or doubled")

    # A source whose audio dies early is the classic way to lose picture silently.
    from .core import FFPROBE, run
    import json as _json
    st = _json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-print_format",
                          "json", str(out)]))["streams"]
    dv = next((float(s.get("duration") or 0) for s in st if s["codec_type"] == "video"), 0)
    da = next((float(s.get("duration") or 0) for s in st if s["codec_type"] == "audio"), 0)
    rep["v_duration"], rep["a_duration"] = round(dv, 3), round(da, 3)
    if dv and da and abs(dv - da) > 0.25:
        fails.append(f"A/V length mismatch: video {dv:.2f}s vs audio {da:.2f}s")

    err = ff_stderr(["-i", out, "-af", "ebur128=peak=true", "-f", "null", "-"])
    for key, pat in (("lufs", r"I:\s+(-?[\d.]+) LUFS"),
                     ("lra", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak", r"Peak:\s+(-?[\d.]+) dBFS")):
        m = re.findall(pat, err)
        if m:
            rep[key] = float(m[-1])
    if rep.get("lufs") is not None and abs(rep["lufs"] - args.target_lufs) > 1.5:
        warns.append(f"loudness {rep['lufs']} LUFS, target {args.target_lufs}")
    if rep.get("true_peak", -99) > -0.5:
        fails.append(f"true peak {rep['true_peak']} dBFS — clipping on playback")

    err = ff_stderr(["-i", out, "-vf", "blackdetect=d=0.5:pic_th=0.98", "-f", "null", "-"])
    black = [{"s": float(a), "e": float(b)} for a, b in
             re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", err)]
    rep["black_spans"] = black
    if black:
        warns.append(f"{len(black)} black span(s), e.g. {hhmmss(black[0]['s'])}")

    err = ff_stderr(["-i", out, "-af", "silencedetect=noise=-45dB:d=1.5", "-f", "null", "-"])
    sil = _spans(err, r"silence_start: ([\d.\-]+)", r"silence_end: ([\d.\-]+)")
    rep["long_silences"] = sil
    if sil:
        warns.append(f"{len(sil)} silence(s) >=1.5s left in, "
                     f"{sum(s['d'] for s in sil):.1f}s total")
    if info["w"] % 2 or info["h"] % 2:
        fails.append(f"odd dimensions {info['w']}x{info['h']}")
    if not info.get("has_audio"):
        fails.append("no audio track in output")

    rep["fails"], rep["warnings"] = fails, warns
    rep["verdict"] = "FAIL" if fails else ("WARN" if warns else "PASS")
    save_json(workdir(args.root) / "verify.json", rep)

    OUT.emit(**rep)
    OUT.say(f"verdict: {rep['verdict']}")
    OUT.say(f"  {hhmmss(info['duration'], ms=False)}  {info['w']}x{info['h']}@{info['fps']}  "
          f"{info['size_mb']} MB  {rep.get('lufs', '?')} LUFS  peak {rep.get('true_peak', '?')} dBFS")
    for f in fails:
        OUT.say(f"  FAIL  {f}")
    for w in warns:
        OUT.say(f"  warn  {w}")
    if not fails and not warns:
        OUT.say("  no issues found")
