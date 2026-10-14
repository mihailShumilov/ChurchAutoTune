"""SPEC S7: does the sound come out of the speakers and reach the microphones?

``check_path`` plays a quiet (−40 dBFS) 1 kHz tone and then band-limited noise, both faded, and
reads every microphone: was it heard, how far above the room noise, and with which system latency
(cross-correlation of the noise). ``same_polarity`` compares the direct sound of two impulse
responses measured at one microphone (left top against right top): a swapped polarity is a finding
for the report, never an automatic fix.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import signal

from churchtune.domain.arrays import FloatArray
from churchtune.domain.levels import db_to_lin, rms_dbfs
from churchtune.domain.recording import Float32Array, MultiRecording, StopFlag
from churchtune.measure.stimulus import check_level, half_cos_fade

TONE_HZ = 1000.0
TEST_LEVEL_DBFS = -40.0
QUIET_S, TONE_S, NOISE_S = 0.5, 1.0, 1.0
FADE_S = 0.05
NOISE_BAND_HZ = (500.0, 4000.0)
TONE_BAND_HZ = (900.0, 1100.0)
# Heard: the tone stands this far above the same band during the quiet lead-in.
MIN_HEARD_DB = 10.0
MAX_LATENCY_S = 0.5
POLARITY_BAND_HZ = (300.0, 3000.0)
DIRECT_PRE_S, DIRECT_POST_S = 0.002, 0.005


class PlayRec(Protocol):
    """The part of a multichannel backend the path check needs."""

    def playrec_multi(
        self,
        out: Float32Array,
        inputs: Sequence[int],
        events: None = None,
        stop: StopFlag | None = None,
    ) -> MultiRecording:
        """Play and record in one stream."""
        ...


@dataclass(frozen=True, slots=True)
class MicPath:
    """What one microphone heard during the path check.

    Attributes:
        usb_input: 1-based USB input.
        heard: True when the tone stood out of the room noise.
        tone_db: Tone level above the quiet lead-in, in its band.
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
    backend: PlayRec,
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
        lead_in = _band_db(rec[: quiet_end - int(FADE_S * fs_hz)], TONE_BAND_HZ, fs_hz)
        # Leave the latency margin at the start of the tone part.
        tone = _band_db(
            rec[quiet_end + int(MAX_LATENCY_S * fs_hz) // 2 : tone_end], TONE_BAND_HZ, fs_hz
        )
        rise = tone - lead_in
        heard = rise >= MIN_HEARD_DB
        latency = _latency_ms(rec, noise, tone_end, fs_hz) if heard else None
        results.append(MicPath(usb_input, heard, round(rise, 1), latency))
    return tuple(results)


def _direct_window(ir: FloatArray, fs_hz: float) -> FloatArray:
    sos = signal.butter(2, POLARITY_BAND_HZ, "bandpass", fs=fs_hz, output="sos")
    filtered = signal.sosfiltfilt(sos, ir)
    peak = int(np.argmax(np.abs(filtered)))
    start = max(0, peak - int(DIRECT_PRE_S * fs_hz))
    return np.asarray(filtered[start : peak + int(DIRECT_POST_S * fs_hz)])


def same_polarity(ir_a: FloatArray, ir_b: FloatArray, fs_hz: float) -> bool:
    """True when two speakers measured at one microphone have the same polarity."""
    a, b = _direct_window(ir_a, fs_hz), _direct_window(ir_b, fs_hz)
    correlation = signal.correlate(a, b, mode="full")
    return bool(correlation[int(np.argmax(np.abs(correlation)))] > 0)
