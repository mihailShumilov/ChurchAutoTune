"""The order of writes: never louder than before or after (SPEC §9.3).

One rule for every plan: in every intermediate state the modelled response of every speaker
path stays at most ``max(before, after) + 0.5 dB`` at every frequency from 20 Hz to 20 kHz.

The order is built on console-neutral strip states (EQ block, fader, mute) and then checked by
simulating every prefix of the plan; a plan that cannot be ordered safely is refused. The driver
turns the ordered steps into console writes in the same order.

Base algorithm (§9.3): faders down and mutes first; then each EQ band — gains that only go down
at once, a band that moves (type, frequency, Q) in the first order of its writes that keeps the
rule (if none does, its gain goes to 0 dB first); boosts and faders up last.
"""

import itertools
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

import numpy as np

from churchtune.domain.arrays import FloatArray
from churchtune.domain.eq import Band, EqType, SystemEq
from churchtune.domain.eq_model import eq_response_db
from churchtune.domain.snapshot import SignalRef
from churchtune.errors import UnsafeWrite

TOLERANCE_DB = 0.5
SHELVES = frozenset({EqType.LSHV, EqType.HSHV})
SHELF_SMOOTH_Q = 0.707  # a shelf with a higher Q overshoots on both sides of its corner
# Only a peaking cut is never louder anywhere when it is added: a shelf with a high Q is not.
HOLDING_TYPES = frozenset({EqType.PEQ, EqType.VEQ})
# 1/96 octave, plus the centre frequency of every band of the plan (the peak of a narrow band
# lies on it and could fall between grid points).
GRID_HZ: FloatArray = 20.0 * 2 ** (np.arange(0, int(np.log2(20000 / 20) * 96) + 1) / 96)
MUTED_DB = -200.0
# A band's parameters as written one at a time, read through a table.
BAND_PARAMS: tuple[str, ...] = ("type", "freq_hz", "q", "gain_db")
_PARAM_OF: dict[str, Callable[[Band], object]] = {
    "type": lambda band: band.type,
    "freq_hz": lambda band: band.freq_hz,
    "q": lambda band: band.q,
    "gain_db": lambda band: band.gain_db,
}
# Take one parameter of ``target`` into ``band``.
_TAKE: dict[str, Callable[[Band, Band], Band]] = {
    "type": lambda band, target: replace(band, type=target.type),
    "freq_hz": lambda band, target: replace(band, freq_hz=target.freq_hz),
    "q": lambda band, target: replace(band, q=target.q),
    "gain_db": lambda band, target: replace(band, gain_db=target.gain_db),
}
# What comes last, in this order: EQ switches, faders up, unmuting.
LATE_ORDER: tuple[str, ...] = ("eq_on", "fader", "on")


@dataclass(frozen=True, slots=True)
class StripState:
    """What the model knows of a strip on a speaker path.

    Attributes:
        eq: Its EQ block.
        fader_db: Its fader in dB (``-inf`` when down).
        on: False when muted.
    """

    eq: SystemEq
    fader_db: float = 0.0
    on: bool = True


@dataclass(frozen=True, slots=True)
class Step:
    """One write in console-neutral terms: a whole band, the EQ switch, a fader or a mute.

    Attributes:
        strip: Which strip.
        what: ``band``, ``eq_on``, ``fader`` or ``on``.
        band: The band as it is after this step (one parameter changed), for ``band``.
        value: The new switch or fader value, for the others.
        param: The band parameter this step changes (``type``, ``freq_hz``, ``q``, ``gain_db``).
    """

    strip: SignalRef
    what: Literal["band", "eq_on", "fader", "on"]
    band: Band | None = None
    value: float | bool | None = None
    param: str = ""


Chains = Mapping[str, tuple[SignalRef, ...]]


def plan_grid(*plans: Mapping[SignalRef, StripState]) -> FloatArray:
    """The fixed grid plus every band centre of the given states (within 20 Hz-20 kHz).

    A shelf with Q above 0.707 overshoots away from its corner: around every shelf the grid is
    made finer (1/384 octave over ±1 octave), so its hump is not missed between points.
    """
    extra: list[FloatArray] = []
    for states in plans:
        for state in states.values():
            for band in state.eq.bands:
                if not GRID_HZ[0] <= band.freq_hz <= GRID_HZ[-1]:
                    continue
                extra.append(np.array([band.freq_hz]))
                if band.type in SHELVES and band.q > SHELF_SMOOTH_Q:
                    extra.append(band.freq_hz * 2 ** (np.arange(-384, 385) / 384))
    points = np.concatenate([GRID_HZ, *extra])
    return np.unique(points[(points >= GRID_HZ[0]) & (points <= GRID_HZ[-1])])


def chain_response_db(
    states: Mapping[SignalRef, StripState],
    chain: tuple[SignalRef, ...],
    fs_hz: float,
    grid_hz: FloatArray = GRID_HZ,
) -> FloatArray:
    """Modelled response of one speaker path: every strip's EQ and fader, muted as silence."""
    total = np.zeros_like(grid_hz)
    for ref in chain:
        state = states[ref]
        if not state.on:
            return np.full_like(grid_hz, MUTED_DB)
        total = total + eq_response_db(state.eq, grid_hz, fs_hz) + max(state.fader_db, MUTED_DB)
    return total


class _Simulation:
    """The plan so far, checked against the envelope of before and after."""

    def __init__(
        self,
        before: Mapping[SignalRef, StripState],
        after: Mapping[SignalRef, StripState],
        chains: Chains,
        fs_hz: float,
    ) -> None:
        self.states = dict(before)
        self.chains = chains
        self.fs_hz = fs_hz
        # Intermediate bands take every parameter from before or after: their centres are here.
        self.grid = plan_grid(before, after)
        self.envelope = {
            name: np.maximum(
                chain_response_db(before, chain, fs_hz, self.grid),
                chain_response_db(after, chain, fs_hz, self.grid),
            )
            + TOLERANCE_DB
            for name, chain in chains.items()
        }
        self.steps: list[Step] = []

    def _applied(self, step: Step) -> dict[SignalRef, StripState]:
        states = dict(self.states)
        state = states[step.strip]
        if step.what == "band" and step.band is not None:
            states[step.strip] = replace(state, eq=state.eq.with_band(step.band))
        elif step.what == "eq_on":
            states[step.strip] = replace(state, eq=state.eq.with_on(bool(step.value)))
        elif step.what == "fader":
            states[step.strip] = replace(state, fader_db=float(step.value or 0.0))
        else:
            states[step.strip] = replace(state, on=bool(step.value))
        return states

    def safe(self, steps: Sequence[Step]) -> bool:
        """True when every prefix of ``steps`` keeps every path inside the envelope."""
        states = self.states
        try:
            for step in steps:
                self.states = self._applied(step)
                for name, chain in self.chains.items():
                    if step.strip in chain and np.any(
                        chain_response_db(self.states, chain, self.fs_hz, self.grid)
                        > self.envelope[name]
                    ):
                        return False
            return True
        finally:
            self.states = states

    def take(self, steps: Sequence[Step]) -> None:
        for step in steps:
            self.states = self._applied(step)
            self.steps.append(step)


def _band_steps(strip: SignalRef, old: Band, new: Band, order: Sequence[str]) -> list[Step]:
    """One step per parameter in ``order`` (only parameters that differ are ever passed)."""
    steps = []
    current = old
    for param in order:
        current = _TAKE[param](current, new)
        steps.append(Step(strip, "band", band=current, param=param))
    return steps


def _band_orders(strip: SignalRef, old: Band, new: Band) -> list[list[Step]]:
    """Candidate write orders for one band.

    Every order of its parameters, then the same with the gain at 0 dB first.
    """
    changed = [p for p in BAND_PARAMS if _PARAM_OF[p](old) != _PARAM_OF[p](new)]
    orders = [_band_steps(strip, old, new, perm) for perm in itertools.permutations(changed)]
    if old.adjustable and old.gain_db != 0:
        neutral = replace(old, gain_db=0.0)
        first = Step(strip, "band", band=neutral, param="gain_db")
        # From the neutral band the gain may have to come back to its value, even if it did
        # not change between before and after.
        rest = [p for p in BAND_PARAMS if _PARAM_OF[p](neutral) != _PARAM_OF[p](new)]
        orders.extend(
            [first, *_band_steps(strip, neutral, new, perm)]
            for perm in itertools.permutations(rest)
        )
    return orders


def _switches_and_faders(
    sim: _Simulation,
    before: Mapping[SignalRef, StripState],
    after: Mapping[SignalRef, StripState],
) -> list[Step]:
    """Take the mutes and faders down now; return what must come last.

    Last: unmuting, faders up and EQ switches.
    """
    late: list[Step] = []
    for ref in before:
        old, new = before[ref], after[ref]
        if old.on and not new.on:
            sim.take([Step(ref, "on", value=False)])
        if new.fader_db < old.fader_db:
            sim.take([Step(ref, "fader", value=new.fader_db)])
        if new.eq.on != old.eq.on:
            late.append(Step(ref, "eq_on", value=new.eq.on))
        if new.fader_db > old.fader_db:
            late.append(Step(ref, "fader", value=new.fader_db))
        if new.on and not old.on:
            late.append(Step(ref, "on", value=True))
    return sorted(late, key=lambda step: LATE_ORDER.index(step.what))


def _muted_change(
    sim: _Simulation, ref: SignalRef, rest: Sequence[tuple[SignalRef, Band, Band]]
) -> list[Step] | None:
    """Mute the strip, change all its remaining bands, switch it back on; None if not safe.

    A muted path is never louder; switched on again, the strip has all its planned bands.
    """
    if not sim.states[ref].on:
        return None
    steps = [Step(ref, "on", value=False)]
    for _, old_band, new_band in rest:
        steps.extend(_band_orders(ref, old_band, new_band)[0])
    steps.append(Step(ref, "on", value=True))
    return steps if sim.safe(steps) else None


def _hold_a_cut(
    sim: _Simulation,
    stuck: list[tuple[SignalRef, Band, Band]],
    after: Mapping[SignalRef, StripState],
    helped: set[tuple[SignalRef, int]],
) -> tuple[SignalRef, Band, Band] | None:
    """Copy a stuck cut into a spare band of its strip, so the cut itself can move.

    Two cuts that swap places (Main 50 -> 80 Hz while the sub goes 80 -> 50 Hz) have no order:
    whichever moves first leaves a frequency uncut. A spare band (flat now and after) takes a
    copy of the old cut first (adding a peaking cut is never louder; shelves are not held, and
    the simulation checks the copy anyway); the cut then moves, and the copy
    is removed later like any other change.

    Returns:
        The removal of the copy, to be ordered with the rest; None when no cut can be held.
    """
    for ref, old_band, _new in stuck:
        if (
            (ref, old_band.band) in helped
            or old_band.type not in HOLDING_TYPES
            or old_band.gain_db >= 0
        ):
            continue
        eq = sim.states[ref].eq
        spare = next(
            (b for b in eq.bands
             if b.adjustable and b.gain_db == 0 and b == after[ref].eq.band(b.band)
             and all(item[0] != ref or item[1].band != b.band for item in stuck)),
            None,
        )  # fmt: skip
        if spare is None:
            continue
        copy = replace(old_band, band=spare.band)
        steps = _band_steps(ref, spare, copy, ("type", "freq_hz", "q", "gain_db"))
        if sim.safe(steps):
            sim.take(steps)
            helped.add((ref, old_band.band))
            return ref, copy, spare
    return None


def _mute_one_strip(
    sim: _Simulation,
    stuck: list[tuple[SignalRef, Band, Band]],
    mutable: Collection[SignalRef],
) -> list[tuple[SignalRef, Band, Band]]:
    """Finish one mutable strip with it muted; the bands still waiting after that.

    Raises:
        UnsafeWrite: No mutable strip can be finished that way: there is no safe order.
    """
    for ref in dict.fromkeys(item[0] for item in stuck):
        if ref not in mutable:
            continue
        rest = [item for item in stuck if item[0] == ref]
        muted = _muted_change(sim, ref, rest)
        if muted is not None:
            sim.take(muted)
            return [item for item in stuck if item[0] != ref]
    ref, _, new_band = stuck[0]
    raise UnsafeWrite(
        f"no safe order for band {new_band.band} of {ref.label()}",
        strip=ref.label(),
        band=new_band.band,
    )


def order_steps(
    before: Mapping[SignalRef, StripState],
    after: Mapping[SignalRef, StripState],
    chains: Chains,
    fs_hz: float,
    *,
    mutable: Collection[SignalRef] = (),
) -> tuple[Step, ...]:
    """The writes from ``before`` to ``after`` in an order that is never louder.

    When no order of a band's parameters stays inside the envelope (a high-Q shelf that turns
    into a crossover slope overshoots on its other side), a strip in ``mutable`` is muted for
    the change and switched back on right after it: a muted path is never louder.

    Args:
        before: State of every strip that changes (and of every strip on the same paths).
        after: Wanted state of the same strips.
        chains: Each speaker path as the strips it goes through, in order.
        fs_hz: Sample rate of the EQ model.
        mutable: Strips that may be muted briefly (speaker matrices; never Main LR).

    Returns:
        The ordered steps.

    Raises:
        UnsafeWrite: The strips differ, or no order keeps every path inside the envelope.
    """
    if set(before) != set(after):
        raise UnsafeWrite("before and after must describe the same strips")
    sim = _Simulation(before, after, chains, fs_hz)
    late = _switches_and_faders(sim, before, after)
    # 3. Bands: cuts first, then moves, then boosts.
    pending = [
        (ref, old_band, new_band)
        for ref in before
        for old_band, new_band in zip(before[ref].eq.bands, after[ref].eq.bands, strict=True)
        if old_band != new_band
    ]
    pending.sort(key=lambda item: item[2].gain_db - item[1].gain_db)
    # A band that has no safe order now may have one once another band has moved: try again
    # while anything moves; then mute a mutable strip for the rest of its bands, or refuse.
    waiting = list(pending)
    helped: set[tuple[SignalRef, int]] = set()
    while waiting:
        left = []
        for ref, old_band, new_band in waiting:
            for steps in _band_orders(ref, old_band, new_band):
                if sim.safe(steps):
                    sim.take(steps)
                    break
            else:
                left.append((ref, old_band, new_band))
        if len(left) == len(waiting):
            removal = _hold_a_cut(sim, left, after, helped)
            left = [*left, removal] if removal is not None else _mute_one_strip(sim, left, mutable)
        waiting = left
    if not sim.safe(late):
        raise UnsafeWrite("raising faders or unmuting last would still be too loud")
    sim.take(late)
    if any(sim.states[ref] != after[ref] for ref in after):
        raise UnsafeWrite("the ordered steps do not reach the planned state")
    return tuple(sim.steps)


def check_order(
    before: Mapping[SignalRef, StripState],
    steps: Sequence[Step],
    after: Mapping[SignalRef, StripState],
    chains: Chains,
    fs_hz: float,
) -> None:
    """Refuse a sequence of steps that is louder than the envelope at any point.

    Raises:
        UnsafeWrite: Some intermediate state is too loud, or the steps do not end at ``after``.
    """
    sim = _Simulation(before, after, chains, fs_hz)
    if not sim.safe(steps):
        raise UnsafeWrite("this order of writes would be louder than before and after")
    sim.take(steps)
    if sim.states != dict(after):
        raise UnsafeWrite("the writes do not lead to the planned state")
