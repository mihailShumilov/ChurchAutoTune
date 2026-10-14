"""Checking the speakers one by one (SPEC S7): heard, polarity of the tops, which side.

Each speaker path is isolated through the mutes of the matrices or buses that feed it (the
console has no mute on physical outputs). The isolation is a ``SpeakerSwitch`` supplied by the
caller, as for the junction; the original mutes are restored in ``finally``. Paths that cannot be
isolated (tops straight from Main LR with a sub matrix fed from Main) are left out by the caller,
with an explanation. A swapped polarity or side is a finding for the report, never a fix.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.recording import StopFlag
from churchtune.errors import MeasurementAborted
from churchtune.measure.backend import MeasureBackend
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.multi import measure_round
from churchtune.measure.stimulus import check_level

HEARD_PNR_DB = 20.0
POLARITY_BAND_HZ = (300.0, 3000.0)
DIRECT_PRE_S, DIRECT_POST_S = 0.002, 0.006
SAME_MAX_DEG, INVERTED_MIN_DEG = 60.0, 120.0
SIDE_MIN_MS = 0.3


class Speaker(StrEnum):
    """A speaker path that can be checked alone."""

    TOP_L = "top_l"
    TOP_R = "top_r"
    SUB = "sub"


class Polarity(StrEnum):
    """Result of comparing two speakers' direct sound."""

    SAME = "same"
    INVERTED = "inverted"
    UNCLEAR = "unclear"


class Side(StrEnum):
    """Where a speaker is, seen from two microphones placed left and right."""

    LEFT = "left"
    RIGHT = "right"


# Called with the speaker that must sound alone, or None to restore the original mutes; must raise
# when the console does not confirm.
SpeakerSwitch = Callable[[Speaker | None], None]


@dataclass(frozen=True, slots=True)
class SpeakerCheck:
    """One speaker measured alone.

    Attributes:
        speaker: Which path sounded.
        heard: Per microphone input: True when the speaker stood out of the noise.
        verdicts: Per microphone input: the quality verdict of the sweep (a clipped or noisy sweep
            makes "heard" unreliable).
        irs: Per microphone input: impulse response.
        arrival_ms: Per microphone input: arrival of the direct sound (system latency included).
    """

    speaker: Speaker
    heard: dict[int, bool]
    verdicts: dict[int, str]
    irs: dict[int, FloatArray]
    arrival_ms: dict[int, float]


def measure_speakers_one_by_one(  # noqa: PLR0913 - explicit configuration instead of globals
    backend: MeasureBackend,
    inputs: Sequence[int],
    switch: SpeakerSwitch,
    speakers: Sequence[Speaker],
    *,
    fs_hz: float,
    sweep: SweepConfig,
    level_dbfs: float,
    gates: QualityGates | None = None,
    stop: StopFlag | None = None,
) -> tuple[SpeakerCheck, ...]:
    """Play one sweep per speaker with the others muted; restore the mutes whatever happens.

    Raises:
        MeasurementAborted: Stopped; the output is silent and the mutes are restored.
        LevelError: The level is above the hard output limit; nothing is played or muted.
        ValueError: No speaker or the inputs repeat; nothing is played or muted.
    """
    if not speakers or not inputs or len(set(inputs)) != len(inputs):
        raise ValueError("the speaker check needs speakers and distinct microphone inputs")
    check_level(level_dbfs)
    checks = []
    try:
        for speaker in speakers:
            if stop is not None and stop.is_set():
                raise MeasurementAborted("stopped before the next speaker")
            switch(speaker)
            measurement = measure_round(
                backend,
                inputs,
                fs_hz=fs_hz,
                sweep=sweep,
                gates=gates or QualityGates(),
                level_dbfs=level_dbfs,
                n_avg=1,
                stop=stop,
            )
            checks.append(
                SpeakerCheck(
                    speaker,
                    {m.usb_input: m.quality.pnr_db >= HEARD_PNR_DB for m in measurement.mics},
                    {m.usb_input: m.quality.verdict.value for m in measurement.mics},
                    {m.usb_input: m.ir for m in measurement.mics},
                    {m.usb_input: m.quality.latency_ms for m in measurement.mics},
                )
            )
    finally:
        switch(None)
    return tuple(checks)


def _direct(ir: FloatArray, fs_hz: float) -> FloatArray:
    sos = signal.butter(2, POLARITY_BAND_HZ, "bandpass", fs=fs_hz, output="sos")
    filtered = signal.sosfiltfilt(sos, ir)
    envelope = np.abs(signal.hilbert(filtered))
    peak = int(np.argmax(envelope))
    start = max(0, peak - int(DIRECT_PRE_S * fs_hz))
    return np.asarray(filtered[start : peak + int(DIRECT_POST_S * fs_hz)], dtype=np.float64)


def compare_polarity(ir_a: FloatArray, ir_b: FloatArray, fs_hz: float) -> Polarity:
    """Polarity of two speakers measured at one microphone.

    The phase of the analytic cross-correlation at its envelope peak: near 0° the same, near 180°
    inverted, anything between is unclear (reflections, very different paths).
    """
    a, b = _direct(ir_a, fs_hz), _direct(ir_b, fs_hz)
    correlation = signal.hilbert(signal.correlate(a, b, mode="full"))
    phase = abs(float(np.degrees(np.angle(correlation[int(np.argmax(np.abs(correlation)))]))))
    if phase < SAME_MAX_DEG:
        return Polarity.SAME
    if phase > INVERTED_MIN_DEG:
        return Polarity.INVERTED
    return Polarity.UNCLEAR


def side_from_arrival(left_mic_ms: float, right_mic_ms: float) -> Side | None:
    """Side of a speaker from its arrival at a left and a right microphone (closer = earlier)."""
    difference = right_mic_ms - left_mic_ms
    if abs(difference) < SIDE_MIN_MS:
        return None
    return Side.LEFT if difference > 0 else Side.RIGHT
