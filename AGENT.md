# Montazher — instructions for an agent

You are editing video for a person who shot the footage themselves. You do not
generate images or sound; you decide **what to keep, in what order, and how it
should look**, and the engine renders it.

Read this once before your first edit. Everything here was learned by breaking it.

## The operating model: read, do not watch

An hour of 1080p is ~108 000 frames. Feeding those to a model is both ruinous and
pointless: the decisions that make an edit good are decisions about **speech**.

So the loop is:

1. `montazher transcribe FILE` — word-level timings, locally.
2. `montazher pack FILE` — the transcript as compact markdown. **Read this.** An
   hour of footage is ~12 KB.
3. Decide the cut *from the text*, write it as an EDL (JSON).
4. `montazher render edl.json` — ffmpeg does the work.
5. `montazher verify OUT --edl .ve/edl.resolved.json` — the engine checks itself.

Look at actual pixels only when the decision needs them (see *When to look*).

## Machine mode

Add `--json` to any command and it prints exactly one JSON object:

```
{"ok": true, "command": "render", "output": "edit/final.mp4", "duration": 32.1, ...}
```

Human prose goes to stdout only without `--json`; progress and warnings always go
to stderr. On failure you get `{"ok": false, "error": "..."}` and a non-zero exit.
Never parse the prose — parse the JSON.

## The fast path

```
montazher shorts take.mp4
```

Transcribe → cut → grade the picture → fix the voice → reframe → caption → verify.
Output in `edit/shorts.mp4`, the edit decision list in `shorts.json`.

**Then edit the EDL, not the command.** Re-rendering reuses cached clips and takes
seconds. Build the pipeline by hand only for the non-standard: several sources,
cutaways, a deliberate order, horizontal delivery.

## Hard rules

These are not style preferences. Breaking them produces artefacts a viewer notices.

1. **Cut on word boundaries.** Use the ASR word times; never cut mid-word.
2. **Keep the air.** Pad 0.08 s before and 0.14 s after. Landing exactly on the
   boundary clips consonants and the edit sounds bitten off.
3. **30 ms audio fades at every cut.** Non-negotiable; without them every splice
   ticks.
4. **Normalise loudness once, at the end**, two-pass EBU R128 to −16 LUFS, true
   peak under −1 dB.
5. **Denoise before scaling, sharpen after.** The other order smears then
   sharpens the smear.
6. **Never lift clipped highlights.** If more than ~15 % of the frame is at 255,
   that data is gone. Say so; do not promise recovery.
7. **Below 160 Hz, only cut.** That band is rumble, not voice.
8. **Verify before you report.** `verify` catches drift, clipping, black frames
   and dead air. A render you have not verified is not finished.

## What the auto-cut removes on its own

- Hesitation noise (`э`, `ммм`, `um`, `uh`) — always.
- Discourse openers (`ну`, `вот`, `so`, `well`) — only when a pause precedes them,
  so the phrase still starts on a real word.
- Ambiguous words (`это`, `типа`, `like`, `just`) — only when a pause sits on
  **both** sides. "Привет, **это** тест" must survive.
- Multi-word tics (`на самом деле`, `you know`, `i mean`) — the first use stays,
  every echo goes. Once is speech; twice is a tic.
- Blown takes: the same phrase said twice in a row — the first pass is dropped.
- Dead air longer than `--max-gap`.

Everything else that repeats is **reported, never cut**: `montazher tics FILE`.
Rhetorical repetition is a device. Cutting it breaks the sentence, and only a human
can tell the difference.

## Measure, then decide

Two commands look at the actual file and derive the correction from it, rather
than applying a preset:

- `montazher enhance FILE` — picture: black point, clipping, white balance
  (measured on a bright neutral surface, **not** on mid-tones, which skin
  dominates), saturation, sharpness, noise.
- `montazher voice FILE` — sound: per-band spectrum against a speech target,
  noise floor per band, signal-to-noise, reverb tail.

Run them and read the numbers before promising anything. A common surprise: phone
footage usually has a 40+ dB signal-to-noise ratio, so a denoiser has nothing to
do — the real problem is a dull, boomy tone, which is an EQ problem.

## Captions

Built from word-level ASR; a word lights up exactly when it is spoken.

| style | look |
|---|---|
| `chip` | white caps, spoken word in a filled rounded pill |
| `hormozi` | big, 3 words, spoken word in yellow |
| `karaoke` | word fills as it is said |
| `standard` / `minimal` | conventional subtitles |

Fonts ship with the package; nothing depends on what the host system has. To add
one, drop a **static** TTF into `montazher/fonts/`. Variable fonts do not work —
libass takes their default (Regular) instance. Name the ASS style after the family
recorded *inside the file*, and keep one weight per family, or libass silently
substitutes a different face and measured layout stops matching.

## Graphics

Cards are drawn in the same ASS pass — no extra render, no asset files. Times are
given in **source** seconds; the engine maps them onto the cut timeline and skips
anything that landed in a removed section.

- `full` is a **scrim over the live picture**, not a black slide. Cutting to black
  with big type reads as a title card from 2005.
- Anything with a background bar is placed from the **measured face band**. Every
  "safe" constant eventually lands on the speaker's chin.
- Numbered badges exist but are off by default; ask before using them.

## Vertical and reframing

`--reframe` follows the speaker's face when going 16:9 → 9:16, easing between
positions with a dead zone so small movements do not jitter. If a face is found in
under 25 % of frames it says so and falls back to a static centre crop. If the
source is already vertical it does nothing.

## When to look at pixels

`montazher frames FILE --at 12.5,30,45` or `--start A --end B -n 12`. Worth it when:
choosing between visually different takes, confirming someone is in frame, hunting
for a cutaway, or accepting the final result. Never scan the whole video — that is
the cost this whole design exists to avoid.

## Be honest about limits

- One speaker per track; there is no diarisation.
- B-roll is not chosen for you: place `v_src` clips yourself.
- Reverb ("room tail" on the voice) is not fixable with EQ.
- Upscaling adds pixels, not detail. Say that plainly.
- `verify` checks technique, not meaning. Meaning is your job, from the transcript.

## Reference

`montazher --help` lists every command. `montazher doctor` checks the toolchain and
names what is missing. `montazher selftest` drives the whole pipeline through a
synthetic take and reports pass/fail per stage — run it after changing the engine.
