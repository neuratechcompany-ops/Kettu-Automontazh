"""Pluggable speech-recognition backends.

The engine only ever needs one thing from ASR: words with start/end times. Which
library produces them is an implementation detail, so it is chosen at run time:

    mlx     — Apple Silicon, runs on the GPU via Metal. Fastest on a Mac.
    faster  — faster-whisper (CTranslate2). CPU everywhere, CUDA on Linux.

Both return the same shape, so nothing downstream knows the difference.
"""
from __future__ import annotations

import importlib.util
import platform

from .core import die, log

MODEL_IDS = {
    "mlx": {
        "turbo": "mlx-community/whisper-large-v3-turbo",
        "large": "mlx-community/whisper-large-v3-mlx",
        "medium": "mlx-community/whisper-medium-mlx",
        "small": "mlx-community/whisper-small-mlx",
        "tiny": "mlx-community/whisper-tiny-mlx",
    },
    "faster": {
        "turbo": "large-v3-turbo", "large": "large-v3",
        "medium": "medium", "small": "small", "tiny": "tiny",
    },
}


def _has(mod):
    return importlib.util.find_spec(mod) is not None


def available():
    out = []
    if platform.system() == "Darwin" and platform.machine() == "arm64" and _has("mlx_whisper"):
        out.append("mlx")
    if _has("faster_whisper"):
        out.append("faster")
    return out


def pick(name="auto"):
    got = available()
    if name != "auto":
        if name not in got:
            die(f"ASR backend {name!r} is not installed (available: {got or 'none'})")
        return name
    if not got:
        die("no ASR backend installed.\n"
            "  Apple Silicon:  pip install mlx-whisper\n"
            "  anywhere else:  pip install faster-whisper")
    return got[0]


def describe(name):
    if name == "mlx":
        return "MLX / Metal (Apple Silicon GPU)"
    try:
        import torch
        if torch.cuda.is_available():
            return "faster-whisper / CUDA"
    except Exception:                                    # noqa: BLE001
        pass
    return "faster-whisper / CPU"


def transcribe(wav, model_key, lang=None, backend="auto"):
    """-> {"language": str, "segments": [{s, e, text, words: [{w, s, e, p}]}]}"""
    be = pick(backend)
    model_id = MODEL_IDS[be].get(model_key, model_key)
    log(f"ASR: {model_id} via {describe(be)}")

    if be == "mlx":
        import mlx_whisper
        res = mlx_whisper.transcribe(
            str(wav), path_or_hf_repo=model_id, word_timestamps=True, language=lang,
            condition_on_previous_text=False, temperature=(0.0, 0.2, 0.4), verbose=None)
        segs = [{"s": s["start"], "e": s["end"], "text": s["text"],
                 "words": [{"w": w["word"], "s": w["start"], "e": w["end"],
                            "p": float(w.get("probability", 1.0))}
                           for w in (s.get("words") or [])]}
                for s in res.get("segments", [])]
        return {"language": res.get("language", lang), "model": model_id, "segments": segs}

    return _faster(wav, model_id, lang)


def _faster(wav, model_id, lang):
    from faster_whisper import WhisperModel
    device, compute = "cpu", "int8"
    try:
        import torch
        if torch.cuda.is_available():
            device, compute = "cuda", "float16"
    except Exception:                                    # noqa: BLE001
        pass
    m = WhisperModel(model_id, device=device, compute_type=compute)
    segments, info = m.transcribe(
        str(wav), word_timestamps=True, language=lang,
        condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4])
    segs = []
    for s in segments:
        segs.append({"s": s.start, "e": s.end, "text": s.text,
                     "words": [{"w": w.word, "s": w.start, "e": w.end,
                                "p": float(getattr(w, "probability", 1.0))}
                               for w in (s.words or [])]})
    return {"language": info.language, "model": model_id, "segments": segs}
