"""PNG charts of a session, a proposal, a verify comparison and a sub / top alignment.

Figures are built with matplotlib's object API on the Agg canvas, inside a local style
context: no global matplotlib state is touched.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import matplotlib as mpl
import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.points import Analysis
from churchtune.design.alignment import AlignmentResult
from churchtune.design.proposal import Proposal
from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq_model import eq_response_db

SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#7a5cc2", "#9a6b3f")
INK, INK2, GRID_COLOR, SURFACE = "#1f1f1e", "#6b6a63", "#e4e3dc", "#ffffff"
AVERAGE_COLOR = "#1c5cab"
X_TICKS_HZ = (31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
X_LABELS = ("31", "63", "125", "250", "500", "1k", "2k", "4k", "8k", "16k")
JUNCTION_TICKS_HZ = (50, 63, 80, 100, 125, 160, 200)
APPROX_FROM_HZ = 10000.0
DPI = 130
ACTIVE_GAIN_DB = 0.25
KILO = 1000
STYLE = {
    "font.size": 10,
    "axes.edgecolor": INK2,
    "axes.labelcolor": INK,
    "xtick.color": INK2,
    "ytick.color": INK2,
    "axes.grid": True,
    "grid.color": GRID_COLOR,
    "grid.linewidth": 0.8,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "legend.frameon": False,
}


@contextmanager
def _figure(path: Path, size: tuple[float, float]) -> Iterator[Figure]:
    """A figure in the report style that is saved to ``path`` on leaving the block."""
    with mpl.rc_context(STYLE):  # type: ignore[arg-type]  # rc keys are typed as a Literal union
        figure = Figure(figsize=size)
        FigureCanvasAgg(figure)
        yield figure
        figure.tight_layout()
        path.parent.mkdir(parents=True, exist_ok=True)
        figure.savefig(path, dpi=DPI)


def _freq_axis(ax: Axes, ylim: tuple[float, float] = (-24, 24)) -> None:
    ax.set_xscale("log")
    ax.set_xlim(20, 20000)
    ax.set_xticks(X_TICKS_HZ)
    ax.set_xticklabels(X_LABELS)
    ax.minorticks_off()
    ax.set_ylim(*ylim)
    ax.set_xlabel("Гц")
    ax.set_ylabel("дБ (500–2000 Гц = 0)")
    ax.axvspan(APPROX_FROM_HZ, 20000, color="#f2f1ec", zorder=0)
    ax.text(14000, ylim[1] - 2, "микрофон\nориент.", ha="center", va="top", fontsize=8, color=INK2)


def plot_points(an: Analysis, path: Path) -> None:
    """Response of every point and their average."""
    with _figure(path, (10, 5.2)) as figure:
        ax = figure.subplots()
        for k, (name, curve) in enumerate(an.norm_db.items()):
            ax.plot(an.grid_hz, curve, lw=1.2, color=SERIES[k % len(SERIES)], label=name, alpha=0.9)
        ax.plot(an.grid_hz, an.avg_db, lw=2.4, color=INK, label="среднее (энерг.)")
        _freq_axis(ax)
        ax.set_title("АЧХ по точкам, сглаживание 1/6 окт", loc="left", color=INK)
        ax.legend(ncol=4, fontsize=8, loc="lower left")


def plot_average(an: Analysis, path: Path, target_db: FloatArray | None = None) -> None:
    """Averaged response with the spread between points, and the target."""
    with _figure(path, (10, 5.2)) as figure:
        ax = figure.subplots()
        grid = an.grid_hz
        ax.fill_between(
            grid, an.min_db, an.max_db, color="#cde2fb", lw=0, label="мин–макс по точкам"
        )
        ax.fill_between(
            grid,
            an.avg_db - an.spread_db,
            an.avg_db + an.spread_db,
            color="#86b6ef",
            lw=0,
            label="±σ",
        )
        ax.plot(grid, an.avg_db, lw=2.2, color=AVERAGE_COLOR, label="среднее")
        if target_db is not None:
            ax.plot(grid, target_db, lw=1.4, ls="--", color=INK2, label="цель")
        ax.axvline(an.f_low_hz, color=INK2, lw=0.8, ls=":")
        _freq_axis(ax)
        ax.set_title("Усреднённая АЧХ и разброс между точками", loc="left", color=INK)
        ax.legend(fontsize=8, loc="lower left")


def plot_rt60(an: Analysis, path: Path) -> None:
    """Reverberation time per octave band, averaged over the points."""
    getters: tuple[tuple[str, Callable[[int], float | None]], ...] = (
        ("T20", lambda c: an.rt60[c].t20.mean_s),
        ("T30", lambda c: an.rt60[c].t30.mean_s),
        ("EDT", lambda c: an.rt60[c].edt.mean_s),
    )
    with _figure(path, (8, 4.2)) as figure:
        ax = figure.subplots()
        x = np.arange(len(OCTAVES_HZ))
        for k, (label, getter) in enumerate(getters):
            values = [getter(c) or 0 for c in OCTAVES_HZ]
            ax.bar(x + (k - 1) * 0.27, values, width=0.25, color=SERIES[k], label=label)
        ax.set_xticks(x)
        ax.set_xticklabels([f"{c // KILO}k" if c >= KILO else str(c) for c in OCTAVES_HZ])
        ax.set_ylabel("с")
        ax.set_xlabel("октава, Гц")
        ax.set_title("Время реверберации по октавам (среднее по точкам)", loc="left", color=INK)
        ax.legend(fontsize=8)
        ax.grid(axis="x", visible=False)


def plot_proposal(
    an: Analysis, proposal: Proposal, path: Path, target_db: FloatArray, fs_hz: float
) -> None:
    """Measured average, predicted result and the two EQ curves."""
    grid = an.grid_hz
    new_eq = proposal.to_eq()
    old_response = eq_response_db(proposal.eq_at_measurement, grid, fs_hz)
    new_response = eq_response_db(new_eq, grid, fs_hz)
    with _figure(path, (10, 7.5)) as figure:
        top, bottom = figure.subplots(2, 1, sharex=True, gridspec_kw={"height_ratios": [3, 2]})
        top.plot(grid, an.avg_db, lw=1.8, color=INK2, label="замер (среднее)")
        predicted = an.avg_db + new_response - old_response
        top.plot(grid, predicted, lw=2.2, color=AVERAGE_COLOR, label="прогноз после EQ")
        top.plot(grid, target_db, lw=1.2, ls="--", color=INK, label="цель")
        _freq_axis(top)
        top.set_xlabel("")
        top.set_title("Предложение EQ Main LR: прогноз", loc="left", color=INK)
        top.legend(fontsize=8, loc="lower left")
        bottom.plot(grid, old_response, lw=1.4, color=INK2, label="EQ во время замера")
        bottom.plot(grid, new_response, lw=2.2, color=SERIES[1], label="предложенный EQ")
        for band in new_eq.bands:
            if band.adjustable and abs(band.gain_db) >= ACTIVE_GAIN_DB:
                bottom.annotate(
                    f"{band.band}",
                    (band.freq_hz, band.gain_db),
                    textcoords="offset points",
                    xytext=(0, 6 if band.gain_db > 0 else -12),
                    ha="center",
                    fontsize=8,
                    color=INK,
                )
        _freq_axis(bottom, ylim=(-14, 6))
        bottom.set_ylabel("дБ (модель)")
        bottom.legend(fontsize=8, loc="lower right")


def plot_verify(
    before: Analysis, after: Analysis, path: Path, target_db: FloatArray | None = None
) -> None:
    """Averaged response before and after, over the same points."""
    with _figure(path, (10, 5.2)) as figure:
        ax = figure.subplots()
        ax.plot(before.grid_hz, before.avg_db, lw=1.8, color=INK2, label="до")
        ax.plot(before.grid_hz, after.avg_db, lw=2.2, color=AVERAGE_COLOR, label="после")
        if target_db is not None:
            ax.plot(before.grid_hz, target_db, lw=1.2, ls="--", color=INK, label="цель")
        _freq_axis(ax)
        ax.set_title("Сравнение до / после (среднее по общим точкам)", loc="left", color=INK)
        ax.legend(fontsize=8, loc="lower left")


def _power_mean(curves: list[FloatArray]) -> FloatArray:
    return np.asarray(10 * np.log10(np.mean([10 ** (c / 10) for c in curves], axis=0)))


def plot_alignment(result: AlignmentResult, path: Path) -> None:
    """The sub / top junction now and predicted, and the score over the delay search."""
    points = list(result.points.values())
    centres = result.centres_hz
    with _figure(path, (12, 4.6)) as figure:
        left, right = figure.subplots(1, 2)
        curves = (
            ("только саб", [p.sub_db for p in points], SERIES[1], 1.6),
            ("только сателлиты", [p.tops_db for p in points], SERIES[0], 1.6),
            ("вместе сейчас (замер)", [p.all_measured_db for p in points], INK2, 2.0),
            (
                "вместе после выравнивания (прогноз)",
                [p.sum_best_db for p in points],
                SERIES[2],
                2.4,
            ),
        )
        for label, per_point, color, width in curves:
            left.plot(centres, _power_mean(per_point), color=color, lw=width, label=label)
        left.set_xscale("log")
        left.set_xticks(JUNCTION_TICKS_HZ)
        left.set_xticklabels([str(t) for t in JUNCTION_TICKS_HZ])
        left.minorticks_off()
        left.axvline(result.fc_hz, color=INK2, lw=0.8, ls=":")
        left.set_xlabel("Гц")
        left.set_ylabel("дБ")
        left.set_title("Стык саб / сателлиты", loc="left", color=INK)
        left.legend(fontsize=8, loc="lower center")
        right.plot(
            result.delays_ms,
            result.score_normal_db,
            color=SERIES[0],
            lw=1.6,
            label="полярность норм.",
        )
        right.plot(
            result.delays_ms,
            result.score_inverted_db,
            color=SERIES[1],
            lw=1.6,
            label="полярность инверт.",
        )
        right.axvline(result.delay_ms, color=INK, lw=0.8, ls=":")
        right.set_xlabel("задержка саба, мс (<0 = задержать сателлиты)")
        right.set_ylabel("оценка суммы, дБ")
        polarity = "норм." if result.polarity == 1 else "ИНВЕРСИЯ"
        right.set_title(f"Лучшее: {result.delay_ms:+.1f} мс, {polarity}", loc="left", color=INK)
        right.legend(fontsize=8)
