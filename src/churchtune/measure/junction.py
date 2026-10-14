"""The sub / top junction measurement: three identical sweep blocks in one stream.

Block A plays the sub only, block B the tops only, block C everything as it normally plays;
the mutes are switched in the silent gap between blocks. One stream means one latency, so the
relative timing of sub and tops is exact.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import numpy.typing as npt

from churchtune.analysis.response import third_octave_centres
from churchtune.domain.arrays import FloatArray
from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.levels import peak_dbfs
from churchtune.domain.recording import StopFlag
from churchtune.measure.backend import MeasureBackend
from churchtune.measure.config import SweepConfig
from churchtune.measure.deconvolution import deconvolve
from churchtune.measure.stimulus import build_stimulus, make_sweep

BLOCKS = 3


@dataclass(frozen=True, slots=True)
class BlockStimulus:
    """A stimulus made of identical blocks.

    Attributes:
        out: The whole buffer, ``float32[n_blocks * block_len, 2]``.
        block_len: Samples per block.
        ref: One block of the reference signal, ``float64[block_len]``.
    """

    out: npt.NDArray[np.float32]
    block_len: int
    ref: FloatArray


@dataclass(frozen=True, slots=True)
class MutePlan:
    """Matrix switch states (True = on) for the junction measurement.

    Attributes:
        original: States before the measurement, to be restored afterwards.
        sub_only: States for block A.
        tops_only: States for block B.
    """

    original: Mapping[int, bool]
    sub_only: Mapping[int, bool]
    tops_only: Mapping[int, bool]


def block_stimulus(
    fs_hz: float, sweep: SweepConfig, level_dbfs: float, n_blocks: int = BLOCKS
) -> BlockStimulus:
    """Build ``n_blocks`` identical sweep blocks (pre-roll, sweep, tail) in one buffer.

    Raises:
        LevelError: The level is above the hard output limit.
    """
    block = build_stimulus(
        make_sweep(fs_hz, sweep, level_dbfs), fs_hz, sweep.preroll_s, sweep.tail_s
    )
    return BlockStimulus(
        out=np.concatenate([block] * n_blocks),
        block_len=len(block),
        ref=block[:, 0].astype(np.float64),
    )


def mute_plan(state: ConsoleState, roles: Roles) -> MutePlan:
    """Switch states for the sub-only and tops-only blocks.

    A matrix that is muted already stays muted in every block; the other speaker matrices
    (balcony, fills) are muted in both blocks.
    """
    original = {n: state.matrices[n].on for n in roles.all}
    return MutePlan(
        original=original,
        sub_only={n: (n in roles.sub) and on for n, on in original.items()},
        tops_only={n: (n in roles.mains) and on for n, on in original.items()},
    )


def split_irs(
    rec: FloatArray, stimulus: BlockStimulus, fs_hz: float, sweep: SweepConfig
) -> list[FloatArray]:
    """Deconvolve each block of a block recording.

    Returns:
        One circular impulse response per complete block, all on the block's own time base.
    """
    block_len = stimulus.block_len
    return [
        deconvolve(
            np.asarray(rec[i * block_len : (i + 1) * block_len], dtype=np.float64),
            stimulus.ref,
            fs_hz,
            sweep.f1_hz,
            sweep.f2_hz,
        )
        for i in range(len(rec) // block_len)
    ]


class JunctionBlock(StrEnum):
    """Which paths sound in a block of the junction measurement."""

    SUB_ONLY = "sub_only"
    TOPS_ONLY = "tops_only"
    ORIGINAL = "original"


# Called with the block whose mutes must be in place; must raise if the console does not confirm.
MuteSwitch = Callable[[JunctionBlock], None]
MUTE_SWITCH_AFTER_S = 0.3
# The switch (write and readback) must be over before the next block's sweep starts.
MUTE_SWITCH_MARGIN_S = 0.1


@dataclass(frozen=True, slots=True)
class JunctionMic:
    """Sub, tops and both impulse responses of one microphone, on one time base.

    Attributes:
        usb_input: 1-based USB input.
        sub: Circular impulse response of block A (sub only).
        tops: ... of block B (tops only).
        both: ... of block C (everything).
        peaks_dbfs: Recorded peak of each block.
    """

    usb_input: int
    sub: FloatArray
    tops: FloatArray
    both: FloatArray
    peaks_dbfs: tuple[float, float, float]


def measure_junction_round(  # noqa: PLR0913 - explicit configuration instead of globals
    backend: MeasureBackend,
    inputs: Sequence[int],
    switch: MuteSwitch,
    *,
    fs_hz: float,
    sweep: SweepConfig,
    level_dbfs: float,
    stop: StopFlag | None = None,
) -> tuple[JunctionMic, ...]:
    """Measure the sub / top junction at every microphone of a round (SPEC S10).

    The mutes for block A are set before the stream starts, switched to block B and back to the
    original between blocks, and restored in ``finally`` whatever happens: an interrupt, a
    failed switch or a stream error. The caller writes the pending-restore file before calling.

    Raises:
        MeasurementAborted: Stopped; the output is silent and the mutes are restored.
        LevelError: The level is above the hard output limit; nothing is played or muted.
        ValueError: The inputs repeat or are missing, or the pre-roll is too short for the mute
            switch; nothing is played or muted.
    """
    if not inputs or len(set(inputs)) != len(inputs):
        raise ValueError(f"the junction needs distinct microphone inputs, got {list(inputs)}")
    if sweep.preroll_s - MUTE_SWITCH_MARGIN_S <= MUTE_SWITCH_AFTER_S:
        raise ValueError("the mute switch must happen in the silent pre-roll of the next block")
    stimulus = block_stimulus(fs_hz, sweep, level_dbfs)
    switch_after = int(MUTE_SWITCH_AFTER_S * fs_hz)
    events = [
        (stimulus.block_len + switch_after, lambda: switch(JunctionBlock.TOPS_ONLY)),
        (2 * stimulus.block_len + switch_after, lambda: switch(JunctionBlock.ORIGINAL)),
    ]
    restored = False
    try:
        switch(JunctionBlock.SUB_ONLY)
        recording = backend.playrec_multi(stimulus.out, inputs, events, stop)
        switch(JunctionBlock.ORIGINAL)
        restored = True
    finally:
        if not restored:
            switch(JunctionBlock.ORIGINAL)
    mics = []
    length = stimulus.block_len
    for column, usb_input in enumerate(inputs):
        rec = np.asarray(recording.channel(column), dtype=np.float64)
        sub, tops, both = split_irs(rec, stimulus, fs_hz, sweep)[:BLOCKS]
        peaks = tuple(
            round(peak_dbfs(rec[i * length : (i + 1) * length]), 1) for i in range(BLOCKS)
        )
        mics.append(JunctionMic(usb_input, sub, tops, both, (peaks[0], peaks[1], peaks[2])))
    return tuple(mics)


# A junction point is trusted only if the "both" block matches the sum of the sub and tops
# blocks where they meet: otherwise a mute did not switch, switched late, or something moved.
CHECK_BAND_HZ = (50.0, 200.0)
MISMATCH_LIMIT_DB = 3.0
QUIET_BELOW_LOUDEST_DB = 20.0  # a block this far below the loudest one did not really play
CLIP_PEAK_DBFS = -0.5


def junction_mismatch_db(mic: JunctionMic, fs_hz: float) -> float:
    """RMS difference (third octaves, 50-200 Hz) between "both" and the complex sum sub + tops.

    The responses are taken as stored (the causal first half of each circular response).
    """
    half = len(mic.sub) // 2
    sub, tops, both = mic.sub[:half], mic.tops[:half], mic.both[:half]
    n = len(sub)
    freqs = np.fft.rfftfreq(n, 1.0 / fs_hz)
    summed = np.abs(np.fft.rfft(sub, n) + np.fft.rfft(tops, n)) ** 2
    measured = np.abs(np.fft.rfft(both, n)) ** 2
    differences = []
    for centre in third_octave_centres(*CHECK_BAND_HZ):
        inside = (freqs >= centre * 2 ** (-1 / 6)) & (freqs < centre * 2 ** (1 / 6))
        if not inside.any():
            continue
        sum_db = 10 * np.log10(np.mean(summed[inside]) + 1e-30)
        both_db = 10 * np.log10(np.mean(measured[inside]) + 1e-30)
        differences.append(sum_db - both_db)
    return float(np.sqrt(np.mean(np.square(differences)))) if differences else float("inf")


class JunctionProblem(StrEnum):
    """Why a junction point is not used."""

    CLIPPED = "clipped"
    QUIET = "quiet"
    MISMATCH = "mismatch"


def junction_problem(mic: JunctionMic, fs_hz: float) -> JunctionProblem | None:
    """Why this microphone's junction blocks cannot be trusted, or None when they can."""
    if max(mic.peaks_dbfs) > CLIP_PEAK_DBFS:
        return JunctionProblem.CLIPPED
    if min(mic.peaks_dbfs) < max(mic.peaks_dbfs) - QUIET_BELOW_LOUDEST_DB:
        return JunctionProblem.QUIET
    if junction_mismatch_db(mic, fs_hz) > MISMATCH_LIMIT_DB:
        return JunctionProblem.MISMATCH
    return None
