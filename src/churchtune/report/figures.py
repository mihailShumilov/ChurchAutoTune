"""PNG charts for the session report, labelled in the report's language.

The same style as ``plots`` (object API on the Agg canvas inside a local style context, no
global matplotlib state); labels come from the translate function the report is rendered with.
"""

import io
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import matplotlib as mpl
import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.points import Analysis
from churchtune.design.alignment import AlignmentResult
from churchtune.domain.arrays import FloatArray
from churchtune.report.model import Translate
from churchtune.report.plots import (
    APPROX_FROM_HZ,
    AVERAGE_COLOR,
    DPI,
    INK,
    INK2,
    JUNCTION_TICKS_HZ,
    KILO,
    SERIES,
    STYLE,
    SURFACE,
    X_LABELS,
    X_TICKS_HZ,
)

BEFORE_COLOR = "#9a9890"
FREQ_SIZE = (9.0, 4.6)
SMALL_SIZE = (8.0, 4.0)


@contextmanager
def _figure(size: tuple[float, float], out: io.BytesIO) -> Iterator[Figure]:
    """A figure in the report style, written as PNG to ``out`` on leaving the block."""
    with mpl.rc_context(STYLE):  # type: ignore[arg-type]  # rc keys are typed as a Literal union
        figure = Figure(figsize=size)
        FigureCanvasAgg(figure)
        yield figure
        figure.tight_layout()
        figure.savefig(out, dpi=DPI, format="png", metadata={"Software": None})


def _png(size: tuple[float, float], draw: Callable[[Figure], None]) -> bytes:
    out = io.BytesIO()
    with _figure(size, out) as figure:
        draw(figure)
    return out.getvalue()


def _numbers(ax: Axes, tr: Translate) -> None:
    """Y axis numbers with the language's decimal separator."""
    comma = tr("report-decimal-separator", None)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}".replace(".", comma)))


def _freq_labels(tr: Translate) -> list[str]:
    kilo = tr("report-kilo", None)
    return [label.replace("k", kilo) for label in X_LABELS]


def _freq_axis(ax: Axes, tr: Translate, ylim: tuple[float, float] = (-24, 24)) -> None:
    ax.set_xscale("log")
    ax.set_xlim(20, 20000)
    ax.set_xticks(X_TICKS_HZ)
    ax.set_xticklabels(_freq_labels(tr))
    _numbers(ax, tr)
    ax.minorticks_off()
    ax.set_ylim(*ylim)
    ax.set_xlabel(tr("report-axis-hz", None))
    ax.set_ylabel(tr("report-axis-db-normalised", None))
    ax.axvspan(APPROX_FROM_HZ, 20000, color="#f2f1ec", zorder=0)


def points_png(an: Analysis, tr: Translate) -> bytes:
    """Response of every point and their average."""

    def draw(figure: Figure) -> None:
        ax = figure.subplots()
        for k, (name, curve) in enumerate(an.norm_db.items()):
            ax.plot(an.grid_hz, curve, lw=1.1, color=SERIES[k % len(SERIES)], label=name, alpha=0.9)
        ax.plot(an.grid_hz, an.avg_db, lw=2.4, color=INK, label=tr("report-legend-average", None))
        _freq_axis(ax, tr)
        ax.legend(ncol=4, fontsize=8, loc="lower left")

    return _png(FREQ_SIZE, draw)


def average_png(
    before: Analysis, target_db: FloatArray | None, tr: Translate, after: Analysis | None = None
) -> bytes:
    """The averaged response with the spread between points and the target; after, if given."""

    def draw(figure: Figure) -> None:
        ax = figure.subplots()
        grid = before.grid_hz
        ax.fill_between(
            grid,
            before.avg_db - before.spread_db,
            before.avg_db + before.spread_db,
            color="#cde2fb",
            lw=0,
            label=tr("report-legend-spread", None),
        )
        if after is None:
            label = tr("report-legend-average", None)
            ax.plot(grid, before.avg_db, lw=2.2, color=AVERAGE_COLOR, label=label)
        else:  # dotted before, solid after: also tells them apart in black and white
            label = tr("report-legend-before", None)
            ax.plot(grid, before.avg_db, lw=2.0, ls=":", color=BEFORE_COLOR, label=label)
        if after is not None:
            ax.plot(
                after.grid_hz,
                after.avg_db,
                lw=2.2,
                color=AVERAGE_COLOR,
                label=tr("report-legend-after", None),
            )
        if target_db is not None:
            ax.plot(
                grid, target_db, lw=1.3, ls="--", color=INK2, label=tr("report-legend-target", None)
            )
        _freq_axis(ax, tr)
        ax.legend(fontsize=8, loc="lower left")

    return _png(FREQ_SIZE, draw)


def decay_png(an: Analysis, tr: Translate) -> bytes:
    """Reverberation time (T20) and early decay (EDT) per octave, averaged over the points."""
    octaves = [c for c in OCTAVES_HZ if c in an.rt60]

    def draw(figure: Figure) -> None:
        ax = figure.subplots()
        x = np.arange(len(octaves))
        t20 = [an.rt60[c].t20.mean_s or 0.0 for c in octaves]
        edt = [an.rt60[c].edt.mean_s or 0.0 for c in octaves]
        ax.bar(x - 0.2, t20, width=0.38, color=SERIES[0], label=tr("report-column-t20", None))
        ax.bar(x + 0.2, edt, width=0.38, color=SERIES[2], hatch="//", edgecolor=SURFACE,
               label=tr("report-column-edt", None))  # fmt: skip
        ax.set_xticks(x)
        kilo = tr("report-kilo", None)
        ax.set_xticklabels([f"{c // KILO}{kilo}" if c >= KILO else str(c) for c in octaves])
        _numbers(ax, tr)
        ax.set_ylabel(tr("report-axis-seconds", None))
        ax.set_xlabel(tr("report-axis-octave", None))
        ax.legend(fontsize=8)
        ax.grid(axis="x", visible=False)

    return _png(SMALL_SIZE, draw)


def _power_mean(curves: list[FloatArray]) -> FloatArray:
    return np.asarray(10 * np.log10(np.mean([10 ** (c / 10) for c in curves], axis=0)))


def junction_png(result: AlignmentResult, tr: Translate) -> bytes:
    """Sub alone, tops alone and both together around the crossover, averaged over the points."""
    points = list(result.points.values())

    def draw(figure: Figure) -> None:
        ax = figure.subplots()
        centres = result.centres_hz
        ax.plot(centres, _power_mean([p.sub_db for p in points]), lw=1.6, color=SERIES[1],
                label=tr("report-legend-sub", None))  # fmt: skip
        ax.plot(centres, _power_mean([p.tops_db for p in points]), lw=1.6, color=SERIES[0],
                label=tr("report-legend-tops", None))  # fmt: skip
        ax.plot(centres, _power_mean([p.sum_now_db for p in points]), lw=2.4, color=INK,
                label=tr("report-legend-both", None))  # fmt: skip
        ax.axvline(result.fc_hz, color=INK2, lw=0.8, ls=":")
        label = tr("report-legend-crossover", {"fc_hz": round(result.fc_hz)})
        ax.text(result.fc_hz, 0.98, f" {label}", transform=ax.get_xaxis_transform(),
                va="top", fontsize=8, color=INK2)  # fmt: skip
        ax.set_xscale("log")
        ax.set_xticks(JUNCTION_TICKS_HZ)
        ax.set_xticklabels([str(f) for f in JUNCTION_TICKS_HZ])
        ax.minorticks_off()
        ax.set_xlabel(tr("report-axis-hz", None))
        ax.set_ylabel(tr("report-axis-db", None))
        _numbers(ax, tr)
        ax.legend(fontsize=8, loc="lower left")

    return _png(SMALL_SIZE, draw)
