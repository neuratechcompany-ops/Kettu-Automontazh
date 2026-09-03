# Style references

Decisions that were made by looking, not by reasoning. Each image is the evidence
for a rule written in [AGENT.md](../../AGENT.md) — when in doubt, open the picture
rather than re-deriving the rule.

| | shows | the decision it settles |
|---|---|---|
| `01-theme-directions.png` | five themes × three chart kinds | **`glass` and `paper` were chosen.** The other three stay available but are not defaults |
| `02-theme-glass-all-kinds.png` | every graphic kind in `glass` | a card on a violet gradient; reads premium over dark footage |
| `03-theme-paper-all-kinds.png` | every graphic kind in `paper` | cream surface, dark ink; an editorial cut-in against warm footage |
| `04-graphic-kinds.png` | the ten kinds side by side | what the engine can draw |
| `05-antialiasing-before-after.png` | one donut, 1× vs 2× render | Pillow does not antialias. **Left is what "jagged" means**; supersampling is not optional |
| `06-matplotlib-under-themes.png` | the same plots in `paper` and `glass` | matplotlib obeys the theme; it used to carry a frozen surface of its own |
| `07-grade-strength.png` | a 1:1 face crop at 0 / 0.35 / 0.6 / 1.0 | **0.6 is the working default.** At 1.0 the denoiser waxes the skin and the sharpener advertises it. Judge on a 1:1 crop; a scaled frame hides it |
| `08-cover-candidates.png` | three cover frames scored by the engine | a machine ranks face size and sharpness; **it cannot judge an expression** — a person picks |
| `09-caption-fonts.png` | the three bundled caption faces | Onest is the caption default. Montserrat was rejected by the client as "not it" |

## What none of these show

Motion. Every rule about easing, stagger and how long a cutaway may run lives in
the docs, because a still cannot carry it. When changing timing, render a clip and
watch it — the contact sheet will look fine either way.
