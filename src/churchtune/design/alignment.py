"""Sub / top alignment from three impulse responses per point: sub only, tops only, both.

The three responses of a point come from one stream and share a time base, so the relative
timing of sub and tops is exact. The search looks for the sub delay (positive = delay the sub,
negative = delay the tops) and polarity that maximise the summed response around the crossover:
mean level minus half the ripple, averaged over the points. Only the direct sound and the first
reflections are compared (a 60 ms window), as in a phase alignment.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from churchtune.domain.arrays import FloatArray

ALIGN_WINDOW_S = 0.060
WINDOW_PRE_MS = 10.0
WINDOW_FADE_FRACTION = 0.5
FIRST_ARRIVAL_REL_DB = -20.0
NFFT = 1 << 17
SMOOTHING_FRACTION = 6
# The junction is evaluated from fc / 2 to 2 fc in 1/6-octave steps.
ZONE_STEPS_EACH_SIDE = 6
ZONE_STEPS_PER_OCTAVE = 6
BAND_MARGIN = 1.2
DEFAULT_MAX_DELAY_MS = 15.0
DEFAULT_STEP_MS = 0.1
RIPPLE_WEIGHT = 0.5
# Among near-equal scores, prefer a small delay and normal polarity.
DELAY_PENALTY_DB_PER_MS = 0.02
INVERT_PENALTY_DB = 0.3
DEFAULT_LEVEL_THRESHOLD_DB = 1.0
DEFAULT_LEVEL_CAP_DB = 6.0
NORMAL, INVERTED = 1, -1
_POWER_FLOOR = 1e-30


@dataclass(frozen=True, slots=True)
class JunctionPoint:
    """Impulse responses of one point on a common time base.

    Attributes:
        sub: Sub only, ``float64[n]``.
        tops: Tops only.
        both: Everything as it normally plays.
    """

    sub: FloatArray
    tops: FloatArray
    both: FloatArray


@dataclass(frozen=True, slots=True)
class PointAlignment:
    """Junction levels of one point, in dB relative to the louder of sub and tops.

    All curves are on ``AlignmentResult.centres_hz``.

    Attributes:
        sub_db: Sub alone.
        tops_db: Tops alone.
        all_measured_db: The measured "both" block.
        sum_now_db: Sum computed from the sub and tops blocks as they are now.
        sum_best_db: Sum predicted with the best delay and polarity.
        check_mismatch_db: RMS difference between the measured and the computed current sum;
            a large value means the mutes did not switch or something changed while measuring.
        min_now_db: Deepest point of the current sum in the zone.
        min_best_db: Deepest point of the predicted sum in the zone.
    """

    sub_db: FloatArray
    tops_db: FloatArray
    all_measured_db: FloatArray
    sum_now_db: FloatArray
    sum_best_db: FloatArray
    check_mismatch_db: float
    min_now_db: float
    min_best_db: float


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    """Result of the alignment search.

    Attributes:
        fc_hz: Crossover frequency.
        centres_hz: Evaluation frequencies, fc/2..2fc.
        delay_ms: Best sub delay; positive delays the sub, negative delays the tops.
        polarity: Best sub polarity, +1 or -1.
        score_now_db: Score of the current state (no delay, normal polarity).
        score_best_db: Score of the best delay and polarity.
        delays_ms: The delays that were tried.
        score_normal_db: Score per delay with normal polarity.
        score_inverted_db: Score per delay with inverted polarity.
        points: Levels per point.
        sub_minus_tops_at_fc_db: Mean level difference at fc (should be about 0 with LR filters).
    """

    fc_hz: float
    centres_hz: FloatArray
    delay_ms: float
    polarity: int
    score_now_db: float
    score_best_db: float
    delays_ms: FloatArray
    score_normal_db: FloatArray
    score_inverted_db: FloatArray
    points: Mapping[str, PointAlignment]
    sub_minus_tops_at_fc_db: float

    @property
    def gain_db(self) -> float:
        """How much the best delay and polarity improve the score."""
        return self.score_best_db - self.score_now_db


def _first_arrival(ir: FloatArray, rel_db: float = FIRST_ARRIVAL_REL_DB) -> int:
    """Index of the first sample within ``rel_db`` of the peak (onset of the direct sound)."""
    magnitude = np.abs(ir[: len(ir) // 2])
    threshold = magnitude.max() * 10 ** (rel_db / 20)
    return int(np.argmax(magnitude >= threshold))


def _spectra(irs: Sequence[FloatArray], fs_hz: float) -> tuple[FloatArray, list[np.ndarray]]:
    """Complex spectra of all responses over one common, short time window.

    The window starts just before the earlier first arrival of sub or tops.
    """
    onset = min(_first_arrival(ir) for ir in irs[:2])
    start = max(0, onset - int(WINDOW_PRE_MS * 1e-3 * fs_hz))
    n = int(ALIGN_WINDOW_S * fs_hz)
    window = np.ones(n)
    fade = int(WINDOW_FADE_FRACTION * n)
    window[n - fade :] = np.hanning(2 * fade)[fade:]
    freqs = np.fft.rfftfreq(NFFT, 1 / fs_hz)
    spectra = []
    for ir in irs:
        segment = ir[start : start + n]
        padded = np.pad(segment, (0, max(0, n - len(segment))))
        spectra.append(np.fft.rfft(padded * window, NFFT))
    return freqs, spectra


def _smooth_db(freqs: FloatArray, power: FloatArray, centres: FloatArray) -> FloatArray:
    """Mean power in a 1/6-octave band around each centre, in dB."""
    half_band = 2 ** (1 / (2 * SMOOTHING_FRACTION))
    levels = []
    for centre in centres:
        in_band = (freqs >= centre / half_band) & (freqs <= centre * half_band)
        levels.append(10 * np.log10(max(float(np.mean(power[in_band])), _POWER_FLOOR)))
    return np.array(levels)


@dataclass(frozen=True, slots=True)
class _PointSpectra:
    freqs: FloatArray
    sub: np.ndarray
    tops: np.ndarray
    ref_db: float
    sub_db: FloatArray
    tops_db: FloatArray
    all_db: FloatArray
    now_db: FloatArray


def _summed_db(p: _PointSpectra, centres: FloatArray, delay_ms: float, polarity: int) -> FloatArray:
    shifted = polarity * p.sub * np.exp(-2j * np.pi * p.freqs * delay_ms * 1e-3)
    return _smooth_db(p.freqs, np.abs(p.tops + shifted) ** 2, centres) - p.ref_db


def _point_spectra(point: JunctionPoint, fs_hz: float, centres: FloatArray) -> _PointSpectra:
    freqs, spectra = _spectra((point.sub, point.tops, point.both), fs_hz)
    band = (freqs >= centres[0] / BAND_MARGIN) & (freqs <= centres[-1] * BAND_MARGIN)
    f, sub, tops, both = freqs[band], spectra[0][band], spectra[1][band], spectra[2][band]
    sub_db = _smooth_db(f, np.abs(sub) ** 2, centres)
    tops_db = _smooth_db(f, np.abs(tops) ** 2, centres)
    ref_db = max(float(sub_db.max()), float(tops_db.max()))
    return _PointSpectra(
        freqs=f,
        sub=sub,
        tops=tops,
        ref_db=ref_db,
        sub_db=sub_db - ref_db,
        tops_db=tops_db - ref_db,
        all_db=_smooth_db(f, np.abs(both) ** 2, centres) - ref_db,
        now_db=_smooth_db(f, np.abs(tops + sub) ** 2, centres) - ref_db,
    )


def analyze_alignment(
    points: Mapping[str, JunctionPoint],
    fc_hz: float,
    fs_hz: float,
    *,
    max_delay_ms: float = DEFAULT_MAX_DELAY_MS,
    step_ms: float = DEFAULT_STEP_MS,
) -> AlignmentResult:
    """Search the sub delay and polarity that give the best sum around the crossover.

    Args:
        points: Sub / tops / both impulse responses per point.
        fc_hz: Crossover frequency.
        fs_hz: Sample rate of the responses.
        max_delay_ms: Search range, both ways.
        step_ms: Search step.

    Returns:
        The best delay and polarity with their scores, and the junction levels per point.

    Raises:
        ValueError: ``points`` is empty.
    """
    if not points:
        raise ValueError("there are no junction points to analyse")
    centres = fc_hz * 2 ** (
        np.arange(-ZONE_STEPS_EACH_SIDE, ZONE_STEPS_EACH_SIDE + 1) / ZONE_STEPS_PER_OCTAVE
    )
    delays = np.arange(-max_delay_ms, max_delay_ms + 1e-9, step_ms)
    polarities = (NORMAL, INVERTED)
    spectra = {name: _point_spectra(point, fs_hz, centres) for name, point in points.items()}
    score = np.zeros((len(polarities), len(delays)))
    for p in spectra.values():
        for pi, polarity in enumerate(polarities):
            for di, delay in enumerate(delays):
                summed = _summed_db(p, centres, float(delay), polarity)
                score[pi, di] += summed.mean() - RIPPLE_WEIGHT * summed.std()
    score /= len(spectra)
    preferred = (
        score
        - DELAY_PENALTY_DB_PER_MS * np.abs(delays)[None, :]
        - np.array([[0.0], [INVERT_PENALTY_DB]])
    )
    pi, di = (int(i) for i in np.unravel_index(int(np.argmax(preferred)), preferred.shape))
    best_delay, best_polarity = float(delays[di]), polarities[pi]
    i_fc = int(np.argmin(np.abs(centres - fc_hz)))
    per_point: dict[str, PointAlignment] = {}
    level_diffs = []
    for name, p in spectra.items():
        best_db = _summed_db(p, centres, best_delay, best_polarity)
        level_diffs.append(p.sub_db[i_fc] - p.tops_db[i_fc])
        per_point[name] = PointAlignment(
            sub_db=p.sub_db,
            tops_db=p.tops_db,
            all_measured_db=p.all_db,
            sum_now_db=p.now_db,
            sum_best_db=best_db,
            check_mismatch_db=round(float(np.sqrt(np.mean((p.all_db - p.now_db) ** 2))), 2),
            min_now_db=round(float(p.now_db.min()), 1),
            min_best_db=round(float(best_db.min()), 1),
        )
    return AlignmentResult(
        fc_hz=fc_hz,
        centres_hz=centres,
        delay_ms=round(best_delay, 1),
        polarity=best_polarity,
        score_now_db=float(score[0, int(np.argmin(np.abs(delays)))]),
        score_best_db=float(score[pi, di]),
        delays_ms=delays,
        score_normal_db=score[0],
        score_inverted_db=score[1],
        points=per_point,
        sub_minus_tops_at_fc_db=round(float(np.mean(level_diffs)), 1),
    )


def recommend_level(
    result: AlignmentResult,
    threshold_db: float = DEFAULT_LEVEL_THRESHOLD_DB,
    cap_db: float = DEFAULT_LEVEL_CAP_DB,
) -> float:
    """Sub fader change that brings sub and tops level at fc, capped; 0 when close enough.

    With Linkwitz-Riley filters sub and tops should be equal at the crossover frequency.
    """
    diff = result.sub_minus_tops_at_fc_db
    if abs(diff) < threshold_db:
        return 0.0
    return float(np.clip(-diff, -cap_db, cap_db))
