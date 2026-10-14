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

FRAME_S = 0.05
# A scratch is loud and short: its best frame must stand this far above the input's own median.
MIN_RISE_DB = 15.0
# ... and this far above the rise of the second-best input (crosstalk, a bumped table).
MIN_LEAD_DB = 10.0
QUIET_PERCENTILE = 50
CLIP_LEVEL = 0.99
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


def frame_levels_dbfs(samples: FloatArray, fs_hz: float) -> FloatArray:
    """Short-term RMS level of each input, ``float64[n_frames, k]`` from ``samples[n, k]``."""
    frame = max(1, int(FRAME_S * fs_hz))
    n_frames = max(1, samples.shape[0] // frame)
    trimmed = samples[: n_frames * frame].reshape(n_frames, frame, samples.shape[1])
    power = np.mean(trimmed * trimmed, axis=1)
    return np.asarray(10 * np.log10(np.maximum(power, POWER_FLOOR)), dtype=np.float64)


def find_scratch(
    samples: FloatArray,
    inputs: Sequence[int],
    fs_hz: float,
    taken: Sequence[int] = (),
) -> ScratchResult:
    """Find the input that carried a scratch.

    Args:
        samples: Recording of the candidate inputs, ``float[n, k]``.
        inputs: The 1-based USB input of each column.
        fs_hz: Sample rate.
        taken: Inputs already mapped to other microphones.

    Raises:
        ValueError: The column count does not match ``inputs``.
    """
    data = np.asarray(samples, dtype=np.float64)
    if data.ndim != 2 or data.shape[1] != len(inputs):  # noqa: PLR2004
        raise ValueError(f"expected {len(inputs)} columns, got shape {data.shape}")
    levels = frame_levels_dbfs(data, fs_hz)
    rise = np.max(levels, axis=0) - np.percentile(levels, QUIET_PERCENTILE, axis=0)
    order = np.argsort(rise)[::-1]
    best = int(order[0])
    second = float(rise[order[1]]) if len(order) > 1 else 0.0
    lead = float(rise[best]) - second
    clipped = bool(np.max(np.abs(data[:, best])) >= CLIP_LEVEL)
    usb_input = inputs[best]
    if rise[best] < MIN_RISE_DB:
        return ScratchResult(MappingOutcome.NOTHING, None, float(rise[best]), lead, False)
    if lead < MIN_LEAD_DB:
        return ScratchResult(MappingOutcome.AMBIGUOUS, None, float(rise[best]), lead, clipped)
    outcome = MappingOutcome.ALREADY_MAPPED if usb_input in taken else MappingOutcome.FOUND
    return ScratchResult(outcome, usb_input, float(rise[best]), lead, clipped)
