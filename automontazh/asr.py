"""Local ASR (MLX / Metal) + transcript packing + draft-cut generation."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import backends
from .core import (OUT, die, ff, hhmmss, load_json, log, probe, save_json, stem_of,
                   words_path, workdir)

MODELS = ("turbo", "large", "medium", "small", "tiny")

# Two tiers, because the cost of a wrong cut is much higher than a missed one.
# HARD: pure hesitation noise. Never a real word -- safe to cut anywhere.
HARD_FILLERS = {
    "ru": {"э", "ээ", "эээ", "ээээ", "эм", "эмм", "ээм", "м", "мм", "ммм", "мэ",
           "аа", "ааа", "ых", "кхм"},
    "en": {"um", "umm", "ummm", "uh", "uhh", "uhm", "erm", "er", "ahh", "hmm",
           "hmmm", "mhm", "mm", "mmm"},
}
# LEAD: discourse openers. Parasitic when they *start* a phrase after a pause --
# dropping them still leaves the phrase beginning cleanly on a real word.
LEAD_FILLERS = {
    "ru": {"ну", "вот", "короче", "значит", "слушай", "смотри", "так",
           "собственно", "итак"},
    "en": {"so", "well", "okay", "anyway", "right", "basically", "actually", "look"},
}
# ISOLATED: real words that are only parasitic when the speaker hesitated on BOTH
# sides. "Привет, ЭТО тест" must survive; "ну ... это ... короче" must not.
ISOLATED_FILLERS = {
    "ru": {"это", "эт", "типа", "как-бы", "блин", "а", "и"},
    "en": {"like", "just", "literally", "yeah"},
}
SOFT_PAUSE = 0.20   # s of silence that counts as a hesitation

# Verbal tics that span several words. Same two-tier logic as single words:
#   ALWAYS — pure padding, safe to cut wherever it appears
#   REPEAT — fine once, a tic on the way back: keep the FIRST, cut every echo
# Everything else that repeats is only *reported* (`ve tics`), never auto-cut:
# a rhetorical repetition is a device, and cutting it breaks the sentence.
PHRASE_ALWAYS = {
    "ru": [("так", "сказать"), ("что", "называется"), ("скажем", "так")],
    "en": [("so", "to", "speak"), ("as", "it", "were")],
}
PHRASE_REPEAT = {
    "ru": [("на", "самом", "деле"), ("то", "есть"), ("по", "сути"),
           ("в", "принципе"), ("в", "общем"), ("как", "бы"), ("если", "честно"),
           ("грубо", "говоря"), ("собственно", "говоря"), ("я", "бы", "сказал")],
    "en": [("you", "know"), ("i", "mean"), ("to", "be", "honest"), ("sort", "of"),
           ("kind", "of"), ("at", "the", "end", "of", "the", "day")],
}


def norm(w):
    w = unicodedata.normalize("NFKC", w).lower().strip()
    return re.sub(r"[^\w\-']", "", w, flags=re.UNICODE)


def _dup_ranges(words, n_lo=3, n_hi=10, window=25.0):
    """False starts: the same phrase said twice in a row -> drop the first pass."""
    toks = [norm(w["w"]) for w in words]
    drop, i = set(), 0
    while i < len(toks):
        hit = 0
        for n in range(n_hi, n_lo - 1, -1):
            if i + 2 * n > len(toks):
                continue
            a, b = toks[i:i + n], toks[i + n:i + 2 * n]
            if a == b and all(a) and words[i + 2 * n - 1]["e"] - words[i]["s"] < window:
                hit = n
                break
        if hit:
            drop.update(range(i, i + hit))
            i += hit
        else:
            i += 1
    return drop


def phrase_hits(toks, phrases):
    """Non-overlapping occurrences of each phrase, longest match winning."""
    out, used = [], set()
    for ph in sorted(phrases, key=len, reverse=True):
        n = len(ph)
        for i in range(len(toks) - n + 1):
            span = set(range(i, i + n))
            if tuple(toks[i:i + n]) == ph and not (span & used):
                out.append((i, i + n, ph))
                used |= span
    return sorted(out)


def repeated_phrases(words, n_lo=2, n_hi=6, min_count=2):
    """Any multi-word phrase said more than once. Reported, never cut."""
    from collections import defaultdict
    toks = [norm(w["w"]) for w in words]
    seen = defaultdict(list)
    for n in range(n_hi, n_lo - 1, -1):
        for i in range(len(toks) - n + 1):
            ph = tuple(toks[i:i + n])
            if all(ph) and len(" ".join(ph)) > 6:
                seen[ph].append(i)
    cand = sorted(((ph, ix) for ph, ix in seen.items() if len(ix) >= min_count),
                  key=lambda x: (-len(x[0]), -len(x[1])))
    kept = []
    for ph, ix in cand:                       # drop phrases inside longer ones
        text = " ".join(ph)
        if any(text in " ".join(k[0]) for k in kept):
            continue
        kept.append((ph, ix))
    return [(" ".join(ph), [round(words[i]["s"], 1) for i in ix],
             [(i, i + len(ph)) for i in ix]) for ph, ix in kept]


def droppable(words, lang, keep_fillers=False, keep_retakes=False, keep_phrases=False):
    """Indices autocut would remove. Shared by `pack` so the legend never lies."""
    hard = HARD_FILLERS.get(lang, set())
    lead = LEAD_FILLERS.get(lang, set())
    iso = ISOLATED_FILLERS.get(lang, set())
    drop = set()
    if not keep_fillers:
        for i, w in enumerate(words):
            n = norm(w["w"])
            if not n:
                continue
            before = w["s"] - words[i - 1]["e"] if i else 9.0
            after = words[i + 1]["s"] - w["e"] if i + 1 < len(words) else 9.0
            if n in hard:
                drop.add(i)
            elif n in lead and before >= SOFT_PAUSE:
                drop.add(i)
            elif n in iso and before >= SOFT_PAUSE and after >= SOFT_PAUSE:
                drop.add(i)
    if not keep_phrases:
        toks = [norm(w["w"]) for w in words]
        for i, j, _ in phrase_hits(toks, PHRASE_ALWAYS.get(lang, [])):
            drop |= set(range(i, j))
        first = set()
        for i, j, ph in phrase_hits(toks, PHRASE_REPEAT.get(lang, [])):
            if ph in first:
                drop |= set(range(i, j))     # the echo, not the first use
            else:
                first.add(ph)
    if not keep_retakes:
        drop |= _dup_ranges(words)
    return drop


# ------------------------------------------------------------- transcribe ---

def cmd_transcribe(args):
    src = Path(args.source).resolve()
    if not src.exists():
        die(f"no such file: {src}")
    wd = workdir(args.root)
    out = words_path(src, args.root)
    if out.exists() and not args.force:
        log(f"cached: {out.name} (use --force to redo)")
        OUT.say(out)
        return

    info = probe(src)
    if not info.get("has_audio"):
        die(f"{src.name} has no audio track")

    wav = wd / "cache" / f"{stem_of(src)}.16k.wav"
    if not wav.exists() or args.force:
        log(f"extracting audio -> {wav.name}")
        ff(["-i", src, "-vn", "-ac", "1", "-ar", "16000",
            "-af", "aresample=resampler=soxr:precision=28", "-c:a", "pcm_s16le", wav])

    log(f"transcribing {hhmmss(info['duration'], ms=False)} of audio")
    res = backends.transcribe(wav, args.model, args.lang,
                              getattr(args, "backend", "auto"))

    words, segments = [], []
    for seg in res["segments"]:
        segments.append({"s": round(seg["s"], 3), "e": round(seg["e"], 3),
                         "text": seg["text"].strip()})
        for w in seg.get("words") or []:
            t = w["w"].strip()
            if t:
                words.append({"w": t, "s": round(w["s"], 3), "e": round(w["e"], 3),
                              "p": round(w["p"], 3)})
    doc = {"source": str(src), "duration": info["duration"],
           "language": res.get("language", args.lang), "model": res["model"],
           "words": words, "segments": segments}
    save_json(out, doc)
    OUT.emit(path=str(out), words=len(words),
             segments=len(segments), language=doc["language"], model=doc["model"])
    log(f"{len(words)} words, {len(segments)} segments -> {out.name} "
        f"({out.stat().st_size / 1024:.0f} KB)")
    OUT.say(out)


# -------------------------------------------------------------------- pack ---

def _load(ref, root):
    return load_json(ref if str(ref).endswith(".json") else words_path(ref, root))


def cmd_pack(args):
    doc = _load(args.words, args.root)
    words, dur = doc["words"], doc["duration"]
    lang = (doc.get("language") or "ru")[:2]
    drop = droppable(words, lang)

    lines = [
        f"# {Path(doc['source']).name} — {hhmmss(dur, ms=False)}, {lang}, "
        f"{len(words)} words, model {doc['model'].split('/')[-1]}",
        "# legend: [id start–end] text · ⟨x⟩ = autocut removes this token · "
        "⏸N.Ns = dead air before the line · ⚠ = low ASR confidence",
        "",
    ]
    by_idx = {id(w): i for i, w in enumerate(words)}
    prev_end, idx = 0.0, 0
    for seg in doc["segments"]:
        sw = [w for w in words if w["s"] >= seg["s"] - 0.05 and w["e"] <= seg["e"] + 0.05]
        if not sw:
            continue
        idx += 1
        parts = []
        for w in sw:
            tok = f"⟨{w['w']}⟩" if by_idx[id(w)] in drop else w["w"]
            if w["p"] < 0.45:
                tok += "⚠"
            parts.append(tok)
        gap = seg["s"] - prev_end
        pause = f"⏸{gap:.1f}s " if gap > 0.7 else ""
        lines.append(f"[{idx} {hhmmss(sw[0]['s'])}–{hhmmss(sw[-1]['e'])}] {pause}"
                     + " ".join(parts))
        prev_end = seg["e"]

    out = workdir(args.root) / f"{stem_of(doc['source'])}.transcript.md"
    out.write_text("\n".join(lines))
    log(f"{out.name} ({out.stat().st_size / 1024:.1f} KB) — read this, not the video")
    OUT.emit(path=str(out))
    OUT.say(out)


# ----------------------------------------------------------------- autocut ---

def cmd_autocut(args):
    doc = _load(args.words, args.root)
    words = doc["words"]
    if not words:
        die("empty transcript")
    lang = (doc.get("language") or "ru")[:2]
    if getattr(args, "gentle", False):
        # A contemplative monologue lives in its pauses. Chopping them turns a
        # thought into a stutter, so only the longest silences go.
        args.max_gap = max(args.max_gap, 1.6)
        args.tail = max(args.tail, 0.30)
        args.min_clip = max(args.min_clip, 1.2)
        log("бережный режим: паузы до 1.6с сохраняются")

    drop = droppable(words, lang, args.keep_fillers, args.keep_retakes,
                     getattr(args, "keep_phrases", False))

    # A run breaks on a removed word (so the cut is real, not cosmetic) or on
    # dead air longer than --max-gap.
    runs, cur = [], []
    for i, w in enumerate(words):
        if i in drop:
            if cur:
                runs.append(cur)
                cur = []
            continue
        if cur and w["s"] - words[cur[-1]]["e"] > args.max_gap:
            runs.append(cur)
            cur = []
        cur.append(i)
    if cur:
        runs.append(cur)
    if not runs:
        die("everything got dropped — loosen the thresholds")

    src, dur = doc["source"], doc["duration"]
    clips = []
    for run in runs:
        i, j = run[0], run[-1]
        a = words[i]["s"] - args.lead
        b = words[j]["e"] + args.tail
        # padding must never reach across a neighbour word we deliberately cut
        a = max(a, words[i - 1]["e"] + 0.02 if i else 0.0)
        b = min(b, words[j + 1]["s"] - 0.02 if j + 1 < len(words) else dur)
        a, b = max(0.0, a), min(dur, b)
        if b - a < args.min_clip:
            continue
        clips.append({"src": src, "in": round(a, 3), "out": round(b, 3)})
    if not clips:
        die("no clip survived --min-clip")

    # What follows the last word is usually the landing, not dead air: a breath,
    # a smile, a beat before the frame ends. Cutting it makes a warm ending stop
    # dead. Keep it when it is short.
    tail_left = dur - clips[-1]["out"]
    if 0 < tail_left <= args.keep_ending:
        clips[-1]["out"] = round(dur, 3)
        log(f"хвост {tail_left:.2f}с после последнего слова оставлен — это концовка, "
            f"а не мёртвый воздух")

    total = sum(c["out"] - c["in"] for c in clips)
    edl = {
        "version": 1, "output": args.output,
        "canvas": {"w": args.width, "h": args.height, "fps": args.fps},
        "audio": {"loudnorm": True, "target_lufs": args.lufs, "fade_ms": 30,
                  "denoise": False},
        "grade": args.grade, "fit": args.fit,
        "captions": {"enabled": bool(args.captions), "style": args.captions or "hormozi"},
        "clips": clips,
    }
    save_json(Path(args.edl), edl)
    removed = [words[i]["w"] for i in sorted(drop)]
    log(f"removed {len(drop)}/{len(words)} words: "
        + (", ".join(removed[:12]) + ("…" if len(removed) > 12 else "") if removed else "—"))
    OUT.emit(path=str(args.edl), clips=len(clips), removed=len(drop),
             words=len(words), source_duration=round(dur, 2),
             edit_duration=round(total, 2),
             kept_pct=round(100 * total / max(dur, 0.01), 1))
    log(f"{len(clips)} clips · {hhmmss(dur, ms=False)} -> {hhmmss(total, ms=False)} "
        f"({100 * total / max(dur, 0.01):.0f}% kept, {dur - total:.1f}s cut)")
    OUT.say(args.edl)


# -------------------------------------------------------------------- pull ---

def cmd_pull(args):
    """Fetch an ASR model into the local cache.

    HF's Xet transfer backend stalls at 0 KB/s on some networks and, worse, does
    not resume -- every retry restarts from zero. We force the plain HTTP path.
    """
    import os
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "30")
    from huggingface_hub import snapshot_download

    be = backends.pick(getattr(args, "backend", "auto"))
    repo = backends.MODEL_IDS[be].get(args.model, args.model)
    if be == "faster":
        log("faster-whisper downloads on first use; nothing to pre-pull")
        return 0
    last = None
    for attempt in range(1, args.retries + 1):
        try:
            p = snapshot_download(repo, max_workers=1)
            log(f"{repo} ready at {p}")
            OUT.say(p)
            return 0
        except Exception as e:                       # noqa: BLE001 - report and retry
            last = e
            log(f"attempt {attempt}/{args.retries} failed: {str(e)[:160]}")
    die(f"could not fetch {repo}: {last}")


def cmd_tics(args):
    """Report repeated phrases. What autocut already handles is marked, the rest
    is left to a human: rhetorical repetition is a device, not a stumble."""
    doc = _load(args.words, args.root)
    words = doc["words"]
    lang = (doc.get("language") or "ru")[:2]
    auto = {" ".join(p) for p in
            PHRASE_ALWAYS.get(lang, []) + PHRASE_REPEAT.get(lang, [])}
    rep = repeated_phrases(words, min_count=args.min_count)
    if not rep:
        OUT.say("повторов не найдено")
        return
    OUT.emit(phrases=[{"text": t, "count": len(ts), "times": ts,
                       "auto_cut": t in auto} for t, ts, _ in rep])
    OUT.say(f"{len(rep)} повторяющ(их)ся фраз на {hhmmss(doc['duration'], ms=False)}:\n")
    OUT.say(f"  {'раз':>3}  {'фраза':<34} {'когда':<26} режется?")
    for text, times, _spans in rep:
        mark = "да, кроме первой" if text in auto else "нет — решай сам"
        ts = ", ".join(hhmmss(t, ms=False) for t in times[:5])
        OUT.say(f"  {len(times):>3}× {text[:34]:<34} {ts:<26} {mark}")
    OUT.say("\nчтобы вырезать не входящее в список — правь клипы в EDL руками.")
