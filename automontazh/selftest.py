"""`ve selftest` — build a synthetic take and drive the whole pipeline through it."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .core import OUT, FFMPEG, ff, log, probe, save_json


BLOCKS = [
    (0.15, 3.24,  "Привет. Это тест системы автоматического монтажа."),
    (5.24, 5.56,  "Ну."),
    (6.66, 7.14,  "Эээ."),
    (8.60, 11.87, "Короче, сейчас мы проверим автоматическую нарезку."),
    (14.29, 16.84, "Сейчас мы проверим автоматическую нарезку."),
    (18.55, 18.89, "Вот."),
    (20.14, 23.66, "Движок должен вырезать паузы, паразиты и повторный дубль."),
    (26.39, 28.90, "Спасибо за внимание."),
]


def _fixture(d: Path):
    """A 29 s take with pauses, fillers and one blown take, plus its transcript.
    The audio deliberately ends before the video -- that is the case that used to
    let -shortest eat the tail of the film."""
    ff(["-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30",
        "-f", "lavfi", "-i", "sine=f=180:r=48000", "-t", "28.933",
        "-c:v", "libx264", "-crf", "24", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-af", "atrim=0:27.7,apad=pad_dur=0",
        d / "take.mp4"])
    src = str((d / "take.mp4").resolve())
    words, segs = [], []
    for s, e, text in BLOCKS:
        toks = text.split()
        step = (e - s) / len(toks)
        for i, t in enumerate(toks):
            words.append({"w": t, "s": round(s + i * step, 3),
                          "e": round(s + (i + 0.88) * step, 3), "p": 0.95})
        segs.append({"s": s, "e": e, "text": text})
    (d / ".ve").mkdir(exist_ok=True)
    save_json(d / ".ve" / "take.words.json",
              {"source": src, "duration": 28.933, "language": "ru",
               "model": "selftest/fixture", "words": words, "segments": segs})
    return src


def cmd_selftest(args):
    ve = [sys.executable, "-m", "automontazh"]
    keep = args.keep
    d = Path(tempfile.mkdtemp(prefix="ve_selftest_"))
    results = []

    def step(name, argv, expect_rc=0):
        p = subprocess.run([*ve, *argv], cwd=d, capture_output=True, text=True)
        ok = p.returncode == expect_rc
        results.append((name, ok, (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]))
        OUT.say(f"  {'ok  ' if ok else 'FAIL'} {name}")
        if not ok:
            OUT.say("       " + "\n       ".join((p.stderr or p.stdout).splitlines()[-6:]))
        return ok

    try:
        src = _fixture(d)
        OUT.say("selftest in", d)

        step("pack transcript", ["pack", "take.mp4"])
        step("autocut draft", ["autocut", "take.mp4", "--captions", "hormozi",
                               "--width", "1280", "--height", "720",
                               "--output", "edit/a.mp4", "--edl", "a.json"])
        edl = json.loads((d / "a.json").read_text())
        assert len(edl["clips"]) >= 3, "autocut produced too few clips"

        # a second EDL exercising speed, transitions, cutaway, mute and vertical
        b = {"version": 1, "output": "edit/b.mp4",
             "canvas": {"w": 1080, "h": 1920, "fps": 30},
             "audio": {"loudnorm": True, "target_lufs": -14, "fade_ms": 30},
             "grade": "cinematic", "fit": "crop",
             "captions": {"enabled": True, "style": "karaoke"},
             "cards": [
                 {"type": "full", "src": src, "at": 0.5, "dur": 0.8, "text": "КАРТОЧКА"},
                 {"type": "counter", "src": src, "at": 20.5, "dur": 1.0, "text": "1/3"},
                 {"type": "lower", "src": src, "at": 21.5, "dur": 1.0, "text": "ПЛАШКА"},
                 {"type": "pop", "src": src, "at": 22.5, "dur": 0.8, "text": "ВОТ"},
                 {"type": "full", "src": src, "at": 5.4, "dur": 0.5, "text": "ВЫРЕЗАНО"},
             ],
             "clips": [
                 {"src": src, "in": 0.07, "out": 3.318},
                 {"src": src, "in": 20.06, "out": 23.747, "speed": 1.25,
                  "transition": {"type": "fade", "dur": 0.4}},
                 {"src": src, "in": 26.31, "out": 28.933, "mute": True,
                  "captions": False},
             ]}
        (d / "b.json").write_text(json.dumps(b, ensure_ascii=False))

        step("render jump-cut", ["render", "a.json"])
        step("verify jump-cut", ["verify", "edit/a.mp4", "--edl", ".ve/edl.resolved.json"])
        step("render vertical+speed+transition", ["render", "b.json"])
        step("captions .ass", ["captions", "b.json", "--style", "hormozi"])
        step("chip captions (measured pill)", ["captions", "b.json", "--style", "chip"])
        step("contact sheet", ["frames", "edit/a.mp4", "-n", "4", "--cols", "2"])
        step("silence map", ["silence", "take.mp4"])
        step("cache hit (re-render)", ["render", "a.json"])
        step("shorts one-liner", ["shorts", "take.mp4", "--output", "edit/s.mp4",
                                  "--edl", "s.json", "--fast"])
        step("enhance profile", ["enhance", "take.mp4"])
        step("render with auto grade", ["render", "a.json", "--grade", "auto"])
        step("tics report", ["tics", "take.mp4"])
        step("voice profile", ["voice", "take.mp4"])
        step("render with voice", ["render", "a.json", "--voice"])
        step("chart counter", ["chart", "counter", "--to", "70", "--label", "машин",
                               "--dur", "1.0", "--out", "edit/ch.mp4"])
        step("chart bars", ["chart", "bars", "--data", "А=30,Б=70",
                            "--dur", "1.0", "--out", "edit/cb.mp4"])
        step("chart list", ["chart", "list", "--data", "Раз;Два;Три",
                            "--dur", "1.0", "--out", "edit/cl.mp4"])
        step("chart line", ["chart", "line", "--data", "4,9,18,70",
                            "--dur", "1.0", "--out", "edit/cn.mp4"])
        step("chart donut", ["chart", "donut", "--to", "70",
                             "--dur", "1.0", "--out", "edit/cd.mp4"])
        step("chart timeline", ["chart", "timeline", "--data", "2023=А;2024=Б",
                                "--dur", "1.0", "--out", "edit/ct.mp4"])
        step("chart swarm", ["chart", "swarm", "--text", "РОЙ", "--particles", "400",
                             "--dur", "1.0", "--out", "edit/cs.mp4"])
        import importlib.util
        if importlib.util.find_spec("matplotlib"):
            step("viz area", ["viz", "area", "--data", "А=1,2,3;Б=3,2,1",
                              "--dur", "0.6", "--out", "edit/va.mp4"])
            step("viz heat", ["viz", "heat", "--data", "А=1,2;Б=3,4",
                              "--dur", "0.6", "--out", "edit/vh.mp4"])
        else:
            print("  skip viz (matplotlib not installed)")
        step("reframe track", ["reframe", "take.mp4", "--to", "1080x1920"])
        step("render with reframe", ["render", "b.json", "--reframe"])

        ass = (d / ".ve" / "captions.ass").read_text()
        ok_esc = "{\\an" in ass and "{n" not in ass and "-7354" not in ass
        results.append(("ass override tags survive escaping", ok_esc, []))
        OUT.say(f"  {'ok  ' if ok_esc else 'FAIL'} ass override tags survive escaping")

        from . import asr as _asr
        mk = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4, "p": 1.0} for i, w in
              enumerate(["Но", "на", "самом", "деле", "это", "важно", "и",
                         "на", "самом", "деле", "точка"])]
        got = _asr.droppable(mk, "ru", keep_fillers=True, keep_retakes=True)
        ok_ph = got == {7, 8, 9}          # first use survives, the echo does not
        results.append(("repeated tic: first kept, echo cut", ok_ph, []))
        OUT.say(f"  {'ok  ' if ok_ph else 'FAIL'} repeated tic: first kept, echo cut "
              f"(dropped {sorted(got)})")

        from . import voice as _v
        bands = [{"lo": 60, "hi": 100, "target": -12.0, "measured": -20.0, "noise_dbfs": -80},
                 {"lo": 100, "hi": 160, "target": 0.0, "measured": -9.0, "noise_dbfs": -80},
                 {"lo": 2500, "hi": 4000, "target": -11.0, "measured": -27.0, "noise_dbfs": -70}]
        _c, _n, gains = _v.build({"snr": 45.0, "bands": bands}, 1.0)
        low = {(lo, hi): g for lo, hi, g in gains}
        ok_v = low[(60, 100)] <= 0 and low[(100, 160)] <= 0 and low[(2500, 4000)] > 5
        results.append(("rumble bands are never boosted", ok_v, []))
        OUT.say(f"  {'ok  ' if ok_v else 'FAIL'} rumble bands are never boosted "
              f"(60-100 {low[(60, 100)]:+.1f}, 2.5-4k {low[(2500, 4000)]:+.1f})")

        ok_nd = "afftdn" not in _c      # 45 dB SNR: nothing for a denoiser to do
        results.append(("clean audio gets no denoiser", ok_nd, []))
        OUT.say(f"  {'ok  ' if ok_nd else 'FAIL'} clean audio gets no denoiser")

        vc = probe(d / "edit" / "ch.mp4")
        ok_ch = abs(vc["duration"] - 1.0) < 0.15 and vc.get("has_audio")
        results.append(("chart clip is the length it claims", ok_ch, []))
        print(f"  {'ok  ' if ok_ch else 'FAIL'} chart clip is the length it claims "
              f"({vc['duration']:.2f}s)")

        from . import cards
        ok_hex = cards.hex_ass("#FFD400") == "&H0000D4FF"
        results.append(("ass colour conversion", ok_hex, []))
        OUT.say(f"  {'ok  ' if ok_hex else 'FAIL'} ass colour conversion")

        fake = {"clips": [{"src": "x", "in": 0, "out": 5}]}
        tl = [{"src": "x", "in": 0.0, "out": 5.0, "speed": 1.0, "t0": 0.0, "t1": 5.0},
              {"src": "x", "in": 10.0, "out": 12.0, "speed": 2.0, "t0": 5.0, "t1": 6.0}]
        ok_map = (abs(cards.src_to_out(tl, "x", 2.0) - 2.0) < 1e-6
                  and abs(cards.src_to_out(tl, "x", 11.0) - 5.5) < 1e-6
                  and cards.src_to_out(tl, "x", 7.0) is None)
        results.append(("card times map through cuts and speed", ok_map, []))
        OUT.say(f"  {'ok  ' if ok_map else 'FAIL'} card times map through cuts and speed")

        from . import enhance
        prof = json.loads((d / ".ve" / "take.enhance.json").read_text())
        ok_en = ("curves" in prof["grade"] and prof["measured"]["samples"] > 5
                 and "=" in prof["grade"])
        results.append(("auto grade derives a chain from pixels", ok_en, []))
        OUT.say(f"  {'ok  ' if ok_en else 'FAIL'} auto grade derives a chain from pixels")

        # a flat grey ramp must not be "corrected" into something wild
        flat = enhance.build({"temporal_noise": 0.1, "wb_r": 128, "wb_g": 128,
                                 "wb_b": 128, "black_point": 0.0, "clipped_pct": 0.0,
                                 "saturation": 60.0, "sharpness": 400.0}, 1.0)
        ok_noop = flat[0] == "" and "unsharp" not in flat[1] and "colorchannelmixer" not in flat[1]
        results.append(("clean footage gets left alone", ok_noop, []))
        OUT.say(f"  {'ok  ' if ok_noop else 'FAIL'} clean footage gets left alone")

        from . import reframe
        ok_box = (reframe.crop_box(1920, 1080, 1080, 1920) == (608, 1080)
                  and reframe.crop_box(1080, 1920, 1080, 1920) == (1080, 1920))
        results.append(("crop box maths", ok_box, []))
        OUT.say(f"  {'ok  ' if ok_box else 'FAIL'} crop box maths")

        # testsrc has no face, so the track must degrade to a dead-centre crop
        tr = json.loads((d / ".ve" / "take.reframe_1080x1920.json").read_text())
        xs = {pt["x"] for pt in tr["points"]}
        ok_fb = tr["crop_w"] == 404 and len(xs) == 1 and abs(xs.pop() - 438) < 1.0
        results.append(("no-face fallback centres the crop", ok_fb, []))
        OUT.say(f"  {'ok  ' if ok_fb else 'FAIL'} no-face fallback centres the crop")

        vs = probe(d / "edit" / "s.mp4")
        ok_s = (vs["w"], vs["h"]) == (1080, 1920) and vs.get("has_audio") and vs["duration"] > 5
        results.append(("shorts one-liner produces a vertical film", ok_s, []))
        OUT.say(f"  {'ok  ' if ok_s else 'FAIL'} shorts one-liner produces a vertical film")

        vb = probe(d / "edit" / "b.mp4")
        ok_rf = (vb["w"], vb["h"]) == (1080, 1920) and vb.get("has_audio")
        results.append(("reframed render is 1080x1920 with sound", ok_rf, []))
        OUT.say(f"  {'ok  ' if ok_rf else 'FAIL'} reframed render is 1080x1920 with sound")

        v = probe(d / "edit" / "a.mp4")
        rep = json.loads((d / ".ve" / "verify.json").read_text())
        ok_len = abs(rep.get("drift", 99)) <= 0.4
        results.append(("duration within 0.4s of EDL", ok_len, []))
        OUT.say(f"  {'ok  ' if ok_len else 'FAIL'} duration within 0.4s of EDL "
              f"(drift {rep.get('drift')}s)")
        ok_aud = v.get("has_audio") and v["duration"] > 5
        results.append(("output has picture and sound", ok_aud, []))
        OUT.say(f"  {'ok  ' if ok_aud else 'FAIL'} output has picture and sound")
    finally:
        if keep:
            OUT.say("kept:", d)
        else:
            shutil.rmtree(d, ignore_errors=True)

    bad = [n for n, ok, _ in results if not ok]
    OUT.say(f"\n{len(results) - len(bad)}/{len(results)} passed")
    OUT.say("verdict:", "FAIL — " + ", ".join(bad) if bad else "PASS")
    return 1 if bad else 0
