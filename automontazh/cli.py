#!/usr/bin/env python3
"""ve — local, free, self-hosted video editing engine for coding agents."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core import OUT, FFMPEG, FFPROBE, die, hhmmss, log, probe, VIDEO_EXT, AUDIO_EXT  # noqa: E402
from . import analyze, asr, captions, cover, enhance, reframe, render  # noqa: E402
from . import cards, selftest, shorts, voice  # noqa: E402


def cmd_probe(args):
    paths = []
    for t in args.sources:
        p = Path(t)
        if p.is_dir():
            paths += sorted(q for q in p.iterdir()
                            if q.suffix.lower() in VIDEO_EXT | AUDIO_EXT)
        else:
            paths.append(p)
    if not paths:
        die("no media found")
    OUT.say(f"{'file':<38} {'dur':>9} {'res':>11} {'fps':>6} {'audio':>16}  {'MB':>7}")
    found, bad = [], []
    for p in paths:
        try:
            i = probe(p)
            found.append(i)
        except Exception as e:                                # noqa: BLE001
            bad.append({"path": str(p), "error": str(e)[:200]})
            OUT.say(f"{p.name:<38}  unreadable: {e}")
            continue
        res = f"{i.get('w','-')}x{i.get('h','-')}" if i["has_video"] else "audio"
        aud = (f"{i.get('acodec','-')} {i.get('channels','?')}ch "
               f"{i.get('sample_rate',0)//1000}k") if i["has_audio"] else "NONE"
        OUT.say(f"{i['name'][:38]:<38} {hhmmss(i['duration'], ms=False):>9} {res:>11} "
              f"{i.get('fps','-'):>6} {aud:>16}  {i['size_mb']:>7}")


    OUT.emit(media=found, unreadable=bad)
    if not found:
        die("no readable media among the given paths")


def cmd_captions(args):
    import json
    edl = json.loads(Path(args.edl).read_text())
    OUT.say(captions.build_ass(edl, args.root, args.style))


def cmd_cards(args):
    import json
    p = Path(args.edl)
    edl = json.loads(p.read_text())
    if args.auto:
        edl["cards"] = cards.auto_cards(edl, args.root)
        p.write_text(json.dumps(edl, ensure_ascii=False, indent=1))
        OUT.say(p)
    else:
        for c in edl.get("cards") or []:
            OUT.say(f"  {c.get('type'):8} at {c.get('at')}s {c.get('dur')}s  {c.get('text','')}")


def cmd_doctor(args):
    import shutil
    ok = True
    OUT.say(f"ffmpeg   {FFMPEG}")
    OUT.say(f"ffprobe  {FFPROBE}")
    from .core import run
    cfg = run([FFMPEG, "-version"]).splitlines()
    filters = run([FFMPEG, "-hide_banner", "-filters"])
    for f in ("subtitles", "ass", "drawtext", "loudnorm", "silencedetect", "scdet",
              "xfade", "acrossfade", "rubberband", "showwavespic", "ebur128",
              "blackdetect", "curves", "hqdn3d", "unsharp"):
        have = any(line.split()[1:2] == [f] for line in filters.splitlines()
                   if line.startswith(" "))
        OUT.say(f"  filter {f:<15} {'ok' if have else 'MISSING'}")
        ok &= have
    from . import backends
    got = backends.available()
    if got:
        OUT.say(f"  ASR backend      ok ({', '.join(backends.describe(b) for b in got)})")
    else:
        OUT.say("  ASR backend      MISSING — pip install mlx-whisper (Apple) "
              "or faster-whisper (anywhere)")
        ok = False
    try:
        import cv2
        m = reframe.MODEL
        sz = m.stat().st_size if m.exists() else 0
        good = sz > 100_000
        OUT.say(f"  opencv+YuNet     {'ok' if good else 'MISSING face model'} "
              f"(cv2 {cv2.__version__}, model {sz // 1024} KB)")
        ok &= good
    except Exception as e:
        OUT.say(f"  opencv+YuNet     MISSING ({e}) — auto-reframe unavailable")
        ok = False
    from .chip import FONTS
    ttf = sorted(FONTS.glob("*.ttf"))
    OUT.say(f"  bundled fonts    {'ok' if ttf else 'MISSING'} "
          f"({', '.join(f.stem for f in ttf) if ttf else 'fonts/ is empty'})")
    ok &= bool(ttf)

    OUT.say("\nverdict:", "READY" if ok else "INCOMPLETE")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(prog="ve", description=__doc__)
    ap.add_argument("--root", default=".", help="project dir holding .ve/ (default: cwd)")
    ap.add_argument("--json", action="store_true",
                    help="emit one machine-readable JSON object instead of prose")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("shorts", help="one command: source -> finished vertical short")
    p.add_argument("source")
    p.add_argument("--style", default="hormozi",
                   help="chip|hormozi|karaoke|standard|minimal")
    p.add_argument("--output", default="edit/shorts.mp4")
    p.add_argument("--edl", default=None)
    p.add_argument("--width", type=int, default=1080)
    p.add_argument("--height", type=int, default=1920)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--lufs", type=float, default=-16)
    p.add_argument("--max-gap", type=float, default=0.55)
    p.add_argument("--model", default="turbo")
    p.add_argument("--lang", default=None)
    p.add_argument("--cards", action="store_true", help="auto listicle cards")
    p.add_argument("--no-voice", dest="voice", action="store_false",
                   help="skip the measured voice EQ")
    p.add_argument("--no-enhance", dest="enhance", action="store_false",
                   help="skip the measured auto-grade")
    p.add_argument("--fast", action="store_true")
    p.add_argument("--crf", type=int, default=18)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=shorts.cmd_shorts, enhance=True, voice=True)

    p = sub.add_parser("probe", help="inventory sources")
    p.add_argument("sources", nargs="+")
    p.set_defaults(fn=cmd_probe)

    p = sub.add_parser("transcribe", help="local word-level ASR (MLX/Metal)")
    p.add_argument("source")
    p.add_argument("--model", default="turbo",
                   help="turbo|large|medium|small|tiny, or any mlx-community HF repo id")
    p.add_argument("--lang", default=None, help="ru/en/... (default: autodetect)")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=asr.cmd_transcribe)

    p = sub.add_parser("pull", help="download an ASR model into the local cache")
    p.add_argument("model", nargs="?", default="turbo")
    p.add_argument("--retries", type=int, default=8)
    p.set_defaults(fn=asr.cmd_pull)

    p = sub.add_parser("pack", help="words.json -> compact transcript for reading")
    p.add_argument("words")
    p.set_defaults(fn=asr.cmd_pack)

    p = sub.add_parser("tics", help="report repeated phrases / verbal tics")
    p.add_argument("words")
    p.add_argument("--min-count", type=int, default=2)
    p.set_defaults(fn=asr.cmd_tics)

    p = sub.add_parser("autocut", help="draft EDL: kill fillers, dead air, retakes")
    p.add_argument("words")
    p.add_argument("--edl", default="edl.json")
    p.add_argument("--output", default="edit/final.mp4")
    p.add_argument("--max-gap", type=float, default=0.55, help="dead air that survives (s)")
    p.add_argument("--lead", type=float, default=0.08)
    p.add_argument("--tail", type=float, default=0.14)
    p.add_argument("--min-clip", type=float, default=0.35)
    p.add_argument("--keep-fillers", action="store_true")
    p.add_argument("--keep-retakes", action="store_true")
    p.add_argument("--keep-phrases", action="store_true",
                   help="do not cut repeated verbal tics")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--lufs", type=float, default=-16)
    p.add_argument("--grade", default="neutral")
    p.add_argument("--fit", default="pad", choices=["pad", "crop"])
    p.add_argument("--reframe", action="store_true",
                   help="face-tracked crop instead of a static centre crop")
    p.add_argument("--captions", nargs="?", const="hormozi", default=None,
                   help="chip|hormozi|karaoke|standard|minimal")
    p.set_defaults(fn=asr.cmd_autocut)

    p = sub.add_parser("silence", help="dead-air map")
    p.add_argument("source")
    p.add_argument("--noise", default="-32")
    p.add_argument("--min-dur", type=float, default=0.5)
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=analyze.cmd_silence)

    p = sub.add_parser("scenes", help="scene-change map")
    p.add_argument("source")
    p.add_argument("--threshold", type=float, default=10.0)
    p.add_argument("--limit", type=int, default=60)
    p.set_defaults(fn=analyze.cmd_scenes)

    p = sub.add_parser("cover", help="pick the best frame and make a preview still")
    p.add_argument("source")
    p.add_argument("--text", help="headline drawn on the cover")
    p.add_argument("--accent", help="one word from --text to put in a filled pill")
    p.add_argument("--at", type=float, help="use this timestamp instead of searching")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--font", default="onest", choices=["onest", "golos", "montserrat"])
    p.add_argument("--text-scale", type=float, default=13.0,
                   help="short side / N = type size; bigger N = smaller type")
    p.add_argument("--no-grade", action="store_true", help="skip the auto grade")
    p.add_argument("--grade-strength", type=float, default=1.0)
    p.add_argument("--no-face-band", action="store_true")
    p.add_argument("--shortlist", type=int, metavar="N",
                   help="contact sheet of the N best frames instead of one cover")
    p.add_argument("--out")
    p.set_defaults(fn=cover.cmd_cover)

    p = sub.add_parser("frames", help="contact sheet (filmstrip + waveform)")
    p.add_argument("source")
    p.add_argument("--at", help="comma-separated seconds")
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("-n", type=int, default=9)
    p.add_argument("--cols", type=int, default=3)
    p.add_argument("--tile-w", type=int, default=480)
    p.add_argument("--out")
    p.add_argument("--waveform", action="store_true")
    p.set_defaults(fn=analyze.cmd_frames)

    p = sub.add_parser("voice", help="measure the speech and derive an EQ chain")
    p.add_argument("source")
    p.add_argument("--strength", type=float, default=1.0)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=voice.cmd_voice)

    p = sub.add_parser("enhance", help="measure the footage and derive an auto-grade")
    p.add_argument("source")
    p.add_argument("--strength", type=float, default=1.0, help="0.0–1.5")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=enhance.cmd_enhance)

    p = sub.add_parser("cards", help="graphic overlays / cutaway cards")
    p.add_argument("edl")
    p.add_argument("--auto", action="store_true",
                   help="detect a listicle in the transcript and number it")
    p.set_defaults(fn=cmd_cards)

    p = sub.add_parser("reframe", help="face-tracked crop track for 16:9 -> 9:16")
    p.add_argument("source")
    p.add_argument("--to", default="1080x1920", help="target frame, e.g. 1080x1920")
    p.add_argument("--threshold", type=float, default=0.6)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=reframe.cmd_reframe)

    p = sub.add_parser("captions", help="build .ass only")
    p.add_argument("edl")
    p.add_argument("--style", default=None)
    p.set_defaults(fn=cmd_captions)

    p = sub.add_parser("render", help="EDL -> final video")
    p.add_argument("edl")
    p.add_argument("--fast", action="store_true", help="VideoToolbox instead of x264 slow")
    p.add_argument("--crf", type=int, default=18)
    p.add_argument("--jobs", type=int, default=0)
    p.add_argument("--snap", type=float, default=0.25,
                   help="pull cuts onto word boundaries within N s (0 = off)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--caption-style", default=None)
    p.add_argument("--reframe", action="store_true", help="follow the speaker when reframing")
    p.add_argument("--no-reframe", action="store_true")
    p.add_argument("--grade", default=None, help="override the EDL grade (e.g. auto)")
    p.add_argument("--cards", action="store_true", help="auto listicle cards")
    p.add_argument("--voice", action="store_true", help="measured voice EQ chain")
    p.add_argument("--no-voice", action="store_true")
    p.set_defaults(fn=render.cmd_render)

    p = sub.add_parser("verify", help="self-check the render")
    p.add_argument("output")
    p.add_argument("--edl")
    p.add_argument("--target-lufs", type=float, default=-16)
    p.set_defaults(fn=analyze.cmd_verify)

    p = sub.add_parser("doctor", help="check the toolchain")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("selftest", help="drive the whole pipeline on a synthetic take")
    p.add_argument("--keep", action="store_true", help="keep the temp project dir")
    p.set_defaults(fn=selftest.cmd_selftest)

    args = ap.parse_args()
    OUT.json = bool(getattr(args, "json", False))
    try:
        code = args.fn(args) or 0
    except Exception as exc:                                  # noqa: BLE001
        if OUT.json:
            OUT.finish(args.cmd, ok=False, error=f"{type(exc).__name__}: {exc}")
            sys.exit(1)
        raise
    OUT.finish(args.cmd, ok=(code == 0))
    # mlx 0.32.x segfaults in CompileCache TLS destructors during normal
    # interpreter teardown (mlx_whisper loads mlx); output is flushed, skip it.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


