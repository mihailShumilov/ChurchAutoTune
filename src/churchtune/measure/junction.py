"""The sub / top junction measurement: three identical sweep blocks in one stream.

Block A plays the sub only, block B the tops only, block C everything as it normally plays;
the mutes are switched in the silent gap between blocks. One stream means one latency, so the
relative timing of sub and tops is exact.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from churchtune.domain.arrays import FloatArray
from churchtune.domain.console import ConsoleState, Roles
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
