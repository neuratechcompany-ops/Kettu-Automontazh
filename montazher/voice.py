"""Measured voice chain: look at the speech spectrum, derive the EQ from it.

Same philosophy as the picture auto-grade. The target curve below is a typical
well-recorded spoken voice, referenced to the 200-400 Hz band; the correction is
the difference between it and what the file actually has, clamped so a bad
measurement can only ever produce a mild move.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from .core import OUT, FFMPEG, log, load_json, probe, save_json, stem_of, workdir

# (low, high, target level in dB relative to the 200-400 Hz band)
TARGET = [
    (60, 100, -12.0), (100, 160, 0.0), (160, 250, 0.0), (250, 400, 0.0),
    (400, 650, -2.0), (650, 1000, -4.0), (1000, 1600, -6.0), (1600, 2500, -8.0),
    (2500, 4000, -11.0), (4000, 6300, -16.0), (6300, 10000, -22.0),
    (10000, 14000, -30.0),
]
MAX_BOOST, MAX_CUT = 12.0, -8.0
NOISE_CEIL = -50.0          # a band is not boosted past this residual noise level


def measure(src, root="."):
    import numpy as np
    import numpy.fft as fft

    wav = workdir(root) / "cache" / f"{stem_of(src)}.voice.wav"
    wav.parent.mkdir(parents=True, exist_ok=True)
    if not wav.exists():
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                        "-i", str(src), "-vn", "-ac", "1", "-ar", "48000",
                        "-c:a", "pcm_s16le", str(wav)], check=True)
    import wave as wavemod
    w = wavemod.open(str(wav))
    sr = w.getframerate()
    x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768.0
    if len(x) < sr:
        raise RuntimeError("too little audio to measure")

    fr = int(sr * 0.04)
    n = len(x) // fr
    e = np.array([np.sqrt((x[i * fr:(i + 1) * fr] ** 2).mean() + 1e-12) for i in range(n)])
    db = 20 * np.log10(e + 1e-12)
    speech_db = float(np.percentile(db, 85))
    noise_db = float(np.percentile(db, 10))

    def band_spec(sel):
        idx = [i for i in range(n) if sel(db[i])]
        if not idx:
            return None
        win = np.hanning(fr)
        return np.mean([np.abs(fft.rfft(x[i * fr:(i + 1) * fr] * win))
                        for i in idx[:600]], axis=0)

    sp = band_spec(lambda d: d > speech_db - 4)
    ns = band_spec(lambda d: d < noise_db + 4)
    f = fft.rfftfreq(fr, 1 / sr)
    ref = 20 * np.log10(sp[(f >= 200) & (f < 400)].mean() + 1e-12)

    bands = []
    for lo, hi, tgt in TARGET:
        m = (f >= lo) & (f < hi)
        got = 20 * np.log10(sp[m].mean() + 1e-12) - ref
        nz = (20 * np.log10(ns[m].mean() + 1e-12) - ref + speech_db) if ns is not None else -99
        bands.append({"lo": lo, "hi": hi, "target": tgt,
                      "measured": round(float(got), 2), "noise_dbfs": round(float(nz), 1)})

    # decay after word endings -- a proxy for how much room is on the voice
    drops = []
    for i in range(1, n - 4):
        if db[i] > speech_db - 6 and db[i + 1] < db[i] - 3:
            seg = db[i + 1:i + 4]
            if len(seg) == 3:
                drops.append(float(seg[0] - seg[2]))
    reverb = float(np.median(drops)) if drops else 99.0

    return {"source": str(src), "sr": sr, "speech_dbfs": round(speech_db, 1),
            "noise_dbfs": round(noise_db, 1), "snr": round(speech_db - noise_db, 1),
            "reverb_decay_db": round(reverb, 1), "bands": bands}


def build(m, strength=1.0):
    chain, notes, gains = [], [], []
    clamp = lambda v, lo, hi: max(lo, min(hi, v))

    for b in m["bands"]:
        g = (b["target"] - b["measured"]) * strength
        g = clamp(g, MAX_CUT, MAX_BOOST)
        if g > 0 and b["noise_dbfs"] + g > NOISE_CEIL:      # do not lift the hiss
            g = max(0.0, NOISE_CEIL - b["noise_dbfs"])
        if b["hi"] <= 160:
            g = min(g, 0.0)     # rumble band: only ever cut it, never lift it
        if b["lo"] >= 10000:
            g *= 0.35                                       # nothing musical up there
        gains.append((b["lo"], b["hi"], round(g, 1)))

    chain.append("highpass=f=80:p=2")
    notes.append("срез рокота ниже 80 Гц")
    pts = []
    for lo, hi, g in gains:
        if abs(g) < 0.4:
            continue
        c = int((lo * hi) ** 0.5)
        pts.append(f"entry({c},{g})")
        notes.append(f"{lo}-{hi} Гц {g:+.1f} дБ")
    if pts:
        chain.append("firequalizer=gain_entry='" + ";".join(pts) + "'")

    if m["snr"] > 35:
        notes.append(f"с/ш {m['snr']:.0f} дБ — шумодав не нужен")
    else:
        chain.append("afftdn=nf=-28:tn=1")
        notes.append(f"с/ш {m['snr']:.0f} дБ — включён шумодав")

    if max(g for _, _, g in gains) > 5:
        chain.append("deesser=i=0.35:m=0.5:f=0.18")
        notes.append("де-эссер: верх поднят, шипящие надо придержать")

    chain.append("acompressor=threshold=-20dB:ratio=3:attack=8:release=180:makeup=2")
    notes.append("компрессор 3:1 — ровный уровень")
    return ",".join(chain), notes, gains


def profile(src, root=".", force=False, strength=1.0):
    p = workdir(root) / f"{stem_of(src)}.voice.json"
    if p.exists() and not force:
        d = load_json(p)
        if abs(d.get("strength", 1.0) - strength) < 1e-6:
            return d
    m = measure(src, root)
    chain, notes, gains = build(m, strength)
    d = {"source": str(src), "strength": strength, "measured": m,
         "chain": chain, "notes": notes, "gains": gains}
    save_json(p, d)
    return d


def cmd_voice(args):
    d = profile(args.source, args.root, args.force, args.strength)
    m = d["measured"]
    OUT.emit(measured=m, chain=d["chain"], notes=d["notes"], gains=d["gains"])
    OUT.say(f"речь {m['speech_dbfs']} dBFS · шум {m['noise_dbfs']} dBFS · "
          f"с/ш {m['snr']} дБ · спад после слова {m['reverb_decay_db']} дБ")
    OUT.say("\n  полоса          есть   цель   правка")
    for b, (lo, hi, g) in zip(m["bands"], d["gains"]):
        OUT.say(f"  {b['lo']:>5}-{b['hi']:<6} {b['measured']:+7.1f} {b['target']:+6.1f}  {g:+6.1f}")
    OUT.say("\nчто делаю:")
    for nte in d["notes"]:
        OUT.say(f"  · {nte}")
    OUT.say(f"\nцепочка: {d['chain']}")
