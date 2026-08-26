"""Shared helpers for the local video-edit engine. No network, no paid APIs."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- binaries ---

def _pick(env, *candidates):
    if os.environ.get(env) and Path(os.environ[env]).exists():
        return os.environ[env]
    for c in candidates:
        if Path(c).exists():
            return c
    found = shutil.which(candidates[-1].split("/")[-1])
    if found:
        return found
    die(f"not found: {candidates[-1]}")

FFMPEG = _pick("VE_FFMPEG", "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg", "/usr/local/opt/ffmpeg-full/bin/ffmpeg", "ffmpeg")
FFPROBE = _pick("VE_FFPROBE", "/opt/homebrew/opt/ffmpeg-full/bin/ffprobe", "/usr/local/opt/ffmpeg-full/bin/ffprobe", "ffprobe")

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mts", ".m2ts", ".mxf"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}


class _Out:
    """One place that decides whether a command talks to a human or to an agent.

    Human text goes to stdout as usual; with --json the same run emits a single
    JSON object instead, and every diagnostic line stays on stderr where it
    cannot corrupt the parse.
    """

    def __init__(self):
        self.json = False
        self.data = {}

    def emit(self, **kv):
        self.data.update(kv)
        return kv

    def say(self, *a, **kw):
        if not self.json:
            print(*a, **kw)

    def finish(self, command, ok=True, error=None):
        if not self.json:
            return
        payload = {"ok": ok, "command": command}
        if error:
            payload["error"] = error
        payload.update(self.data)
        print(json.dumps(payload, ensure_ascii=False, default=str))


OUT = _Out()


def die(msg, code=1):
    if OUT.json:
        OUT.finish("error", ok=False, error=str(msg))
        sys.exit(code)
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg):
    print(f"  · {msg}", file=sys.stderr, flush=True)


def run(args, quiet=True, check=True):
    """Run a command, returning stdout. Raises with real stderr on failure."""
    p = subprocess.run(args, capture_output=True, text=True)
    if check and p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-25:])
        raise RuntimeError(f"command failed ({p.returncode}):\n  {shlex.join(str(a) for a in args)[:400]}\n{tail}")
    if not quiet:
        print(p.stderr, file=sys.stderr)
    return p.stdout


def ff(args, **kw):
    return run([FFMPEG, "-hide_banner", "-nostdin", "-y", *[str(a) for a in args]], **kw)


def ff_stderr(args):
    """Run ffmpeg and return stderr (for the *detect filters that report there)."""
    p = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", *[str(a) for a in args]],
                       capture_output=True, text=True)
    return p.stderr

# -------------------------------------------------------------------- probe ---

def probe(path):
    path = str(path)
    raw = json.loads(run([FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", path]))
    v = next((s for s in raw["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in raw["streams"] if s["codec_type"] == "audio"), None)

    def frac(x, default=0.0):
        try:
            n, d = x.split("/")
            return float(n) / float(d) if float(d) else default
        except Exception:
            return default

    info = {
        "path": os.path.abspath(path),
        "name": os.path.basename(path),
        "duration": float(raw["format"].get("duration") or 0.0),
        "size_mb": round(int(raw["format"].get("size") or 0) / 1e6, 1),
        "has_video": v is not None,
        "has_audio": a is not None,
    }
    if v:
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = int(sd["rotation"])
        info.update({
            "w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "fps": round(frac(v.get("r_frame_rate", "0/1")), 3),
            "vcodec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
            "rotation": rot,
        })
        if abs(rot) in (90, 270):
            info["w"], info["h"] = info["h"], info["w"]
    if a:
        info.update({
            "acodec": a.get("codec_name"),
            "sample_rate": int(a.get("sample_rate") or 0),
            "channels": int(a.get("channels") or 0),
        })
    return info


def hhmmss(t, ms=True):
    t = max(0.0, float(t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if ms:
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def parse_time(x):
    """Accept 12.5, '1:23', '00:01:23.5'."""
    if isinstance(x, (int, float)):
        return float(x)
    parts = str(x).strip().split(":")
    return sum(float(p) * (60 ** i) for i, p in enumerate(reversed(parts)))

# ------------------------------------------------------------------ workdir ---

def workdir(root=".", create=True):
    d = Path(root).resolve() / ".ve"
    if create:
        d.mkdir(exist_ok=True)
        (d / "cache").mkdir(exist_ok=True)
    return d


def stem_of(path):
    return Path(path).stem.replace(" ", "_")


def words_path(src, root="."):
    return workdir(root) / f"{stem_of(src)}.words.json"


def load_json(p):
    return json.loads(Path(p).read_text())


def save_json(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=1))
    return p

# ---------------------------------------------------------------- filtering ---

GRADES = {
    "none": "",
    "neutral": "eq=contrast=1.05:saturation=1.06:gamma=1.01",
    "clean": "hqdn3d=2:1:3:3,eq=contrast=1.04:saturation=1.03",
    "cinematic": ("curves=r='0/0.02 0.25/0.22 0.5/0.5 0.75/0.78 1/0.98':"
                  "g='0/0.012 0.5/0.5 1/0.99':"
                  "b='0/0.06 0.25/0.28 0.5/0.5 0.75/0.72 1/0.94',"
                  "eq=contrast=1.08:saturation=0.96"),
    "warm": "colorbalance=rs=0.06:gs=0.01:bs=-0.06:rm=0.04:bm=-0.04,eq=saturation=1.06",
    "cool": "colorbalance=rs=-0.05:bs=0.07:rm=-0.03:bm=0.05,eq=saturation=1.03",
    "punch": "eq=contrast=1.18:saturation=1.22:brightness=0.008,unsharp=5:5:0.55:5:5:0.0",
    "bw": "hue=s=0,eq=contrast=1.16",
}

def filter_path(p):
    """Escape a path for use inside a filtergraph argument."""
    return str(p).replace("\\", "\\\\").replace(":", r"\:").replace("'", r"\'")


# Filter order is a hard rule: geometry -> denoise/grade -> sharpen -> fps -> format
def video_chain(info, canvas, grade="neutral", fit="pad", extra=None, reframe=None,
                pre=None):
    w, h, fps = canvas["w"], canvas["h"], canvas["fps"]
    f = []
    if pre:
        f.append(pre)   # denoise belongs at native resolution, before any scale
    if info.get("rotation"):
        pass  # ffmpeg auto-applies display matrix on decode
    if reframe:
        # sendcmd drives crop's x/y per frame, so the box follows the speaker
        cmds, cw, ch, x0, y0 = reframe
        f.append(f"sendcmd=f='{filter_path(cmds)}'")
        f.append(f"crop={cw}:{ch}:{x0}:{y0}")
        f.append(f"scale={w}:{h}:flags=lanczos")
    elif fit == "crop":
        f.append(f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos")
        f.append(f"crop={w}:{h}")
    else:
        f.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos")
        f.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    f.append("setsar=1")
    g = GRADES.get(grade)
    if g is None:   # not a preset name -- accept a literal filter chain
        g = grade if (grade and "=" in str(grade)) else GRADES["neutral"]
    if g:
        f.append(g)
    if extra:
        f.append(extra)
    f.append(f"fps=fps={fps}")
    f.append("format=yuv420p")
    return ",".join(x for x in f if x)


def quantize(dur, fps):
    """Whole frames only, so audio and video can never drift apart on concat."""
    return max(1, round(dur * fps)) / float(fps)


def audio_chain(dur, fade_ms=30, speed=1.0, denoise=False, extra=None, pad=True):
    """30 ms fades at every cut is a hard rule -- it kills click artefacts.
    apad is the other hard rule: a source whose audio ends before its video
    (very common) would otherwise let -shortest silently truncate the film."""
    f = ["aresample=48000:resampler=soxr:precision=28",
         "aformat=sample_fmts=fltp:channel_layouts=stereo"]
    if denoise:
        f.append("afftdn=nf=-25")
    if abs(speed - 1.0) > 1e-3:
        f.append(f"rubberband=tempo={speed:.6f}:pitchq=quality")
        dur = dur / speed
    if extra:
        f.append(extra)
    if pad:
        f.append("apad")
    fd = max(0.001, fade_ms / 1000.0)
    if dur > 2.5 * fd:
        f.append(f"afade=t=in:st=0:d={fd:.4f}")
        f.append(f"afade=t=out:st={dur - fd:.4f}:d={fd:.4f}")
    return ",".join(f)


def encode_args(mode="quality", crf=18):
    if mode == "fast":
        return ["-c:v", "h264_videotoolbox", "-q:v", "60", "-pix_fmt", "yuv420p"]
    if mode == "intermediate":
        return ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-g", "48"]
    return ["-c:v", "libx264", "-crf", str(crf), "-preset", "slow",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1"]


AUDIO_ENC = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
