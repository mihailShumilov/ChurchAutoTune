"""Typed configuration of a measurement: the sweep and the quality gates.

Defaults are the values proven in the reference hall (session of 2026-10-02) and listed in
SPEC §8.1 and §8.2.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SweepConfig:
    """Exponential sweep and the silence around it.

    Attributes:
        f1_hz: Start frequency.
        f2_hz: End frequency.
        duration_s: Sweep length.
        preroll_s: Silence before the sweep (used to measure the room noise).
        tail_s: Silence after the sweep, long enough for the reverb to die out.
        fade_in_s: Half-cosine fade at the start of the sweep.
        fade_out_s: Half-cosine fade at the end of the sweep.
        level_dbfs: Default peak level of the sweep.
        ir_pre_ms: How much of the impulse response is kept before its main peak.
        ir_tail_margin_s: The kept impulse response is ``tail_s`` minus this margin long.
    """

    f1_hz: float = 20.0
    f2_hz: float = 20000.0
    duration_s: float = 6.0
    preroll_s: float = 0.5
    tail_s: float = 3.5
    fade_in_s: float = 0.05
    fade_out_s: float = 0.01
    level_dbfs: float = -24.0
    ir_pre_ms: float = 5.0
    ir_tail_margin_s: float = 0.3

    @property
    def ir_length_s(self) -> float:
        """Length of the impulse response kept after the main peak."""
        return self.tail_s - self.ir_tail_margin_s


@dataclass(frozen=True, slots=True)
class QualityGates:
    """Thresholds that grade one measured point.

    Attributes:
        clip_dbfs: A recorded sample at or above this level counts as clipped (bad).
        hot_dbfs: A recorded peak above this is too close to clipping (warn).
        quiet_dbfs: A recorded peak below this is too quiet (warn).
        bad_quiet_margin_db: ... and this much further below it is unusable (bad).
        min_snr_db: Sweep RMS over room noise needed for a clean point; below it warn.
        bad_snr_db: Below this the point is bad.
        min_pnr_db: Impulse-response peak over its noise needed for T30; below it warn.
        bad_pnr_db: Below this the point is bad (not even T20).
        noise_guard_s: The end of the pre-roll left out of the noise estimate.
        max_repeat_db: Largest 1/3-octave difference between two sweeps (100 Hz-8 kHz) of a
            clean point; above it warn.
        bad_repeat_db: Above this the point is bad (something moved or made noise).
        max_sweep_shift_samples: Latency change between the streams of two sweeps tolerated
            without a warning (UNVERIFIED on CoreAudio: docs/hardware-checklist.md #21).
    """

    clip_dbfs: float = -0.5
    hot_dbfs: float = -6.0
    quiet_dbfs: float = -45.0
    bad_quiet_margin_db: float = 15.0
    min_snr_db: float = 30.0
    bad_snr_db: float = 20.0
    min_pnr_db: float = 45.0
    bad_pnr_db: float = 35.0
    noise_guard_s: float = 0.05
    max_repeat_db: float = 1.0
    bad_repeat_db: float = 2.0
    max_sweep_shift_samples: int = 1
