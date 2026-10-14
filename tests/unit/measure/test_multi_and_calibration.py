"""Multi-mic rounds and mic-set calibration on the simulated hall (SPEC M2 acceptance)."""

import itertools
import threading

import numpy as np
import pytest

from churchtune.analysis.response import magnitude_db
from churchtune.audio.fake.hall import CALIBRATION_POINT, FakeHall, FakeMic
from churchtune.errors import AudioDeviceError, MeasurementAborted
from churchtune.measure.calibration import CalibrationError, MicProblem, calibrate, mic_set_id
from churchtune.measure.config import QualityGates, SweepConfig
from churchtune.measure.gates import Verdict, WarningCode
from churchtune.measure.multi import measure_round

FS = 48000.0
SWEEP = SweepConfig(duration_s=2.0, tail_s=1.5)
GATES = QualityGates()
# SPEC M2: calibration recovers known sensitivity differences within ±0.3 dB.
SENSITIVITY_TOLERANCE_DB = 0.3
SENSITIVITIES = {10: 0.0, 11: 2.3, 12: -1.7, 13: 0.8}


class MovingMic:
    """Moves the microphone to the next point before each sweep."""

    def __init__(self, hall, points):
        self.hall, self.points = hall, iter(points)

    def playrec_multi(self, out, inputs, events=None, stop=None):
        self.hall.place(10, next(self.points))
        return self.hall.playrec_multi(out, inputs, events, stop)


def _hall(mics):
    hall = FakeHall(FS, mics)
    hall.place_all(CALIBRATION_POINT)
    return hall


def test_calibration_recovers_sensitivity_differences_of_four_mics():
    hall = _hall({usb: FakeMic(sensitivity_db=db) for usb, db in SENSITIVITIES.items()})
    cal = calibrate(measure_round(hall, list(SENSITIVITIES), fs_hz=FS, sweep=SWEEP, gates=GATES))
    for a, b in itertools.combinations(SENSITIVITIES, 2):
        measured = cal.of(a).sensitivity_db - cal.of(b).sensitivity_db
        expected = SENSITIVITIES[a] - SENSITIVITIES[b]
        assert measured == pytest.approx(expected, abs=SENSITIVITY_TOLERANCE_DB), (a, b)
    assert cal.rejected == ()
    assert all(abs(m.delay_ms) < 0.35 for m in cal.mics)  # capsules up to 0.3 ms apart


def test_a_damaged_mic_is_rejected_for_its_shape():
    mics = {usb: FakeMic(sensitivity_db=db) for usb, db in SENSITIVITIES.items()}
    mics[14] = FakeMic(shape=((500.0, 0.0), (2000.0, 7.0), (5000.0, 0.0)))
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert [m.usb_input for m in cal.rejected] == [14]
    assert cal.of(14).problems == (MicProblem.SHAPE,)
    assert cal.of(14).max_shape_db > 3.0


def test_an_inverted_and_a_late_mic_are_rejected():
    mics = {
        10: FakeMic(),
        11: FakeMic(),
        12: FakeMic(),
        13: FakeMic(inverted=True),
        14: FakeMic(delay_ms=1.2),
    }
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert cal.of(13).problems == (MicProblem.POLARITY,)
    assert MicProblem.DELAY in cal.of(14).problems
    # The capsules stand up to 0.3 ms apart; the extra 1.2 ms stands out above that.
    assert cal.of(14).delay_ms > 0.9
    assert all(cal.of(usb).ok for usb in (10, 11, 12))
    assert not cal.polarity_ambiguous


def _co_located(mics):
    """Identical positions (no capsule spread), to check timing to a fraction of a sample."""
    hall = FakeHall(FS, mics)
    for usb in mics:
        hall.place(usb, CALIBRATION_POINT)
    return hall


def test_a_fractional_delay_is_measured_to_a_fraction_of_a_sample():
    mics = {10: FakeMic(), 11: FakeMic(), 12: FakeMic(), 13: FakeMic(delay_ms=0.3)}
    cal = calibrate(
        measure_round(_co_located(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES)
    )
    assert cal.of(13).delay_ms == pytest.approx(0.3, abs=0.02)
    assert cal.of(13).ok


def test_half_inverted_set_is_ambiguous_and_flags_every_mic():
    mics = {10: FakeMic(), 11: FakeMic(), 12: FakeMic(inverted=True), 13: FakeMic(inverted=True)}
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert cal.polarity_ambiguous
    assert all(MicProblem.POLARITY in m.problems for m in cal.mics)


def test_a_late_mic_in_a_pair_flags_both():
    mics = {10: FakeMic(), 11: FakeMic(delay_ms=0.9)}
    cal = calibrate(
        measure_round(_co_located(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES)
    )
    assert cal.pair
    assert all(MicProblem.DELAY in m.problems for m in cal.mics)
    assert cal.of(11).delay_ms - cal.of(10).delay_ms == pytest.approx(0.9, abs=0.02)


def test_a_pair_with_one_damaged_mic_flags_both():
    mics = {10: FakeMic(), 11: FakeMic(shape=((500.0, 0.0), (2000.0, 5.0), (5000.0, 0.0)))}
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert cal.pair
    assert len(cal.rejected) == 2


def test_identical_mics_side_by_side_have_almost_no_shape():
    mics = {usb: FakeMic() for usb in (10, 11, 12, 13)}
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert max(m.max_shape_db for m in cal.mics) < 0.3


def test_a_more_sensitive_mic_is_not_called_noisy():
    mics = {10: FakeMic(), 11: FakeMic(), 12: FakeMic(sensitivity_db=12.0)}
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert cal.of(12).ok


def test_a_noisy_mic_is_rejected():
    mics = {10: FakeMic(), 11: FakeMic(), 12: FakeMic(noise_dbfs=-55.0)}
    cal = calibrate(measure_round(_hall(mics), list(mics), fs_hz=FS, sweep=SWEEP, gates=GATES))
    assert MicProblem.NOISE in cal.of(12).problems


def test_correction_curve_includes_sensitivity_and_no_shape_outside_the_checked_band():
    hall = _hall({usb: FakeMic(sensitivity_db=db) for usb, db in SENSITIVITIES.items()})
    cal = calibrate(measure_round(hall, list(SENSITIVITIES), fs_hz=FS, sweep=SWEEP, gates=GATES))
    freqs = np.array([1000.0, 16000.0])
    correction = cal.correction_db(11, freqs)
    assert correction[0] == pytest.approx(cal.of(11).sensitivity_db, abs=0.3)
    assert correction[1] == pytest.approx(cal.of(11).sensitivity_db)
    with pytest.raises(KeyError):
        cal.of(99)


def test_clipped_calibration_sweeps_are_refused():
    hall = _hall({10: FakeMic(sensitivity_db=40.0), 11: FakeMic()})
    with pytest.raises(CalibrationError, match="10"):
        calibrate(measure_round(hall, [10, 11], fs_hz=FS, sweep=SWEEP, gates=GATES))


def test_calibration_needs_two_mics():
    hall = _hall({10: FakeMic()})
    with pytest.raises(ValueError, match="two"):
        calibrate(measure_round(hall, [10], fs_hz=FS, sweep=SWEEP, gates=GATES))


def test_mic_set_id_is_stable_and_order_free():
    assert mic_set_id([(10, "ECM8000"), (11, "ECM8000")]) == mic_set_id(
        [(11, "ecm8000 "), (10, "ECM8000")]
    )
    assert mic_set_id([(10, "ECM8000")]) != mic_set_id([(11, "ECM8000")])


def test_round_at_different_points_has_different_latencies_and_one_result_per_mic():
    hall = FakeHall(FS, {10: FakeMic(), 11: FakeMic()})
    hall.place(10, "p1")
    hall.place(11, "p7")
    result = measure_round(hall, [10, 11], fs_hz=FS, sweep=SWEEP, gates=GATES)
    assert [m.usb_input for m in result.mics] == [10, 11]
    assert result.result(10).peak_idx != result.result(11).peak_idx
    assert result.result(10).quality.verdict is not Verdict.BAD
    assert result.result(10).repeat_db is not None
    assert result.result(10).repeat_db < GATES.max_repeat_db
    with pytest.raises(KeyError):
        result.result(12)


def test_an_unsteady_point_is_flagged():
    hall = FakeHall(FS, {10: FakeMic()})
    backend = MovingMic(hall, ["p1", "p5"])  # the mic was moved between the two sweeps
    result = measure_round(backend, [10], fs_hz=FS, sweep=SWEEP, gates=GATES)
    codes = [w.code for w in result.result(10).quality.warnings]
    assert WarningCode.UNSTEADY in codes


def test_round_argument_checks():
    hall = FakeHall(FS, {10: FakeMic()})
    with pytest.raises(ValueError, match="n_avg"):
        measure_round(hall, [10], fs_hz=FS, sweep=SWEEP, gates=GATES, n_avg=0)
    with pytest.raises(ValueError, match="microphone"):
        measure_round(hall, [], fs_hz=FS, sweep=SWEEP, gates=GATES)
    with pytest.raises(AudioDeviceError):
        measure_round(hall, [10, 10], fs_hz=FS, sweep=SWEEP, gates=GATES)
    with pytest.raises(AudioDeviceError):
        measure_round(hall, [33], fs_hz=FS, sweep=SWEEP, gates=GATES)


def test_stop_silences_the_output_within_50_ms():
    """SPEC M2: Ctrl+C at any moment gives silence within 50 ms in the fake backend."""
    hall = FakeHall(FS, {10: FakeMic()})
    hall.place(10, "p1")
    stop = threading.Event()
    stop_at = int(1.3 * FS)
    with pytest.raises(MeasurementAborted):
        hall.playrec_multi(
            np.full((int(3 * FS), 2), 0.1, dtype=np.float32), [10], [(stop_at, stop.set)], stop
        )
    played = hall.played[-1]
    last_sound = int(np.max(np.nonzero(np.any(played != 0, axis=1))))
    assert (last_sound - stop_at) / FS <= 0.050
    assert not np.any(played[stop_at + int(0.05 * FS) :])


class LateSecondSweep:
    """The second sweep's stream starts a few samples later (a latency jump between streams)."""

    def __init__(self, hall, shift=3):
        self.hall, self.count, self.shift = hall, 0, shift

    def playrec_multi(self, out, inputs, events=None, stop=None):
        self.count += 1
        recording = self.hall.playrec_multi(out, inputs, events, stop)
        if self.count == 2:
            shifted = np.roll(recording.samples, self.shift, axis=0)
            return type(recording)(shifted, recording.flags)
        return recording


def test_a_latency_jump_between_sweeps_is_reported_and_aligned():
    hall = FakeHall(FS, {10: FakeMic()})
    hall.place(10, "p1")
    single = measure_round(hall, [10], fs_hz=FS, sweep=SWEEP, gates=GATES, n_avg=1).mics[0]
    result = measure_round(LateSecondSweep(hall), [10], fs_hz=FS, sweep=SWEEP, gates=GATES).mics[0]
    warning = next(w for w in result.quality.warnings if w.code is WarningCode.UNSTEADY)
    assert warning.params["shift_samples"] == 3
    at_12k = np.array([12000.0])
    aligned = magnitude_db(result.ir, FS, grid_hz=at_12k)[0]
    reference = magnitude_db(single.ir, FS, grid_hz=at_12k)[0]
    assert aligned == pytest.approx(reference, abs=0.3)


def test_a_one_sample_latency_change_is_tolerated():
    hall = FakeHall(FS, {10: FakeMic()})
    hall.place(10, "p1")
    result = measure_round(LateSecondSweep(hall, 1), [10], fs_hz=FS, sweep=SWEEP, gates=GATES)
    assert WarningCode.UNSTEADY not in [w.code for w in result.mics[0].quality.warnings]
