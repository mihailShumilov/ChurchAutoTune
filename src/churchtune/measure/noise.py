"""Background noise of the hall, per microphone and per octave (SPEC S8).

Three seconds of silence per microphone give its RMS, its octave levels and a hint of the cause:
mains hum from dimmers and LED drivers (100/120 Hz and harmonics, seen in the reference hall),
low-frequency rumble (ventilation) or general noise (talking, traffic). Hints are codes; the UI
turns them into advice ("turn the stage lights off").
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from churchtune.analysis.bands import OCTAVES_HZ, octave_levels
from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import rms_dbfs
from churchtune.domain.recording import StopFlag
from churchtune.measure.backend import MeasureBackend

NOISE_SECONDS = 3.0
# Above this broadband level the room is too loud for clean sweeps at the default level.
LOUD_ROOM_DBFS = -50.0
# Hum: a narrow spectral line this far above the median of its neighbourhood.
HUM_PROMINENCE_DB = 15.0
HUM_FAMILIES_HZ = (50.0, 60.0)
# Fundamental and harmonics: even ones come from dimmers, odd ones from ground loops.
HUM_HARMONICS = (1, 2, 3, 4, 5, 6)
HUM_NEIGHBOURHOOD_HZ = 15.0
HUM_LINE_HZ = 1.5
# Rumble: the 63 and 125 Hz octaves this far above the 1 and 2 kHz octaves.
RUMBLE_EXCESS_DB = 20.0
LOW_OCTAVES = (63, 125)
MID_OCTAVES = (1000, 2000)


class NoiseHint(StrEnum):
    """Likely cause of the background noise."""

    LOUD = "loud"
    HUM = "hum"
    RUMBLE = "rumble"


@dataclass(frozen=True, slots=True)
class MicNoise:
    """Background noise at one microphone.

    Attributes:
        usb_input: 1-based USB input.
        rms_dbfs: Broadband RMS.
        octaves_dbfs: RMS per octave band, keyed by centre frequency.
        hum_hz: Mains frequency whose harmonics stand out (50 or 60), or None.
        hints: Likely causes, most important first.
    """

    usb_input: int
    rms_dbfs: float
    octaves_dbfs: dict[int, float]
    hum_hz: float | None
    hints: tuple[NoiseHint, ...]


def hum_frequency(x: FloatArray, fs_hz: float) -> float | None:
    """Mains frequency (50 or 60 Hz) whose harmonics stand out as narrow lines, or None."""
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / fs_hz)
    floor = 1e-30
    for mains_hz in HUM_FAMILIES_HZ:
        prominent = 0
        for harmonic in HUM_HARMONICS:
            line_hz = mains_hz * harmonic
            line = np.abs(freqs - line_hz) <= HUM_LINE_HZ
            around = (np.abs(freqs - line_hz) <= HUM_NEIGHBOURHOOD_HZ) & ~line
            if not line.any() or not around.any():
                continue
            peak = 10 * np.log10(max(float(np.max(spectrum[line])), floor))
            base = 10 * np.log10(max(float(np.median(spectrum[around])), floor))
            prominent += int(peak - base >= HUM_PROMINENCE_DB)
        if prominent >= 2:  # noqa: PLR2004 - two harmonics make a hum, one line may be anything
            return mains_hz
    return None


def analyse_noise(x: FloatArray, fs_hz: float, usb_input: int) -> MicNoise:
    """Levels and likely causes of the noise recorded at one microphone."""
    level = rms_dbfs(x)
    octaves = octave_levels(x, fs_hz, OCTAVES_HZ)
    hum = hum_frequency(x, fs_hz)
    low = max((octaves[b] for b in LOW_OCTAVES if b in octaves), default=level)
    mid = max((octaves[b] for b in MID_OCTAVES if b in octaves), default=level)
    hints = [
        hint
        for hint, present in (
            (NoiseHint.HUM, hum is not None),
            (NoiseHint.RUMBLE, low - mid >= RUMBLE_EXCESS_DB),
            (NoiseHint.LOUD, level > LOUD_ROOM_DBFS),
        )
        if present
    ]
    return MicNoise(usb_input, round(level, 1), octaves, hum, tuple(hints))


def measure_background(
    backend: MeasureBackend,
    inputs: Sequence[int],
    fs_hz: float,
    *,
    duration_s: float = NOISE_SECONDS,
    stop: StopFlag | None = None,
) -> tuple[MicNoise, ...]:
    """Record silence and analyse the noise at every microphone."""
    recording = backend.record_multi(duration_s, inputs, stop)
    return tuple(
        analyse_noise(np.asarray(recording.channel(i), dtype=np.float64), fs_hz, usb)
        for i, usb in enumerate(inputs)
    )
