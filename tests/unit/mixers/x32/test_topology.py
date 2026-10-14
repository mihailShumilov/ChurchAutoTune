"""Topology inference on the fixtures of tests/fixtures/x32/topologies (SPEC M1 acceptance)."""

import pytest

from churchtune.domain.eq import Band, EqType
from churchtune.domain.snapshot import OutputKind
from churchtune.domain.topology import SpeakerRole, Variant
from churchtune.mixers.x32.topology import has_hpf, has_lpf, infer_topology
from tests.fixtures.topologies import all_fixtures

FIXTURES = all_fixtures()


def test_at_least_six_topologies_including_the_required_ones():
    names = {f.name for f in FIXTURES}
    required = {"hall-2026-10-02", "sub-on-bus", "sub-on-mono", "no-sub", "tops-on-main"}
    assert required <= names
    assert any(f.model.startswith("M32") for f in FIXTURES)
    assert len(FIXTURES) >= 6


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.name)
def test_topology_matches_the_expected_roles(fixture):
    guess = infer_topology(fixture.snapshot())
    expected = fixture.expected
    assert guess.variant == Variant(expected["variant"])
    sub = expected.get("sub")
    assert (guess.sub.label() if guess.sub else None) == sub
    assert sorted(t.label() for t in guess.tops) == sorted(expected["tops"])
    if "others" in expected:
        assert sorted(o.label() for o in guess.others) == sorted(expected["others"])
    for key, role in expected["outputs"].items():
        kind, number = key.split()
        assert guess.role_of(OutputKind(kind), int(number)) == SpeakerRole(role), key
    codes = {i.code for i in guess.issues}
    for code in expected.get("issues", []):
        assert code in codes, (code, codes)


def test_reference_hall_reasons_and_confidence():
    hall = next(f for f in FIXTURES if f.name == "hall-2026-10-02")
    guess = infer_topology(hall.snapshot())
    sub = next(g for g in guess.outputs if g.number == 6 and g.kind is OutputKind.MAIN)
    assert sub.confidence >= 0.9
    assert [r.code for r in sub.reasons][:2] == ["name", "lpf"]
    assert sub.name == "Sub buffer"
    tops = [g for g in guess.outputs if g.role in (SpeakerRole.TOP_L, SpeakerRole.TOP_R)]
    assert all(g.confidence >= 0.9 for g in tops)


def test_unnamed_matrices_need_confirmation():
    fixture = next(f for f in FIXTURES if f.name == "unnamed-linked-matrices")
    guess = infer_topology(fixture.snapshot())
    assert guess.confidence < 0.7


def _band(number, eq_type, freq_hz, gain_db=0.0):
    return Band(number, eq_type, freq_hz, gain_db, 2.0)


@pytest.mark.parametrize(
    ("bands", "hpf", "lpf"),
    [
        ([_band(1, EqType.LCUT, 112.5)], True, False),
        ([_band(1, EqType.BU18, 176.2)], True, False),
        ([_band(1, EqType.BU18, 23.0)], False, False),  # subsonic, not a crossover
        ([_band(6, EqType.LR24, 100.0)], False, True),
        ([_band(6, EqType.HCUT, 120.0)], False, True),
        ([_band(6, EqType.HSHV, 751.7, -15.0)], False, True),  # crutch low-pass
        ([_band(6, EqType.HSHV, 10740.0, 2.25)], False, False),
        ([_band(3, EqType.PEQ, 100.0, -6.0)], False, False),
    ],
)
def test_filter_detection(bands, hpf, lpf):
    assert (has_hpf(bands) is not None) is hpf
    assert (has_lpf(bands) is not None) is lpf
