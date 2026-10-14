"""Which USB input carries which microphone: "lightly scratch the grille of mic 1" (SPEC S6).

The wizard records every candidate input for a few seconds while the person scratches one
microphone. The input whose short-term level jumps far above its own quiet level, and above every
other input, is that microphone. Pure: works on recorded blocks.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.domain.arrays import FloatArray

CLIP_LEVEL = 0.99


@dataclass(frozen=True, slots=True)
class MappingConfig:
    """Thresholds of the scratch detection.

    Attributes:
        frame_s: Length of one level frame.
        min_rise_db: The best frame must stand this far above the input's quiet level ...
        min_lead_db: ... and this far above the rise of the second-best input (crosstalk).
        quiet_percentile: Percentile of the frames taken as the quiet level (low, so a long
            scratch does not raise it).
        quiet_floor_dbfs: The quiet level is never taken below this (a digitally silent input
            would otherwise "rise" 100 dB on one stray sample).
        min_scratch_dbfs: The best frame must be at least this loud in absolute terms.
    """

    frame_s: float = 0.05
    min_rise_db: float = 15.0
    min_lead_db: float = 10.0
    quiet_percentile: float = 20.0
    quiet_floor_dbfs: float = -100.0
    min_scratch_dbfs: float = -50.0


# Digital silence, so an unused input never looks like a 300 dB jump.
POWER_FLOOR = 1e-20


class MappingOutcome(StrEnum):
    """Result of listening for one scratch."""

    FOUND = "found"
    NOTHING = "nothing"  # no input rose enough: phantom off, wrong input, too gentle
    AMBIGUOUS = "ambiguous"  # two inputs rose together: crosstalk or two mics touched
    ALREADY_MAPPED = "already_mapped"  # the loudest input belongs to another microphone


@dataclass(frozen=True, slots=True)
class ScratchResult:
    """What listening for one scratch found.

    Attributes:
        outcome: Found, nothing, ambiguous or already mapped.
        usb_input: The input that carried the scratch, when one stood out.
        rise_db: How far that input rose above its quiet level.
        lead_db: Its lead over the next input.
        clipped: True when the scratch clipped the input (gain too high).
    """

    outcome: MappingOutcome
    usb_input: int | None
    rise_db: float
    lead_db: float
    clipped: bool


def frame_levels_dbfs(samples: FloatArray, fs_hz: float, frame_s: float = 0.05) -> FloatArray:
    """Short-term RMS level of each input, ``float64[n_frames, k]`` from ``samples[n, k]``."""
    frame = max(1, int(frame_s * fs_hz))
    n_frames = max(1, samples.shape[0] // frame)
    trimmed = samples[: n_frames * frame].reshape(n_frames, frame, samples.shape[1])
    power = np.mean(trimmed * trimmed, axis=1)
    return np.asarray(10 * np.log10(np.maximum(power, POWER_FLOOR)), dtype=np.float64)


def find_scratch(
    samples: FloatArray,
    inputs: Sequence[int],
    fs_hz: float,
    taken: Sequence[int] = (),
    config: MappingConfig | None = None,
) -> ScratchResult:
    """Find the input that carried a scratch.

    Args:
        samples: Recording of the candidate inputs, ``float[n, k]``.
        inputs: The 1-based USB input of each column.
        fs_hz: Sample rate.
        taken: Inputs already mapped to other microphones.
        config: Thresholds.

    Raises:
        ValueError: The column count does not match ``inputs``.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != len(inputs):  # noqa: PLR2004
        raise ValueError(f"expected {len(inputs)} columns, got shape {data.shape}")
    cfg = config or MappingConfig()
    levels = frame_levels_dbfs(data, fs_hz, cfg.frame_s)
    loudest = np.max(levels, axis=0)
    quiet = np.maximum(np.percentile(levels, cfg.quiet_percentile, axis=0), cfg.quiet_floor_dbfs)
    rise = np.where(loudest >= cfg.min_scratch_dbfs, loudest - quiet, 0.0)
    order = np.argsort(rise)[::-1]
    best = int(order[0])
    second = float(rise[order[1]]) if len(order) > 1 else 0.0
    lead = float(rise[best]) - second
    clipped = bool(np.max(np.abs(data[:, best])) >= CLIP_LEVEL)
    usb_input = inputs[best]
    if rise[best] < cfg.min_rise_db:
        return ScratchResult(MappingOutcome.NOTHING, None, float(rise[best]), lead, False)
    if lead < cfg.min_lead_db:
        return ScratchResult(MappingOutcome.AMBIGUOUS, None, float(rise[best]), lead, clipped)
    outcome = MappingOutcome.ALREADY_MAPPED if usb_input in taken else MappingOutcome.FOUND
    return ScratchResult(outcome, usb_input, float(rise[best]), lead, clipped)
