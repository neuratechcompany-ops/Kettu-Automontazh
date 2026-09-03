"""Matplotlib-backed visualisations, dressed to match the film.

The hand-rolled charts cover the shapes a talking-head short actually needs. This
module is for the ones worth borrowing a plotting library for -- composition over
time, relationships, matrices -- while keeping the same surface, palette and type
as the captions, so a chart still reads as part of the video.

Palette and slot order come from `charts` and were checked with a validator, not
chosen by eye: re-ordering the hues breaks the colour-blind separation gates.
"""
from __future__ import annotations

from pathlib import Path

from .core import OUT, die, log
from . import charts

FONTS = Path(__file__).parent / "fonts"
SURFACE = "#0B1220"
INK = "#FFFFFF"
INK_MUTED = "#BEC6D6"
GRID = "#1C2436"

# Type on a phone-sized vertical frame has to be several times what a report would
# use. These are all derived from frame height so 1080x1920 and 1920x1080 agree.
def TICK(H):
    return max(16, H / 62)


def LEGEND(H):
    return max(18, H / 54)


def _setup(W, H):
    try:
        import matplotlib
    except ImportError:
        die("для `viz` нужен matplotlib. Поставь:\n"
            "  pip install 'kettu-automontazh[viz]'\n"
            "Встроенные типы (`chart`) работают без него.")
    matplotlib.use("Agg")
    from matplotlib import font_manager
    import matplotlib.pyplot as plt

    for f in ("Onest-ExtraBold.ttf", "Montserrat-SemiBold.ttf"):
        p = FONTS / f
        if p.exists():
            font_manager.fontManager.addfont(str(p))
    plt.rcParams.update({
        "font.family": "Onest",
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "text.color": INK,
        "axes.labelcolor": INK_MUTED,
        "xtick.color": INK_MUTED,
        "ytick.color": INK_MUTED,
        "axes.edgecolor": GRID,
        "grid.color": GRID,
        "axes.grid": True,
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "xtick.labelsize": TICK(H),
        "ytick.labelsize": TICK(H),
        "axes.linewidth": max(1.5, H / 900),
        "grid.linewidth": max(1.0, H / 1400),
        "lines.linewidth": max(3.0, H / 480),
        "legend.frameon": False,
        "legend.labelcolor": INK_MUTED,
        "legend.fontsize": LEGEND(H),
        "legend.handlelength": 1.6,
        "legend.handleheight": 1.1,
    })
    dpi = 100
    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi)
    # the plot lives in the middle band: captions and platform UI own the edges
    ax = fig.add_axes([0.145, 0.30, 0.75, 0.40])
    ax.tick_params(pad=max(6, H / 200))
    return plt, fig, ax


def _to_image(fig):
    import numpy as np
    from PIL import Image
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    return Image.fromarray(buf).convert("RGB")


def _title(fig, text, H, W):
    """Shrink until it fits the frame. A clipped headline is worse than a small one."""
    if not text:
        return
    size = max(20, H / 38)
    t = fig.text(0.5, 0.755, text.upper(), ha="center", va="center",
                 fontsize=size, color=INK)
    fig.canvas.draw()
    for _ in range(14):
        if t.get_window_extent(fig.canvas.get_renderer()).width <= W * 0.90:
            break
        size *= 0.93
        t.set_fontsize(size)
        fig.canvas.draw()


def _series(name_values):
    """'Имя=1,2,3;Другое=4,5,6' -> [(name, [floats])]"""
    out = []
    for chunk in (name_values or "").split(";"):
        if not chunk.strip():
            continue
        name, _, vals = chunk.partition("=")
        try:
            out.append((name.strip(), [float(v) for v in vals.split(",") if v.strip()]))
        except ValueError:
            die(f"не разобрал ряд {chunk!r}; формат: 'Имя=1,2,3;Другое=4,5,6'")
    if not out:
        die("нет данных: --data 'Имя=1,2,3;Другое=4,5,6'")
    return out


def render_area(args, W, H, fps):
    """Composition over time. Stacked by default -- the parts make a whole."""
    plt, fig, ax = _setup(W, H)
    data = _series(args.data)
    labels = [x.strip() for x in (args.labels or "").split(",") if x.strip()]
    n = max(1, int(args.dur * fps))
    xs = list(range(len(data[0][1])))
    top = max(sum(v[i] for _, v in data) for i in xs) or 1.0

    frames = []
    for k in range(n):
        prog = charts.ease_out(min(1.0, (k / max(1, n - 1)) / 0.85))
        shown = 1 + prog * (len(xs) - 1)
        cut = int(shown)
        ax.clear()
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        x_part = xs[:cut + 1]
        if len(x_part) > 1:
            frac = shown - cut
            series = []
            for _, vals in data:
                v = list(vals[:cut + 1])
                if cut + 1 < len(vals):
                    v[-1] = vals[cut] + (vals[cut + 1] - vals[cut]) * frac
                series.append(v)
            ax.stackplot(x_part, *series,
                         colors=charts.SERIES[:len(data)],
                         labels=[nm for nm, _ in data], alpha=0.95,
                         edgecolor=SURFACE, linewidth=max(2, H / 640))
        ax.set_xlim(0, len(xs) - 1)
        ax.set_ylim(0, top * 1.1)
        if labels:
            ax.set_xticks(xs[:len(labels)])
            ax.set_xticklabels([l.upper() for l in labels])
        if len(data) > 1:
            ax.legend(loc="upper left", fontsize=LEGEND(H), markerscale=1.4)
        _title(fig, args.title, H, W)
        frames.append(_to_image(fig))
    plt.close(fig)
    return frames


def render_scatter(args, W, H, fps):
    """Relationship between two measures. Capped at three series by the palette."""
    plt, fig, ax = _setup(W, H)
    data = _series(args.data)
    if len(data) > charts.SCATTER_CAP:
        die(f"точечный график: не больше {charts.SCATTER_CAP} рядов — дальше цвета "
            f"перестают различаться при дальтонизме. Сверни лишнее в «Другое».")
    pts = []
    for name, vals in data:
        if len(vals) % 2:
            die(f"ряд {name!r}: нужны пары x,y — получено нечётное число значений")
        pts.append((name, vals[0::2], vals[1::2]))

    n = max(1, int(args.dur * fps))
    frames = []
    for k in range(n):
        prog = charts.ease_out(min(1.0, (k / max(1, n - 1)) / 0.8))
        ax.clear()
        ax.set_facecolor(SURFACE)
        ax.grid(True, color=GRID, linewidth=0.8)
        ax.spines[["top", "right"]].set_visible(False)
        for i, (name, xs, ys) in enumerate(pts):
            m = max(1, int(len(xs) * prog))
            ax.scatter(xs[:m], ys[:m], s=(H / 4.2), c=charts.SERIES[i],
                       edgecolors=SURFACE, linewidths=max(2, H / 640),
                       label=name, zorder=3)
        if len(pts) > 1:
            ax.legend(loc="upper left", fontsize=LEGEND(H), markerscale=1.4)
        _title(fig, args.title, H, W)
        frames.append(_to_image(fig))
    plt.close(fig)
    return frames


def render_heat(args, W, H, fps):
    """A matrix of intensity. Sequential = one hue, light to dark. Never a rainbow."""
    from matplotlib.colors import LinearSegmentedColormap
    plt, fig, ax = _setup(W, H)
    rows = _series(args.data)
    width = len(rows[0][1])
    if any(len(v) != width for _, v in rows):
        die("в тепловой карте все строки должны быть одной длины")
    longest = max(len(nm) for nm, _ in rows)
    left = min(0.42, 0.145 + longest * TICK(H) * 0.62 / W)   # room for row labels
    ax.set_position([left, 0.30, min(0.95 - left, 0.88), 0.40])
    cmap = LinearSegmentedColormap.from_list("solo", ["#16203a", charts.SOLO])
    cols = [x.strip().upper() for x in (args.labels or "").split(",") if x.strip()]

    import numpy as np
    m = np.array([v for _, v in rows], dtype=float)
    n = max(1, int(args.dur * fps))
    frames = []
    for k in range(n):
        prog = charts.ease_out(min(1.0, (k / max(1, n - 1)) / 0.8))
        ax.clear()
        ax.set_facecolor(SURFACE)
        ax.grid(False)
        ax.spines[["top", "right", "left", "bottom"]].set_visible(False)
        ax.imshow(m * prog, cmap=cmap, aspect="auto",
                  vmin=0, vmax=m.max() or 1.0)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([nm.upper() for nm, _ in rows])
        if cols:
            ax.set_xticks(range(len(cols)))
            ax.set_xticklabels(cols)
        else:
            ax.set_xticks([])
        ax.tick_params(length=0)
        _title(fig, args.title, H, W)
        frames.append(_to_image(fig))
    plt.close(fig)
    return frames


KINDS = {"area": render_area, "scatter": render_scatter, "heat": render_heat}


def cmd_viz(args):
    W = args.width or 1080
    H = args.height or 1920
    if args.kind not in KINDS:
        die(f"неизвестный тип: {args.kind}")
    frames = KINDS[args.kind](args, W, H, args.fps)
    out = Path(args.out or f"edit/viz_{args.kind}.mp4")
    out.parent.mkdir(parents=True, exist_ok=True)
    charts._pipe(frames, W, H, args.fps, out)
    OUT.emit(path=str(out), kind=args.kind, frames=len(frames),
             duration=round(len(frames) / args.fps, 3), w=W, h=H)
    log(f"{args.kind}: {len(frames)} кадров, {len(frames)/args.fps:.1f}с -> {out}")
    OUT.say(out)
