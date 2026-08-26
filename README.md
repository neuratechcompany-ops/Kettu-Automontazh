# Montazher

*Part of the Kettu toolchain. The installed command is `montazher`.*

A local, free video-editing engine for coding agents.

The agent does not watch the video. It **reads** it: an hour of footage is ~12 KB of
word-level transcript instead of 108 000 frames. It decides the cut from the text,
and the engine renders it with ffmpeg.

No cloud, no API keys, no per-minute billing. Speech recognition runs on your
machine (Metal on Apple Silicon, CPU or CUDA elsewhere); everything else is ffmpeg.

## What it does

- cuts filler words, dead air, repeated verbal tics and blown takes
- burns word-level captions in several styles
- grades the picture from measurements, not from a preset
- fixes the voice: measured EQ, de-esser, compressor, loudness to spec
- follows the speaker's face when reframing 16:9 to 9:16
- draws graphic overlays and cutaway cards
- checks its own output and reports what is wrong

## Install

```
pip install montazher[mlx]      # Apple Silicon
pip install montazher[faster]   # Linux / Windows / Intel Mac
```

You also need `ffmpeg` built with libass, freetype and libplacebo-free filters.
Run `montazher doctor` — it tells you exactly what is missing.

## Use

```
montazher shorts take.mp4
```

One command: transcribe, cut, grade, fix the voice, caption, render, verify.

For anything non-standard, work the pipeline step by step and edit the EDL — see
[AGENT.md](AGENT.md), which is written for an agent to follow.

## Licence

MIT. Bundled Montserrat is SIL OFL 1.1; the bundled YuNet face detector is from
OpenCV Zoo (Apache 2.0).
