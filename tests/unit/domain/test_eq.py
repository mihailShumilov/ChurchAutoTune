import dataclasses

import pytest

from churchtune.domain.eq import (
    ADJUSTABLE_TYPES,
    CUT_FILTER_TYPES,
    Band,
    EqType,
    SystemEq,
)


def _eq(on=True):
    return SystemEq(on, tuple(Band(i, EqType.PEQ, 100.0 * i, 0.0, 2.0) for i in range(1, 7)))


def test_eq_types_split_into_adjustable_and_cut_filters():
    assert {EqType.PEQ, EqType.VEQ, EqType.LSHV, EqType.HSHV} == ADJUSTABLE_TYPES
    assert set(EqType) == ADJUSTABLE_TYPES | CUT_FILTER_TYPES
    assert not ADJUSTABLE_TYPES & CUT_FILTER_TYPES


def test_eq_type_values_are_console_strings():
    assert EqType("LR24") is EqType.LR24
    assert str(EqType.LCUT) == "LCut"


@pytest.mark.parametrize(
    ("eq_type", "expected"),
    [(EqType.PEQ, True), (EqType.HSHV, True), (EqType.BU18, False), (EqType.LCUT, False)],
)
def test_band_adjustable_only_for_tunable_types(eq_type, expected):
    assert Band(1, eq_type, 100.0, 0.0, 2.0).adjustable is expected


def test_band_is_immutable():
    band = Band(2, EqType.PEQ, 1000.0, -3.0, 2.0)

    with pytest.raises(dataclasses.FrozenInstanceError):
        band.gain_db = 0.0  # type: ignore[misc]


@pytest.mark.parametrize("number", [0, 7, -1])
def test_band_rejects_number_outside_1_to_6(number):
    with pytest.raises(ValueError, match="band number"):
        Band(number, EqType.PEQ, 1000.0, 0.0, 2.0)


def test_system_eq_requires_six_bands_in_order():
    bands = tuple(Band(i, EqType.PEQ, 100.0, 0.0, 2.0) for i in range(1, 6))
    with pytest.raises(ValueError, match="6 bands"):
        SystemEq(True, bands)

    swapped = (*_eq().bands[1:], _eq().bands[0])
    with pytest.raises(ValueError, match="order"):
        SystemEq(True, swapped)


def test_system_eq_band_returns_band_by_number():
    assert _eq().band(3).freq_hz == 300.0


def test_system_eq_with_band_replaces_one_band_and_keeps_original():
    eq = _eq()
    new_band = Band(4, EqType.PEQ, 480.0, -9.0, 1.5)

    changed = eq.with_band(new_band)

    assert changed.band(4) == new_band
    assert eq.band(4).gain_db == 0.0
    assert [b.band for b in changed.bands] == [1, 2, 3, 4, 5, 6]


def test_system_eq_with_on_returns_new_object():
    eq = _eq(on=False)

    assert eq.with_on(True).on is True
    assert eq.on is False
