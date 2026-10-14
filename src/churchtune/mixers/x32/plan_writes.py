"""Ordered console-neutral steps (``safety.order``) as X32/M32 parameter writes.

Each step becomes the writes of one parameter in the same order, with its scope: EQ bands and
switches go in ``system_eq``, faders in ``levels``, mutes in ``temp_mutes``. Old values come from
the strip states the plan started from, so the undo file holds what the console had. A write
whose new value already sits on the console's grid step is left out.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from churchtune.domain.changes import Change, WireValue, WriteScope
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.mixers.x32 import params
from churchtune.safety.laws import ValueLaws
from churchtune.safety.order import Step, StripState
from churchtune.safety.plan import same_step

# Band parameters of a step and their address segment.
_SEGMENT = {"type": "type", "freq_hz": "f", "q": "q", "gain_db": "g"}
_STRIP_RE = re.compile(
    r"^/(?:main/st|mtx/0([1-6]))/(eq/on|eq/([1-6])/(type|f|g|q)|mix/(fader|on))$"
)


@dataclass(frozen=True, slots=True)
class PlannedChange:
    """One write and the scope it must be validated in."""

    scope: WriteScope
    change: Change


def strip_prefix(ref: SignalRef) -> str:
    """The address of a strip that may carry speakers: Main LR or a matrix.

    Raises:
        UnsafeWrite: Any other strip (buses and Mono are never written by a plan).
    """
    if ref.kind is SourceKind.MAIN_L:
        return "/main/st"
    if ref.kind is SourceKind.MATRIX and 1 <= ref.number <= 6:  # noqa: PLR2004 - six matrices
        return f"/mtx/{ref.number:02d}"
    raise UnsafeWrite(f"{ref.label()} is not a strip a plan may write", strip=ref.label())


def _band_write(
    step: Step, previous: StripState, laws: ValueLaws
) -> tuple[Change | None, StripState]:
    if step.band is None or step.param not in _SEGMENT:
        raise UnsafeWrite("a band step must name its band and parameter")
    old_band = previous.eq.band(step.band.band)
    old_values, old_display = laws.band_wire_values(old_band)
    new_values, new_display = laws.band_wire_values(step.band)
    kind = _SEGMENT[step.param]
    after = replace(previous, eq=previous.eq.with_band(step.band))
    if same_step(laws, kind, old_values[kind], new_values[kind]):
        return None, after
    path = f"{strip_prefix(step.strip)}/eq/{step.band.band}/{kind}"
    change = Change(path, old_values[kind], new_values[kind], old_display[kind], new_display[kind])
    return change, after


def _switch(on: bool) -> tuple[WireValue, str]:
    return (1, "ON") if on else (0, "OFF")


def plan_writes(
    steps: Sequence[Step], before: Mapping[SignalRef, StripState], laws: ValueLaws
) -> tuple[PlannedChange, ...]:
    """The writes of ordered steps, each with its scope, in the same order.

    Raises:
        UnsafeWrite: A step names a strip that no plan may write.
    """
    states = dict(before)
    planned: list[PlannedChange] = []
    for step in steps:
        previous = states[step.strip]
        prefix = strip_prefix(step.strip)
        change: Change | None
        if step.what == "band":
            change, states[step.strip] = _band_write(step, previous, laws)
            scope = WriteScope.SYSTEM_EQ
        elif step.what == "eq_on":
            on = bool(step.value)
            old, old_text = _switch(previous.eq.on)
            new, new_text = _switch(on)
            change = Change(f"{prefix}/eq/on", old, new, old_text, new_text)
            states[step.strip] = replace(previous, eq=previous.eq.with_on(on))
            scope = WriteScope.SYSTEM_EQ
        elif step.what == "fader":
            level = float(step.value or 0.0)
            change = Change(
                f"{prefix}/mix/fader",
                laws.fader_wire(previous.fader_db),
                laws.fader_wire(level),
                laws.fader_display(previous.fader_db),
                laws.fader_display(level),
            )
            if same_step(laws, "fader", change.old_value, change.new_value):
                change = None
            states[step.strip] = replace(previous, fader_db=level)
            scope = WriteScope.LEVELS
        else:
            on = bool(step.value)
            old, old_text = _switch(previous.on)
            new, new_text = _switch(on)
            change = Change(f"{prefix}/mix/on", old, new, old_text, new_text)
            states[step.strip] = replace(previous, on=on)
            scope = WriteScope.TEMP_MUTES
        if change is not None:
            planned.append(PlannedChange(scope, change))
    return tuple(planned)


def runs_by_scope(planned: Sequence[PlannedChange]) -> list[tuple[WriteScope, list[Change]]]:
    """Consecutive writes of one scope, in order: each run is authorized on its own."""
    runs: list[tuple[WriteScope, list[Change]]] = []
    for item in planned:
        if runs and runs[-1][0] is item.scope:
            runs[-1][1].append(item.change)
        else:
            runs.append((item.scope, [item.change]))
    return runs


def strip_of(path: str) -> SignalRef:
    """The strip a writable address belongs to.

    Raises:
        UnsafeWrite: The address is not a strip parameter a plan may write.
    """
    match = _STRIP_RE.fullmatch(path)
    if match is None:
        raise UnsafeWrite(f"not a strip parameter a plan may write: {path}", path=path)
    number = match.group(1)
    return SignalRef(SourceKind.MATRIX, int(number)) if number else SignalRef(SourceKind.MAIN_L)


def with_wire_value(state: StripState, path: str, value: WireValue) -> StripState:
    """A strip state with one written parameter set from its wire value (the undo direction).

    Raises:
        UnsafeWrite: The address is not a strip parameter, or the value is not valid for it.
    """
    match = _STRIP_RE.fullmatch(path)
    if match is None:
        raise UnsafeWrite(f"not a strip parameter a plan may write: {path}", path=path)
    _, what, band_no, kind, mix = match.groups()
    is_switch = what == "eq/on" or mix == "on" or kind == "type"
    if is_switch != isinstance(value, int) or isinstance(value, bool):
        raise UnsafeWrite(f"bad value {value!r} for {path}", path=path)
    if is_switch:
        limit = len(params.EQ_TYPES) if kind == "type" else 2
        if not 0 <= int(value) < limit:
            raise UnsafeWrite(f"bad value {value!r} for {path}", path=path)
    elif not 0.0 <= float(value) <= 1.0:
        raise UnsafeWrite(f"bad normalized value {value!r} for {path}", path=path)
    if what == "eq/on":
        return replace(state, eq=state.eq.with_on(bool(value)))
    if mix == "on":
        return replace(state, on=bool(value))
    if mix == "fader":
        return replace(state, fader_db=params.norm_to_fader(float(value)))
    band = state.eq.band(int(band_no))
    if kind == "type":
        band = replace(band, type=params.EQ_TYPES[int(value)])
    elif kind == "f":
        band = replace(band, freq_hz=params.norm_to_freq(float(value)))
    elif kind == "g":
        band = replace(band, gain_db=params.norm_to_gain(float(value)))
    else:
        band = replace(band, q=params.norm_to_q(float(value)))
    return replace(state, eq=state.eq.with_band(band))


def wire_value(state: StripState, path: str, laws: ValueLaws) -> WireValue:
    """The wire value of one strip parameter in a state (as a node read gave it).

    Raises:
        UnsafeWrite: The address is not a strip parameter a plan may write.
    """
    match = _STRIP_RE.fullmatch(path)
    if match is None:
        raise UnsafeWrite(f"not a strip parameter a plan may write: {path}", path=path)
    _, what, band_no, kind, mix = match.groups()
    if what == "eq/on":
        return _switch(state.eq.on)[0]
    if mix == "on":
        return _switch(state.on)[0]
    if mix == "fader":
        return laws.fader_wire(state.fader_db)
    return laws.band_wire_values(state.eq.band(int(band_no)))[0][kind]


# Address segments as console-neutral parameter names.
_PARAM_NAMES = {"type": "type", "f": "freq", "g": "gain", "q": "q"}


def describe_path(path: str) -> tuple[SignalRef, int | None, str]:
    """A writable address as (strip, band or None, parameter): ``gain``, ``fader``, ``eq_on``.

    Raises:
        UnsafeWrite: The address is not a strip parameter a plan may write.
    """
    match = _STRIP_RE.fullmatch(path)
    if match is None:
        raise UnsafeWrite(f"not a strip parameter a plan may write: {path}", path=path)
    _, what, band_no, kind, mix = match.groups()
    strip = strip_of(path)
    if what == "eq/on":
        return strip, None, "eq_on"
    if mix is not None:
        return strip, None, mix
    return strip, int(band_no), _PARAM_NAMES[kind]
