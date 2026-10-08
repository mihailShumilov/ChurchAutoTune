"""Synthetic rooms with known properties, for the fake audio backend.

``synthetic_room`` builds an impulse response with:
  * per-octave RT60 (exponentially decaying band-limited noise),
  * room modes (damped sinusoids) at chosen frequencies,
  * an optional narrow notch and broad dip (to test that the EQ designer leaves the first alone
    and caps boosts for the second).
"""

from collections.abc import Mapping, Sequence

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqType
from churchtune.domain.eq_model import band_sos

# Octave centre (Hz) -> RT60 (s) of the default synthetic hall.
DEFAULT_RT60_S: Mapping[float, float] = {
    31.5: 2.2,
    63: 2.1,
    125: 2.0,
    250: 1.8,
    500: 1.6,
    1000: 1.5,
    2000: 1.3,
    4000: 1.1,
    8000: 0.9,
    16000: 0.6,
}
# ln(10**3): an amplitude envelope exp(-DECAY_60DB * t / rt60) falls 60 dB in rt60 seconds.
DECAY_60DB = 6.9078
DIRECT_DELAY_S = 0.012
REVERB_GAP_S = 0.004
MAX_BAND_EDGE_FRACTION = 0.45
PEAK_LEVEL = 0.5

Mode = tuple[float, float, float]  # (freq_hz, relative amplitude, rt60_s)
Filter = tuple[float, float, float]  # (freq_hz, gain_db, q)


def synthetic_room(  # noqa: PLR0913 - a room has that many independent properties
    rng: np.random.Generator,
    fs_hz: float,
    *,
    length_s: float = 3.0,
    rt60_s: Mapping[float, float] | None = None,
    modes: Sequence[Mode] = (),
    notch: Filter | None = None,
    broad: Filter | None = None,
    direct_delay_s: float = DIRECT_DELAY_S,
    drr_db: float = -6.0,
) -> FloatArray:
    """Build a room impulse response.

    Args:
        rng: Random generator for the reverberant noise.
        fs_hz: Sample rate.
        length_s: Length of the response.
        rt60_s: RT60 per octave band; ``DEFAULT_RT60_S`` when omitted.
        modes: Room modes as ``(freq_hz, relative amplitude, rt60_s)``.
        notch: Narrow peaking filter ``(freq_hz, gain_db, q)`` applied to the whole response.
        broad: Broad peaking filter, same form.
        direct_delay_s: Arrival time of the direct sound.
        drr_db: Direct-to-reverberant energy ratio.

    Returns:
        ``float64[int(length_s * fs_hz)]``, peak-normalised to 0.5.
    """
    decay_times = DEFAULT_RT60_S if rt60_s is None else rt60_s
    n = int(length_s * fs_hz)
    t = np.arange(n) / fs_hz
    reverb = np.zeros(n)
    for centre_hz, band_rt60_s in decay_times.items():
        low = centre_hz / np.sqrt(2)
        high = min(centre_hz * np.sqrt(2), fs_hz * MAX_BAND_EDGE_FRACTION)
        sos = signal.butter(4, [low, high], btype="bandpass", fs=fs_hz, output="sos")
        band = signal.sosfiltfilt(sos, rng.normal(size=n))
        reverb += band * np.exp(-DECAY_60DB * t / band_rt60_s)
    onset = int(direct_delay_s * fs_hz)
    reverb[: onset + int(REVERB_GAP_S * fs_hz)] = 0  # reverb starts after the direct sound
    reverb /= np.sqrt(np.sum(reverb**2))
    reverb *= 10 ** (-drr_db / 20)
    ir = reverb
    ir[onset] += 1.0  # direct sound
    for freq_hz, amplitude, mode_rt60_s in modes:
        since_onset = t - t[onset]
        envelope = np.exp(-DECAY_60DB * np.maximum(since_onset, 0) / mode_rt60_s) * (t >= t[onset])
        ir += amplitude * envelope * np.sin(2 * np.pi * freq_hz * since_onset)
    for spec in (notch, broad):
        if spec is not None:
            freq_hz, gain_db, q = spec
            peaking = band_sos(Band(2, EqType.PEQ, freq_hz, gain_db, q), fs_hz)
            if peaking is not None:
                ir = signal.sosfilt(peaking, ir)
    return np.asarray(ir / np.max(np.abs(ir)) * PEAK_LEVEL, dtype=np.float64)


def demo_rooms(fs_hz: float) -> dict[str, FloatArray]:
    """Six listening points ``p1``..``p6`` of one hall.

    They share modes at 63 and 120 Hz, a narrow notch at 800 Hz and a broad -4 dB dip around
    3.5 kHz; ``p3`` has an extra local mode at 1250 Hz.
    """
    rooms: dict[str, FloatArray] = {}
    for i in range(6):
        scale = 0.8 + 0.08 * i
        modes: list[Mode] = [(63.0, 0.0045 * scale, 2.6), (120.0, 0.0035 * scale, 2.4)]
        if i == 2:  # noqa: PLR2004 - the third point carries the local mode
            modes.append((1250.0, 0.004, 1.6))
        rooms[f"p{i + 1}"] = synthetic_room(
            np.random.default_rng(100 + i),
            fs_hz,
            modes=modes,
            notch=(800.0, -15.0, 8.0),
            broad=(3500.0, -4.0, 0.9),
        )
    return rooms


def speaker_rooms(
    fs_hz: float, *, sub_extra_ms: float = 7.0, seed: int = 500, drr_db: float = -6.0
) -> tuple[dict[int, FloatArray], FloatArray]:
    """Two tops and one sub in the same synthetic hall.

    The sub arrives ``sub_extra_ms`` later (distance and processing); the tops have a natural
    second-order roll-off at 70 Hz, the sub a natural fourth-order low-pass at 180 Hz.

    Returns:
        ``(tops, sub)``: impulse responses of the tops keyed 1 and 2, and of the sub.
    """
    tops: dict[int, FloatArray] = {}
    top_sos = signal.butter(2, 70, "highpass", fs=fs_hz, output="sos")
    for i, number in enumerate((1, 2)):
        room = synthetic_room(np.random.default_rng(seed + i), fs_hz, drr_db=drr_db)
        tops[number] = np.asarray(signal.sosfilt(top_sos, room), dtype=np.float64)
    room = synthetic_room(
        np.random.default_rng(seed + 9),
        fs_hz,
        direct_delay_s=DIRECT_DELAY_S + sub_extra_ms / 1000,
        drr_db=drr_db,
    )
    sub_sos = signal.butter(4, 180, "lowpass", fs=fs_hz, output="sos")
    return tops, np.asarray(signal.sosfilt(sub_sos, room), dtype=np.float64)
