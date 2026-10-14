"""Quality gates of one measured point: numbers, a verdict and coded warnings.

Warnings are codes with parameters, not sentences; the UI turns them into advice.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import db_to_lin, peak_dbfs, rms_dbfs
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import ir_noise_dbfs

MAX_REPORTED_FLAGS = 3


class Verdict(StrEnum):
    """Grade of a measured point, ordered ok < warn < bad."""

    OK = "ok"
    WARN = "warn"
    BAD = "bad"


_SEVERITY = {Verdict.OK: 0, Verdict.WARN: 1, Verdict.BAD: 2}


def worse(a: Verdict, b: Verdict) -> Verdict:
    """Return the more severe of two verdicts."""
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


class WarningCode(StrEnum):
    """Why a point is not clean."""

    CLIPPING = "clipping"  # recorded samples at the clip threshold: lower the level or the gain
    HOT = "hot"  # peak close to clipping
    QUIET = "quiet"  # peak very low: raise the gain, check phantom power
    LOW_SNR = "low-snr"  # room too noisy for the sweep level
    LOW_PNR = "low-pnr"  # impulse response too noisy for T30 (or at all)
    XRUN = "xrun"  # the audio stream reported dropouts: repeat the measurement
    UNSTEADY = "unsteady"  # two sweeps differ: something moved or made noise; repeat


@dataclass(frozen=True, slots=True)
class QualityWarning:
    """One coded warning.

    Attributes:
        code: What is wrong.
        params: Numbers for the message, named with units (``peak_dbfs``, ``snr_db``, ...).
    """

    code: WarningCode
    params: Mapping[str, float | int | str | bool]


@dataclass(frozen=True, slots=True)
class PointQuality:
    """Quality numbers of one measured point.

    Attributes:
        peak_dbfs: Highest recorded peak over all repeats.
        clipped_samples: Samples at or above the clip threshold.
        noise_dbfs: RMS of the pre-roll (room noise).
        sweep_rms_dbfs: RMS of the recording while the sweep arrived.
        snr_db: ``sweep_rms_dbfs - noise_dbfs``.
        pnr_db: Impulse-response peak over its noise floor.
        latency_ms: Position of the impulse-response peak (system latency plus acoustic path).
        verdict: Overall grade.
        warnings: What is wrong, most severe causes included.
    """

    peak_dbfs: float
    clipped_samples: int
    noise_dbfs: float
    sweep_rms_dbfs: float
    snr_db: float
    pnr_db: float
    latency_ms: float
    verdict: Verdict
    warnings: tuple[QualityWarning, ...]


def assess(  # noqa: PLR0913 - the measurement hands over all its raw material
    rec: FloatArray,
    rec_abs_max: FloatArray,
    ir_full: FloatArray,
    peak_idx: int,
    *,
    fs_hz: float,
    sweep: SweepConfig,
    flags: Sequence[str],
    gates: QualityGates,
) -> PointQuality:
    """Grade one measured point.

    Args:
        rec: The (averaged) recording, ``float64[n]``.
        rec_abs_max: Sample-wise maximum of ``|recording|`` over all repeats, ``float64[n]``.
        ir_full: Circular impulse response from the deconvolution.
        peak_idx: Index of the impulse-response peak; with the reference starting at sample 0
            it equals the latency in samples.
        fs_hz: Sample rate.
        sweep: The sweep that was played.
        flags: Stream status flags reported during recording (xruns).
        gates: Thresholds.

    Returns:
        Numbers rounded as they are stored, the verdict and the warnings.
    """
    n_pre = int(sweep.preroll_s * fs_hz)
    sweep_n = int(sweep.duration_s * fs_hz)
    peak = peak_dbfs(rec_abs_max)
    clipped = int(np.sum(rec_abs_max >= db_to_lin(gates.clip_dbfs)))
    noise = rms_dbfs(rec[: max(1, n_pre - int(gates.noise_guard_s * fs_hz))])
    signal = rms_dbfs(rec[n_pre + peak_idx : n_pre + peak_idx + sweep_n])
    snr = signal - noise
    pnr = peak_dbfs(ir_full[peak_idx : peak_idx + 1]) - ir_noise_dbfs(ir_full, peak_idx, fs_hz)

    warnings: list[QualityWarning] = []
    verdict = Verdict.OK
    if clipped:
        warnings.append(
            QualityWarning(
                WarningCode.CLIPPING, {"count": clipped, "threshold_dbfs": gates.clip_dbfs}
            )
        )
        verdict = Verdict.BAD
    elif peak > gates.hot_dbfs:
        warnings.append(QualityWarning(WarningCode.HOT, {"peak_dbfs": round(peak, 1)}))
        verdict = Verdict.WARN
    if peak < gates.quiet_dbfs:
        warnings.append(QualityWarning(WarningCode.QUIET, {"peak_dbfs": round(peak, 1)}))
        too_quiet = peak < gates.quiet_dbfs - gates.bad_quiet_margin_db
        verdict = Verdict.BAD if too_quiet else worse(verdict, Verdict.WARN)
    if snr < gates.min_snr_db:
        warnings.append(
            QualityWarning(
                WarningCode.LOW_SNR, {"snr_db": round(snr, 1), "noise_dbfs": round(noise, 1)}
            )
        )
        verdict = worse(verdict, Verdict.WARN if snr >= gates.bad_snr_db else Verdict.BAD)
    if pnr < gates.min_pnr_db:
        unreliable = pnr < gates.bad_pnr_db
        warnings.append(
            QualityWarning(
                WarningCode.LOW_PNR,
                {
                    "pnr_db": round(pnr, 1),
                    "needed_db": gates.min_pnr_db,
                    "unreliable": unreliable,
                },
            )
        )
        verdict = worse(verdict, Verdict.BAD if unreliable else Verdict.WARN)
    if flags:
        shown = ", ".join(sorted(set(flags))[:MAX_REPORTED_FLAGS])
        warnings.append(QualityWarning(WarningCode.XRUN, {"flags": shown}))
        # SPEC §8.2: any dropout makes the point bad (the original tool only warned).
        verdict = worse(verdict, Verdict.BAD)
    return PointQuality(
        peak_dbfs=round(peak, 2),
        clipped_samples=clipped,
        noise_dbfs=round(noise, 2),
        sweep_rms_dbfs=round(signal, 2),
        snr_db=round(snr, 1),
        pnr_db=round(pnr, 1),
        latency_ms=round(1000 * peak_idx / fs_hz, 2),
        verdict=verdict,
        warnings=tuple(warnings),
    )
