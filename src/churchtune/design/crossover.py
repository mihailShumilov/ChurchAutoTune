"""Sub / top crossover: the target EQ of the sub and top matrices.

Sub:  band 6 becomes a low-pass of the chosen slope at fc; boosts in the crossover zone
      (fc/2..2fc) and the "pseudo low-pass" cuts above 2 fc (shelves or deep cuts used instead
      of a real filter) are set to 0 dB; voicing below fc/2 is kept.
Tops: band 1 becomes a high-pass of the same slope at fc; cuts below 1.5 fc that imitated a
      high-pass are set to 0 dB; everything else is kept.
The filters go on the separate sub and top paths only, never on a common path.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

import numpy as np

from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.eq import BANDS_PER_EQ, Band, EqQuantizer, EqType
from churchtune.domain.eq_model import band_response_db

DEFAULT_FC_HZ = 100.0
DEFAULT_SLOPE = EqType.LR24
# Slopes offered for a crossover.
CROSSOVER_SLOPES: frozenset[EqType] = frozenset(
    {EqType.LR24, EqType.LR12, EqType.BU24, EqType.BU18, EqType.BU12}
)
HPF_BAND = 1
LPF_BAND = BANDS_PER_EQ
# Bands with less gain than this are not in use.
ACTIVE_GAIN_DB = 0.25
ZONE_OCTAVES = 1.0  # the crossover zone is fc / 2 .. 2 fc
FAKE_HPF_BELOW_FC_FACTOR = 1.5
# Warn when the new high-pass lets this much more through an octave or two below fc.
MORE_LOWS_WARNING_DB = 1.0


class NoteCode(StrEnum):
    """What the crossover plan does to one band, or a warning about it."""

    FAKE_LOW_PASS_REMOVED = "fake-low-pass-removed"  # cut above the sub's range, now a real LPF
    BOOST_ABOVE_SUB_REMOVED = "boost-above-sub-removed"
    BOOST_IN_ZONE_REMOVED = "boost-in-zone-removed"  # a boost in the zone spoils the junction
    SUB_LOW_PASS = "sub-low-pass"
    TOP_HIGH_PASS = "top-high-pass"
    FAKE_HIGH_PASS_REMOVED = "fake-high-pass-removed"
    MORE_LOWS_TO_TOPS = "more-lows-to-tops"  # the new HPF passes more lows than the old one


@dataclass(frozen=True, slots=True)
class CrossoverNote:
    """One line of the crossover plan.

    Attributes:
        code: What happens.
        matrix: Matrix number.
        band: The band before the change.
        params: Numbers for the text (``fc_hz``, ``slope``, levels).
    """

    code: NoteCode
    matrix: int
    band: Band
    params: Mapping[str, float | str]


@dataclass(frozen=True, slots=True)
class CrossoverPlan:
    """Target EQ bands per matrix, and what changes.

    Attributes:
        fc_hz: Crossover frequency, on the console grid.
        slope: Filter type of both sides.
        targets: The six target bands for each sub and top matrix.
        notes: Explanations and warnings, in plan order.
    """

    fc_hz: float
    slope: EqType
    targets: Mapping[int, tuple[Band, ...]]
    notes: tuple[CrossoverNote, ...]


def _sub_targets(
    matrix: int, bands: tuple[Band, ...], fc_hz: float, slope: EqType, notes: list[CrossoverNote]
) -> tuple[Band, ...]:
    result = list(bands)
    common: dict[str, float | str] = {"fc_hz": fc_hz, "slope": str(slope)}
    for i, band in enumerate(result[: LPF_BAND - 1]):
        if abs(band.gain_db) < ACTIVE_GAIN_DB or not band.adjustable:
            continue
        code: NoteCode | None = None
        if band.freq_hz > 2**ZONE_OCTAVES * fc_hz:
            code = (
                NoteCode.FAKE_LOW_PASS_REMOVED
                if band.gain_db < 0
                else NoteCode.BOOST_ABOVE_SUB_REMOVED
            )
        elif fc_hz / 2**ZONE_OCTAVES <= band.freq_hz and band.gain_db > 0:
            code = NoteCode.BOOST_IN_ZONE_REMOVED
        if code is not None:
            notes.append(CrossoverNote(code, matrix, band, common))
            result[i] = replace(band, gain_db=0.0)
    last = result[LPF_BAND - 1]
    notes.append(CrossoverNote(NoteCode.SUB_LOW_PASS, matrix, last, common))
    result[LPF_BAND - 1] = Band(LPF_BAND, slope, fc_hz, last.gain_db, last.q)
    return tuple(result)


def _top_targets(  # noqa: PLR0913 - the plan needs all of these for one matrix
    matrix: int,
    bands: tuple[Band, ...],
    *,
    fc_hz: float,
    slope: EqType,
    fs_hz: float,
    notes: list[CrossoverNote],
) -> tuple[Band, ...]:
    result = list(bands)
    common: dict[str, float | str] = {"fc_hz": fc_hz, "slope": str(slope)}
    first = result[0]
    notes.append(CrossoverNote(NoteCode.TOP_HIGH_PASS, matrix, first, common))
    result[0] = Band(HPF_BAND, slope, fc_hz, first.gain_db, first.q)
    if not first.adjustable:
        probe_hz = np.array([fc_hz / 2, fc_hz / 4])
        old_db = band_response_db(first, probe_hz, fs_hz)
        new_db = band_response_db(result[0], probe_hz, fs_hz)
        if np.any(new_db > old_db + MORE_LOWS_WARNING_DB):
            notes.append(
                CrossoverNote(
                    NoteCode.MORE_LOWS_TO_TOPS,
                    matrix,
                    first,
                    {
                        **common,
                        "old_db": float(old_db[0]),
                        "new_db": float(new_db[0]),
                        "at_hz": fc_hz / 2,
                    },
                )
            )
    for i, band in enumerate(result[1:], start=1):
        imitates_hpf = (
            band.adjustable
            and band.gain_db <= -ACTIVE_GAIN_DB
            and band.freq_hz < FAKE_HPF_BELOW_FC_FACTOR * fc_hz
        )
        if imitates_hpf:
            notes.append(CrossoverNote(NoteCode.FAKE_HIGH_PASS_REMOVED, matrix, band, common))
            result[i] = replace(band, gain_db=0.0)
    return tuple(result)


def plan_crossover(  # noqa: PLR0913 - explicit inputs instead of globals
    state: ConsoleState,
    roles: Roles,
    quantizer: EqQuantizer,
    fs_hz: float,
    *,
    fc_hz: float = DEFAULT_FC_HZ,
    slope: EqType = DEFAULT_SLOPE,
) -> CrossoverPlan:
    """Plan a crossover between the sub and the tops.

    Args:
        state: Console state.
        roles: Which matrices are sub and tops.
        quantizer: The console's EQ value grid (the crossover frequency is snapped to it).
        fs_hz: Sample rate used for the EQ response model.
        fc_hz: Wanted crossover frequency.
        slope: Filter type for both sides.

    Returns:
        The plan: target bands for every sub and top matrix, and notes.

    Raises:
        ValueError: ``slope`` is not a crossover slope.
    """
    if slope not in CROSSOVER_SLOPES:
        raise ValueError(f"{slope} is not a crossover slope")
    fc = quantizer.snap_freq_hz(fc_hz)
    notes: list[CrossoverNote] = []
    targets: dict[int, tuple[Band, ...]] = {}
    for n in roles.sub:
        targets[n] = _sub_targets(n, state.matrices[n].eq.bands, fc, slope, notes)
    for n in roles.mains:
        targets[n] = _top_targets(
            n, state.matrices[n].eq.bands, fc_hz=fc, slope=slope, fs_hz=fs_hz, notes=notes
        )
    return CrossoverPlan(fc, slope, targets, tuple(notes))
