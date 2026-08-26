"""`ve shorts FILE` — the whole vertical-shorts recipe in one command.

This is the settings combination that was arrived at on real footage: measured
auto-grade, face-tracked reframe (a no-op when the source is already vertical),
word-level captions, loudness to the platform standard, then a self-check.
"""
from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from .core import die, hhmmss, load_json, log, probe, words_path
from . import analyze
from . import asr
from . import render


def cmd_shorts(args):
    src = str(Path(args.source).resolve())
    if not Path(src).exists():
        die(f"no such file: {src}")
    root = args.root
    info = probe(src)
    log(f"{info['name']} — {hhmmss(info['duration'], ms=False)}, "
        f"{info.get('w')}x{info.get('h')}")

    if not Path(words_path(src, root)).exists() or args.force:
        asr.cmd_transcribe(Namespace(source=src, model=args.model, lang=args.lang,
                                        force=args.force, root=root))
    else:
        log("transcript cached")

    edl_path = args.edl or "shorts.json"
    asr.cmd_autocut(Namespace(
        words=src, edl=edl_path, output=args.output,
        max_gap=args.max_gap, lead=0.08, tail=0.14, min_clip=0.35,
        keep_fillers=False, keep_retakes=False,
        width=args.width, height=args.height, fps=args.fps, lufs=args.lufs,
        grade="auto" if args.enhance else "neutral",
        fit="crop", captions=args.style, reframe=True, root=root))

    render.cmd_render(Namespace(
        edl=edl_path, fast=args.fast, crf=args.crf, jobs=0, snap=0.25,
        dry_run=False, no_captions=False, caption_style=None,
        reframe=False, no_reframe=False, grade=None, cards=args.cards,
        voice=args.voice, no_voice=not args.voice, root=root))

    analyze.cmd_verify(Namespace(output=args.output, edl=".ve/edl.resolved.json",
                                    target_lufs=args.lufs, root=root))
    log(f"ready: {args.output}   (правки — в {edl_path}, потом `ve render {edl_path}`)")
