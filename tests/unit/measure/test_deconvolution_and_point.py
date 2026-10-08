"""Deconvolution, impulse-response extraction, quality gates and one-point measurement.

The system under test is synthetic: a pure delay with gain, optionally with noise or clipping,
so every expected number is known analytically.
"""

import numpy as np
import pytest

from churchtune.domain.levels import db_to_lin, peak_dbfs
from churchtune.errors import LevelError
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.deconvolution import deconvolve, extract_ir, ir_noise_dbfs
from churchtune.measure.gates import PointQuality, Verdict, WarningCode, assess, worse
from churchtune.measure.point import measure_point
from churchtune.measure.stimulus import build_stimulus, make_sweep

FS_HZ = 48000.0
SWEEP = SweepConfig(duration_s=2.0, tail_s=1.0)
GATES = QualityGates()


class DelayLine:
    """A fake audio path: delay, gain, optional noise, clipping and status flags."""

    def __init__(self, delay_samples=480, gain_db=0.0, noise_dbfs=None, flags=(), seed=1):
        self.delay_samples = delay_samples
        self.gain = db_to_lin(gain_db)
        self.noise_dbfs = noise_dbfs
        self.flags = list(flags)
        self.rng = np.random.default_rng(seed)
        self.calls = 0

    def playrec(self, out):
        self.calls += 1
        mono = out[:, 0].astype(np.float64)
        rec = np.zeros_like(mono)
        rec[self.delay_samples :] = mono[: len(mono) - self.delay_samples] * self.gain
        if self.noise_dbfs is not None:
            rec += self.rng.normal(scale=db_to_lin(self.noise_dbfs), size=len(rec))
        return np.clip(rec, -1.0, 1.0), list(self.flags)


def _measure(backend, **kwargs):
    return measure_point(backend, fs_hz=FS_HZ, sweep=SWEEP, gates=GATES, **kwargs)


# ------------------------------------------------------------ deconvolution
@pytest.mark.parametrize("delay_samples", [0, 1, 480, 5000])
def test_deconvolved_delay_line_peaks_at_the_delay_with_the_gain(delay_samples):
    stimulus = build_stimulus(make_sweep(FS_HZ, SWEEP), FS_HZ, SWEEP.preroll_s, SWEEP.tail_s)
    ref = stimulus[:, 0].astype(np.float64)
    rec = np.roll(ref, delay_samples) * 0.5

    ir_full = deconvolve(rec, ref, FS_HZ, SWEEP.f1_hz, SWEEP.f2_hz)
    _, peak_idx = extract_ir(ir_full, FS_HZ, SWEEP.ir_length_s, SWEEP.ir_pre_ms)

    assert peak_idx == delay_samples
    # A band-limited (20 Hz..20 kHz) unit impulse peaks at (f2 - f1) / (fs / 2) = 0.8325.
    assert ir_full[peak_idx] == pytest.approx(0.5 * (20000 - 20) / 24000, rel=0.02)


def test_deconvolution_is_linear():
    stimulus = build_stimulus(make_sweep(FS_HZ, SWEEP), FS_HZ, SWEEP.preroll_s, SWEEP.tail_s)
    ref = stimulus[:, 0].astype(np.float64)
    a, b = np.roll(ref, 100), np.roll(ref, 700)

    summed = deconvolve(a + 2 * b, ref, FS_HZ, 20.0, 20000.0)
    separate = deconvolve(a, ref, FS_HZ, 20.0, 20000.0) + 2 * deconvolve(
        b, ref, FS_HZ, 20.0, 20000.0
    )

    np.testing.assert_allclose(summed, separate, atol=1e-9)


def test_deconvolve_rejects_empty_input():
    with pytest.raises(ValueError, match="non-empty"):
        deconvolve(np.zeros(0), np.ones(8), FS_HZ, 20.0, 20000.0)


def test_extract_ir_keeps_pre_and_post_samples_around_the_peak():
    ir_full = np.zeros(1 << 16)
    ir_full[1000] = 1.0
    ir_full[60000] = 5.0  # distortion products live in the second half and are ignored

    ir, peak_idx = extract_ir(ir_full, FS_HZ, length_s=0.5, pre_ms=5.0)

    assert peak_idx == 1000
    assert len(ir) == 240 + 24000
    assert ir[240] == 1.0


def test_extract_ir_clamps_at_the_start_of_the_buffer():
    ir_full = np.zeros(4096)
    ir_full[10] = -1.0

    ir, peak_idx = extract_ir(ir_full, FS_HZ, length_s=0.01, pre_ms=5.0)

    assert peak_idx == 10
    assert ir[10] == -1.0
    assert len(ir) == 10 + 480


def test_ir_noise_floor_is_measured_before_the_peak():
    rng = np.random.default_rng(2)
    ir_full = rng.normal(scale=db_to_lin(-70.0), size=1 << 16)
    ir_full[20000] = 1.0

    # White noise of -70 dBFS RMS over an 11040-sample window: within 0.2 dB.
    assert ir_noise_dbfs(ir_full, 20000, FS_HZ) == pytest.approx(-70.0, abs=0.2)


# ------------------------------------------------------------ one point
def test_clean_point_is_ok_with_known_latency_and_level():
    result = _measure(DelayLine(delay_samples=480, gain_db=-10.0, noise_dbfs=-90.0))

    quality = result.quality
    assert quality.verdict is Verdict.OK
    assert quality.warnings == ()
    assert quality.latency_ms == pytest.approx(10.0)
    assert quality.peak_dbfs == pytest.approx(-34.0, abs=0.1)  # -24 dBFS sweep, -10 dB path
    assert quality.noise_dbfs == pytest.approx(-90.0, abs=0.5)
    assert quality.snr_db > 50
    assert quality.pnr_db > 45
    assert quality.clipped_samples == 0
    assert result.peak_idx == 480
    assert (result.level_dbfs, result.n_avg, result.fs_hz) == (-24.0, 1, FS_HZ)


def test_point_ir_starts_pre_ms_before_the_peak_and_has_the_configured_length():
    result = _measure(DelayLine(delay_samples=480))

    assert int(np.argmax(np.abs(result.ir))) == 240
    assert len(result.ir) == 240 + int(SWEEP.ir_length_s * FS_HZ)


def test_averaging_plays_n_sweeps_reports_progress_and_lowers_the_noise():
    seen = []
    single = _measure(DelayLine(noise_dbfs=-50.0, seed=3))
    averaged = _measure(
        DelayLine(noise_dbfs=-50.0, seed=3), n_avg=4, on_sweep=lambda i, n: seen.append((i, n))
    )

    assert seen == [(1, 4), (2, 4), (3, 4), (4, 4)]
    # Averaging 4 uncorrelated noise realisations lowers the noise by 10*log10(4) = 6 dB.
    assert single.quality.noise_dbfs - averaged.quality.noise_dbfs == pytest.approx(6.0, abs=0.7)
    assert averaged.n_avg == 4


def test_recording_kept_is_the_first_sweep():
    backend = DelayLine(noise_dbfs=-50.0)
    result = _measure(backend, n_avg=2)

    assert backend.calls == 2
    assert len(result.recording) == int((SWEEP.preroll_s + SWEEP.duration_s + SWEEP.tail_s) * FS_HZ)


def test_level_above_the_hard_limit_plays_nothing():
    backend = DelayLine()

    with pytest.raises(LevelError):
        _measure(backend, level_dbfs=-3.0)

    assert backend.calls == 0


def test_n_avg_below_one_is_rejected():
    with pytest.raises(ValueError, match="n_avg"):
        _measure(DelayLine(), n_avg=0)


# ------------------------------------------------------------ gates
def _codes(quality: PointQuality):
    return [w.code for w in quality.warnings]


def test_clipping_is_bad():
    quality = _measure(DelayLine(gain_db=30.0), level_dbfs=-6.0).quality

    assert quality.verdict is Verdict.BAD
    assert quality.clipped_samples > 0
    assert WarningCode.CLIPPING in _codes(quality)
    assert quality.warnings[0].params["count"] == quality.clipped_samples


def test_hot_peak_warns():
    quality = _measure(DelayLine(gain_db=2.0, noise_dbfs=-90.0), level_dbfs=-6.0).quality

    assert quality.verdict is Verdict.WARN
    assert _codes(quality) == [WarningCode.HOT]
    assert quality.warnings[0].params == {"peak_dbfs": pytest.approx(-4.0, abs=0.1)}


@pytest.mark.parametrize(
    ("gain_db", "verdict"), [(-25.0, Verdict.WARN), (-45.0, Verdict.BAD)], ids=["quiet", "silent"]
)
def test_quiet_recording_warns_and_a_very_quiet_one_is_bad(gain_db, verdict):
    quality = _measure(DelayLine(gain_db=gain_db, noise_dbfs=-120.0)).quality

    assert WarningCode.QUIET in _codes(quality)
    assert quality.verdict is verdict


@pytest.mark.parametrize(
    ("noise_dbfs", "verdict"), [(-52.0, Verdict.WARN), (-40.0, Verdict.BAD)], ids=["25dB", "13dB"]
)
def test_low_snr_warns_and_a_very_low_one_is_bad(noise_dbfs, verdict):
    # Sweep at -24 dBFS peak has -27 dBFS RMS: SNR = -27 - noise.
    quality = _measure(DelayLine(noise_dbfs=noise_dbfs)).quality

    assert WarningCode.LOW_SNR in _codes(quality)
    assert quality.snr_db == pytest.approx(-27.0 - noise_dbfs, abs=1.0)
    assert quality.verdict is verdict


def test_noisy_recording_reports_low_pnr_as_unreliable_when_far_below_the_gate():
    quality = _measure(DelayLine(noise_dbfs=-20.0)).quality

    pnr = next(w for w in quality.warnings if w.code is WarningCode.LOW_PNR)
    assert pnr.params["unreliable"] is True
    assert pnr.params["needed_db"] == GATES.min_pnr_db
    assert quality.verdict is Verdict.BAD


def test_stream_flags_warn_and_are_listed_once():
    quality = _measure(DelayLine(noise_dbfs=-90.0, flags=["input overflow"]), n_avg=2).quality

    assert quality.verdict is Verdict.WARN
    assert quality.warnings[-1].code is WarningCode.XRUN
    assert quality.warnings[-1].params == {"flags": "input overflow"}


def test_gates_are_configurable():
    strict = QualityGates(min_snr_db=80.0, bad_snr_db=70.0)

    quality = measure_point(
        DelayLine(noise_dbfs=-90.0), fs_hz=FS_HZ, sweep=SWEEP, gates=strict
    ).quality

    assert quality.verdict is Verdict.BAD
    assert _codes(quality) == [WarningCode.LOW_SNR]


def test_assess_rounds_numbers_as_they_are_stored():
    rng = np.random.default_rng(4)
    n_pre, sweep_n = int(SWEEP.preroll_s * FS_HZ), int(SWEEP.duration_s * FS_HZ)
    rec = rng.normal(scale=1e-4, size=n_pre + sweep_n + 48000)
    rec[n_pre + 20000 : n_pre + 20000 + sweep_n] += 0.05
    ir_full = rng.normal(scale=1e-5, size=1 << 17)
    ir_full[20000] = 0.8

    quality = assess(
        rec, np.abs(rec), ir_full, 20000, fs_hz=FS_HZ, sweep=SWEEP, flags=[], gates=GATES
    )

    for value, digits in [
        (quality.peak_dbfs, 2), (quality.noise_dbfs, 2), (quality.sweep_rms_dbfs, 2),
        (quality.snr_db, 1), (quality.pnr_db, 1), (quality.latency_ms, 2),
    ]:  # fmt: skip
        assert value == round(value, digits)
    assert quality.latency_ms == 416.67  # 20000 samples at 48 kHz, stored with 2 decimals
    assert quality.sweep_rms_dbfs == pytest.approx(-26.02, abs=0.01)  # 0.05 -> -26.02 dBFS
    # Peak 0.8 (-1.94 dBFS) over white noise of 1e-5 RMS (-100 dBFS) in the pre-peak window.
    assert quality.pnr_db == pytest.approx(peak_dbfs(np.array([0.8])) + 100.0, abs=0.3)


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (Verdict.OK, Verdict.WARN, Verdict.WARN),
        (Verdict.BAD, Verdict.WARN, Verdict.BAD),
        (Verdict.WARN, Verdict.BAD, Verdict.BAD),
        (Verdict.OK, Verdict.OK, Verdict.OK),
    ],
)
def test_worse(a, b, expected):
    assert worse(a, b) is expected
