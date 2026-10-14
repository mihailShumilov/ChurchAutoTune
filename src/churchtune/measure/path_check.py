"""SPEC S7: does the sound come out of the speakers and reach the microphones?

``check_path`` plays a quiet (−40 dBFS) 1 kHz tone (for the console meters) and then band-limited
noise, both faded, and reads every microphone: was the noise heard, how far above the room noise
(the band level before the test), and with which system latency (cross-correlation of the noise).
The speaker-by-speaker check lives in ``measure.speakers``.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import db_to_lin, rms_dbfs
from churchtune.domain.recording import Float32Array, StopFlag
from churchtune.measure.backend import MeasureBackend
from churchtune.measure.stimulus import check_level, half_cos_fade

TONE_HZ = 1000.0
TEST_LEVEL_DBFS = -40.0
QUIET_S, TONE_S, NOISE_S = 0.5, 1.0, 1.0
FADE_S = 0.05
NOISE_BAND_HZ = (500.0, 4000.0)
# Heard: the tone stands this far above the same band during the quiet lead-in.
MIN_HEARD_DB = 10.0
MAX_LATENCY_S = 0.5


@dataclass(frozen=True, slots=True)
class MicPath:
    """What one microphone heard during the path check.

    Attributes:
        usb_input: 1-based USB input.
        heard: True when the tone stood out of the room noise.
        tone_db: Level of the test noise above the quiet lead-in, in its band.
        latency_ms: System latency from the noise, when heard.
    """

    usb_input: int
    heard: bool
    tone_db: float
    latency_ms: float | None


def path_stimulus(
    fs_hz: float, level_dbfs: float = TEST_LEVEL_DBFS
) -> tuple[Float32Array, FloatArray]:
    """The check signal ``float32[n, 2]`` and its noise part (for the latency), ``float64[m]``.

    Raises:
        LevelError: The level is above the hard output limit.
    """
    check_level(level_dbfs)
    amplitude = db_to_lin(level_dbfs)
    fade = int(FADE_S * fs_hz)
    t = np.arange(int(TONE_S * fs_hz)) / fs_hz
    tone = amplitude * np.sin(2 * np.pi * TONE_HZ * t)
    sos = signal.butter(4, NOISE_BAND_HZ, "bandpass", fs=fs_hz, output="sos")
    noise = signal.sosfilt(sos, np.random.default_rng(5).normal(size=int(NOISE_S * fs_hz)))
    noise = amplitude * noise / float(np.max(np.abs(noise)))
    for part in (tone, noise):
        envelope = half_cos_fade(fade)
        part[:fade] *= envelope
        part[-fade:] *= envelope[::-1]
    quiet = np.zeros(int(QUIET_S * fs_hz))
    mono = np.concatenate([quiet, tone, noise, quiet])
    return np.stack([mono, mono], axis=1).astype(np.float32), np.asarray(noise)


def _band_db(x: FloatArray, band: tuple[float, float], fs_hz: float) -> float:
    sos = signal.butter(4, band, "bandpass", fs=fs_hz, output="sos")
    return rms_dbfs(signal.sosfilt(sos, x))


def _latency_ms(rec: FloatArray, noise: FloatArray, start: int, fs_hz: float) -> float:
    window = rec[start : start + len(noise) + int(MAX_LATENCY_S * fs_hz)]
    correlation = signal.correlate(window, noise, mode="valid", method="fft")
    return 1000.0 * float(np.argmax(np.abs(correlation))) / fs_hz


def check_path(
    backend: MeasureBackend,
    inputs: Sequence[int],
    fs_hz: float,
    *,
    level_dbfs: float = TEST_LEVEL_DBFS,
    stop: StopFlag | None = None,
) -> tuple[MicPath, ...]:
    """Play the check signal and report what each microphone heard."""
    out, noise = path_stimulus(fs_hz, level_dbfs)
    recording = backend.playrec_multi(out, inputs, stop=stop)
    quiet_end = int(QUIET_S * fs_hz)
    tone_end = quiet_end + int(TONE_S * fs_hz)
    results = []
    for column, usb_input in enumerate(inputs):
        rec = np.asarray(recording.channel(column), dtype=np.float64)
        lead_in = _band_db(rec[: quiet_end - int(FADE_S * fs_hz)], NOISE_BAND_HZ, fs_hz)
        # The band noise decides "heard": a pure tone can fall into a room null at one seat.
        noise_part = rec[tone_end + int(MAX_LATENCY_S * fs_hz) // 2 : tone_end + len(noise)]
        rise = _band_db(noise_part, NOISE_BAND_HZ, fs_hz) - lead_in
        heard = rise >= MIN_HEARD_DB
        latency = _latency_ms(rec, noise, tone_end, fs_hz) if heard else None
        results.append(MicPath(usb_input, heard, round(rise, 1), latency))
    return tuple(results)
