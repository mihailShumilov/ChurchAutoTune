"""Fake audio backends: the stimulus goes through a console model and a synthetic room.

``FakeBackend``: [Main LR EQ model] -> room -> latency -> mic noise -> ADC clip, per point.
``ConsoleRoomBackend``: Main LR EQ -> matrices (EQ, mute, fader, polarity) -> output delays ->
tops and sub -> room -> mic; the console state is read through an injected function at every
segment, so mute switches between sweep blocks take effect like on the real system.

Both apply the same output limiter as the real backend and keep what "left the computer" in
``played``.
"""

from collections.abc import Callable, Mapping, Sequence

import numpy as np
from scipy import signal

from churchtune.audio.base import Float32Array, PlaybackEvent, limit
from churchtune.audio.fake.room import speaker_rooms
from churchtune.domain.arrays import FloatArray
from churchtune.domain.console import ConsoleState
from churchtune.domain.eq import SystemEq
from churchtune.domain.eq_model import band_sos
from churchtune.domain.levels import db_to_lin

BLOCK_FRAMES = 1024
OUTPUT_CHANNELS = 2
OUTPUT_DIMS = 2
STREAM_ROOM_S = 0.2


def _apply_eq(x: FloatArray, eq: SystemEq, fs_hz: float) -> FloatArray:
    if not eq.on:
        return x
    for band in eq.bands:
        sos = band_sos(band, fs_hz)
        if sos is not None:
            x = np.asarray(signal.sosfilt(sos, x), dtype=np.float64)
    return x


def _mono(out: Float32Array) -> tuple[FloatArray, Float32Array]:
    """Limit an output buffer and mix it to mono; returns ``(mono, limited buffer)``."""
    limited = limit(np.asarray(out, dtype=np.float32))
    if limited.ndim != OUTPUT_DIMS or limited.shape[1] != OUTPUT_CHANNELS:
        raise ValueError(f"output buffer must be [n, 2], got shape {limited.shape}")
    return np.asarray(limited, dtype=np.float64).mean(axis=1), limited


class FakeBackend:
    """Synthetic rooms, one per listening point, behind an optional Main LR EQ."""

    name = "simulator"

    def __init__(  # noqa: PLR0913 - the simulated chain has that many knobs
        self,
        rooms: Mapping[str, FloatArray],
        fs_hz: float,
        *,
        latency_samples: int = 1789,
        noise_dbfs: float = -80.0,
        gain_db: float = -12.0,
        eq_provider: Callable[[], SystemEq | None] | None = None,
        seed: int = 1,
    ) -> None:
        """Set up the simulated chain.

        Args:
            rooms: Impulse response per point name, ``float64[n]``.
            fs_hz: Sample rate.
            latency_samples: Fixed system latency.
            noise_dbfs: RMS of the mic noise.
            gain_db: Gain between output and mic.
            eq_provider: Returns the console's current Main LR EQ, applied to playback; None
                means flat.
            seed: Seed of the noise generator.
        """
        self.rooms = dict(rooms)
        self.fs_hz = fs_hz
        self.latency_samples = latency_samples
        self.noise = db_to_lin(noise_dbfs)
        self.gain = db_to_lin(gain_db)
        self.eq_provider = eq_provider
        self.rng = np.random.default_rng(seed)
        self.point = next(iter(self.rooms))
        self.played: list[Float32Array] = []  # every buffer that "left the computer"

    def describe(self) -> str:
        """One line about the simulator."""
        return f"SIMULATOR: synthetic room, {len(self.rooms)} points"

    def check_samplerate(self) -> float:
        """The simulator runs at any rate it was built for."""
        return float(self.fs_hz)

    def set_point(self, name: str) -> None:
        """Select the room of a point: exact name, its prefix before ``-``, or the first room."""
        key = name.split("-", maxsplit=1)[0]
        if name in self.rooms:
            self.point = name
        elif key in self.rooms:
            self.point = key
        else:
            self.point = next(iter(self.rooms))

    def _chain(self, mono: FloatArray) -> Float32Array:
        eq = self.eq_provider() if self.eq_provider else None
        if eq is not None:
            mono = _apply_eq(mono, eq, self.fs_hz)
        y = signal.fftconvolve(mono, self.rooms[self.point])[: len(mono)] * self.gain
        y = np.concatenate([np.zeros(self.latency_samples), y])[: len(mono)]
        y = y + self.rng.normal(scale=self.noise, size=len(y))
        return np.clip(y, -1.0, 1.0).astype(np.float32)

    def playrec(
        self, out: Float32Array, events: Sequence[PlaybackEvent] | None = None
    ) -> tuple[Float32Array, list[str]]:
        """Play through the simulated chain; events run in order after the buffer."""
        mono, limited = _mono(out)
        self.played.append(limited)
        rec = self._chain(mono)
        for _, action in sorted(events or [], key=lambda event: event[0]):
            action()
        return rec, []

    def record(self, duration_s: float) -> Float32Array:
        """Mic noise only."""
        noise = self.rng.normal(scale=self.noise, size=int(duration_s * self.fs_hz))
        return noise.astype(np.float32)

    def run_stream(
        self,
        next_block: Callable[[int], Float32Array | None],
        on_input: Callable[[Float32Array], None],
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Block-wise loop through the first 0.2 s of the room, until ``next_block`` ends it."""
        ir = self.rooms[self.point][: int(STREAM_ROOM_S * self.fs_hz)] * self.gain
        tail = np.zeros(len(ir) - 1)
        while True:
            block = next_block(BLOCK_FRAMES)
            if block is None:
                return
            mono, limited = _mono(block)
            self.played.append(limited)
            y = signal.fftconvolve(mono, ir)
            y[: len(tail)] += tail
            heard, tail = y[: len(mono)], y[len(mono) :]
            noisy = heard + self.rng.normal(scale=self.noise, size=len(heard))
            on_input(np.clip(noisy, -1, 1).astype(np.float32))
            if on_tick:
                on_tick()


class ConsoleRoomBackend:
    """Tops and a sub behind the console's matrices, in one synthetic hall."""

    name = "simulator-console"

    def __init__(  # noqa: PLR0913 - the simulated chain has that many knobs
        self,
        state_fn: Callable[[], ConsoleState],
        fs_hz: float,
        *,
        sub_matrix: int,
        tops: Mapping[int, FloatArray] | None = None,
        sub: FloatArray | None = None,
        latency_samples: int = 2100,
        noise_dbfs: float = -85.0,
        sub_sens_db: float = 26.0,
        top_sens_db: float = 0.0,
        seed: int = 3,
    ) -> None:
        """Set up the simulated system.

        Args:
            state_fn: Returns the current console state (from the emulator).
            fs_hz: Sample rate.
            sub_matrix: Matrix that feeds the sub.
            tops: Impulse response per top matrix; a default hall when omitted with ``sub``.
            sub: Impulse response of the sub.
            latency_samples: Fixed system latency.
            noise_dbfs: RMS of the mic noise.
            sub_sens_db: Sensitivity of the sub path.
            top_sens_db: Sensitivity of the top paths.
            seed: Seed of the noise generator.
        """
        if tops is None or sub is None:
            tops, sub = speaker_rooms(fs_hz)
        self.state_fn = state_fn
        self.tops = dict(tops)
        self.sub = sub
        self.sub_matrix = sub_matrix
        self.fs_hz = fs_hz
        self.latency_samples = latency_samples
        self.noise = db_to_lin(noise_dbfs)
        self.sens = dict.fromkeys(self.tops, db_to_lin(top_sens_db))
        self.sens[sub_matrix] = db_to_lin(sub_sens_db)
        self.rng = np.random.default_rng(seed)
        self.played: list[Float32Array] = []

    def describe(self) -> str:
        """One line about the simulator."""
        return "SIMULATOR: console matrices + sub/tops"

    def check_samplerate(self) -> float:
        """The simulator runs at any rate it was built for."""
        return float(self.fs_hz)

    def set_point(self, name: str) -> None:
        """One point only."""

    def _chain(self, mono: FloatArray, state: ConsoleState) -> FloatArray:
        x = _apply_eq(mono.copy(), state.main_eq, self.fs_hz)
        y = np.zeros_like(x)
        for number, ir in [*self.tops.items(), (self.sub_matrix, self.sub)]:
            matrix = state.matrices[number]
            if not matrix.on or matrix.fader_db == -np.inf:
                continue
            path = _apply_eq(x, matrix.eq, self.fs_hz)
            gain = db_to_lin(matrix.fader_db) * self.sens[number] * (-1 if matrix.invert else 1)
            delay_ms = state.delays[matrix.outputs[0]].effective_ms if matrix.outputs else 0.0
            delay = round(delay_ms * 1e-3 * self.fs_hz)
            path = np.concatenate([np.zeros(delay), path])[: len(path)] * gain
            y += signal.fftconvolve(path, ir)[: len(path)]
        return y

    def playrec(
        self, out: Float32Array, events: Sequence[PlaybackEvent] | None = None
    ) -> tuple[Float32Array, list[str]]:
        """Play through the simulated system; events split the buffer into segments.

        Each event runs at its sample boundary and the console state is read again for the
        segment after it, exactly as a mute switch between sweep blocks would act.
        """
        mono, limited = _mono(out)
        self.played.append(limited)
        ordered = sorted(events or [], key=lambda event: event[0])
        bounds = [0, *(index for index, _ in ordered), len(mono)]
        y = np.zeros(len(mono))
        for i in range(len(bounds) - 1):
            if i > 0:
                ordered[i - 1][1]()
            segment = np.zeros(len(mono))
            segment[bounds[i] : bounds[i + 1]] = mono[bounds[i] : bounds[i + 1]]
            y += self._chain(segment, self.state_fn())
        y = np.concatenate([np.zeros(self.latency_samples), y])[: len(y)]
        y += self.rng.normal(scale=self.noise, size=len(y))
        return np.clip(y, -1, 1).astype(np.float32), []

    def record(self, duration_s: float) -> Float32Array:
        """Mic noise only."""
        noise = self.rng.normal(scale=self.noise, size=int(duration_s * self.fs_hz))
        return noise.astype(np.float32)

    def run_stream(
        self,
        next_block: Callable[[int], Float32Array | None],
        on_input: Callable[[Float32Array], None],
        on_tick: Callable[[], None] | None = None,
    ) -> None:
        """Play blocks into silence: this backend models sweeps, not live level setting."""
        while True:
            block = next_block(BLOCK_FRAMES)
            if block is None:
                return
            _, limited = _mono(block)
            self.played.append(limited)
            on_input(self.rng.normal(scale=self.noise, size=len(limited)).astype(np.float32))
            if on_tick:
                on_tick()
