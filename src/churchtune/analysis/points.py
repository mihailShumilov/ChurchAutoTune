"""Analysis of a set of measured points: per-point and averaged response, spread, RT60."""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from churchtune.analysis.bands import OCTAVES_HZ
from churchtune.analysis.response import (
    GRID_HZ,
    NORM_BAND_HZ,
    band_mean_db,
    energy_average,
    low_cutoff_hz,
    magnitude_db,
)
from churchtune.analysis.rt60 import BandDecay, rt60_octaves
from churchtune.domain.arrays import FloatArray


@dataclass(frozen=True, slots=True)
class PointResponse:
    """One measured point as input to the analysis.

    Attributes:
        ir: Impulse response, ``float64[n]``.
        fs_hz: Its sample rate.
    """

    ir: FloatArray
    fs_hz: float


@dataclass(frozen=True, slots=True)
class DecayStat:
    """One decay time averaged over the points where it could be measured.

    Attributes:
        mean_s: Mean over those points, or None when no point had it.
        n_points: Number of points that contributed.
        sd_s: Standard deviation, or None with fewer than two points.
    """

    mean_s: float | None
    n_points: int
    sd_s: float | None


@dataclass(frozen=True, slots=True)
class RoomDecay:
    """Averaged decay times of one octave band."""

    edt: DecayStat
    t20: DecayStat
    t30: DecayStat


@dataclass(frozen=True, slots=True)
class Analysis:
    """Result of analysing a session.

    All curves are on ``grid_hz`` (``float64[n_freqs]``).

    Attributes:
        grid_hz: The analysis grid.
        raw_db: Per-point response as measured (minus the mic calibration, if any).
        norm_db: Per-point response normalised to 0 dB in 500..2000 Hz.
        avg_db: Power average of the normalised responses.
        spread_db: Standard deviation between points at each frequency.
        min_db: Lowest normalised response at each frequency.
        max_db: Highest normalised response at each frequency.
        rt60_points: Decay times per point and octave band.
        rt60: Decay times per octave band, averaged over points.
        f_low_hz: Below this the system is not corrected (speaker roll-off).
    """

    grid_hz: FloatArray
    raw_db: Mapping[str, FloatArray]
    norm_db: Mapping[str, FloatArray]
    avg_db: FloatArray
    spread_db: FloatArray
    min_db: FloatArray
    max_db: FloatArray
    rt60_points: Mapping[str, Mapping[int, BandDecay]]
    rt60: Mapping[int, RoomDecay]
    f_low_hz: float

    @property
    def point_names(self) -> tuple[str, ...]:
        """Names of the analysed points, in input order."""
        return tuple(self.norm_db)


def _stat(values: list[float]) -> DecayStat:
    return DecayStat(
        mean_s=float(np.mean(values)) if values else None,
        n_points=len(values),
        sd_s=float(np.std(values)) if len(values) > 1 else None,
    )


def analyze_points(
    points: Mapping[str, PointResponse],
    mic_cal_db: FloatArray | None = None,
    point_cal_db: Mapping[str, FloatArray] | None = None,
) -> Analysis:
    """Analyse the impulse responses of a session.

    Args:
        points: Measured points by name.
        mic_cal_db: Microphone response on the analysis grid, subtracted from every point.
        point_cal_db: Per point, the response of the microphone that measured it (mic-set
            calibration), subtracted as well.

    Returns:
        The analysis.

    Raises:
        ValueError: ``points`` is empty.
    """
    if not points:
        raise ValueError("there are no measured points to analyse")
    raw: dict[str, FloatArray] = {}
    norm: dict[str, FloatArray] = {}
    decays: dict[str, dict[int, BandDecay]] = {}
    for name, point in points.items():
        curve = magnitude_db(point.ir, point.fs_hz)
        if mic_cal_db is not None:
            curve = curve - mic_cal_db
        if point_cal_db is not None and name in point_cal_db:
            curve = curve - point_cal_db[name]
        raw[name] = curve
        norm[name] = curve - band_mean_db(curve, *NORM_BAND_HZ)
        decays[name] = rt60_octaves(point.ir, point.fs_hz)
    curves = np.array(list(norm.values()))
    avg = energy_average(curves)
    room: dict[int, RoomDecay] = {}
    for centre in OCTAVES_HZ:
        band = [decays[name][centre] for name in decays]
        room[centre] = RoomDecay(
            edt=_stat([d.edt_s for d in band if d.edt_s is not None]),
            t20=_stat([d.t20_s for d in band if d.t20_s is not None]),
            t30=_stat([d.t30_s for d in band if d.t30_s is not None]),
        )
    return Analysis(
        grid_hz=GRID_HZ,
        raw_db=raw,
        norm_db=norm,
        avg_db=avg,
        spread_db=np.asarray(curves.std(axis=0), dtype=np.float64),
        min_db=np.asarray(curves.min(axis=0), dtype=np.float64),
        max_db=np.asarray(curves.max(axis=0), dtype=np.float64),
        rt60_points=decays,
        rt60=room,
        f_low_hz=low_cutoff_hz(avg),
    )
