"""Turning sub / top targets into ordered, validated writes of the ``sub`` scope.

Every change returned here has passed the write policy; a target the policy refuses (for
example an output delay while those writes are not verified on hardware) raises ``UnsafeWrite``
and the caller presents it as advice instead.
"""

from collections.abc import Mapping, Sequence

from churchtune.domain.changes import Change, WriteScope
from churchtune.domain.console import ConsoleState, Roles
from churchtune.domain.eq import Band
from churchtune.safety.policy import WritePolicy

# Write phases that keep the system quiet while it is switched over.
PHASE_GAIN_DOWN = 1  # remove boosts first
PHASE_FILTER = 2  # then type, frequency and Q
PHASE_GAIN_UP = 3  # remove old cuts last
_SAME_NORM = 1e-9
_SAME_DELAY_NORM = 1e-12
# Delays and fader moves smaller than these are not worth a write.
MIN_DELAY_MS = 0.05
MIN_FADER_CHANGE_DB = 0.5
INVERTED_POLARITY = -1


def crossover_changes(
    state: ConsoleState, targets: Mapping[int, Sequence[Band]], policy: WritePolicy
) -> list[Change]:
    """Diff matrix EQ targets against the console into writes, quietest order first.

    Phase 1 lowers gains (removes boosts), phase 2 changes filter type, frequency and Q,
    phase 3 raises gains (removes old cuts).

    Args:
        state: Console state.
        targets: Six target bands per matrix.
        policy: Write policy; every change is validated in the ``sub`` scope.

    Returns:
        The writes in the order they must be sent.

    Raises:
        UnsafeWrite: A change is outside the policy.
    """
    laws = policy.laws
    phased: list[tuple[int, Change]] = []
    for matrix, bands in targets.items():
        for current, target in zip(state.matrices[matrix].eq.bands, bands, strict=True):
            cur_values, cur_display = laws.band_wire_values(current)
            tgt_values, tgt_display = laws.band_wire_values(target)
            for kind in ("type", "f", "q", "g"):
                if kind == "type":
                    same = cur_values[kind] == tgt_values[kind]
                else:
                    same = abs(cur_values[kind] - tgt_values[kind]) < _SAME_NORM
                if same:
                    continue
                if kind != "g":
                    phase = PHASE_FILTER
                else:
                    lower = tgt_values["g"] < cur_values["g"]
                    phase = PHASE_GAIN_DOWN if lower else PHASE_GAIN_UP
                change = Change(
                    f"/mtx/{matrix:02d}/eq/{current.band}/{kind}",
                    cur_values[kind],
                    tgt_values[kind],
                    cur_display[kind],
                    tgt_display[kind],
                )
                phased.append((phase, policy.checked_change(change, WriteScope.SUB)))
    phased.sort(key=lambda item: item[0])
    return [change for _, change in phased]


def _delay_changes(
    state: ConsoleState, output: int, add_ms: float, policy: WritePolicy
) -> list[Change]:
    laws = policy.laws
    current = state.delays[output]
    wanted_ms = current.effective_ms + add_ms
    new_ms = min(max(wanted_ms, laws.min_delay_ms), policy.limits.sub_max_delay_ms)
    new_norm = laws.delay_wire(new_ms)
    current_norm = laws.delay_wire(current.delay_ms)
    changes = []
    # Time first, then the switch: switching on first would put the old stored time (up to
    # 500 ms) into the signal path for a moment, or for good if the plan stopped in between.
    if abs(new_norm - current_norm) > _SAME_DELAY_NORM:
        changes.append(
            Change(
                f"/outputs/main/{output:02d}/delay/time",
                current_norm,
                new_norm,
                f"{current.delay_ms:.1f} ms",
                f"{laws.delay_ms(new_norm):.1f} ms",
            )
        )
    if not current.on:
        changes.append(Change(f"/outputs/main/{output:02d}/delay/on", 0, 1, "OFF", "ON"))
    return changes


def alignment_changes(  # noqa: PLR0913 - one call describes the whole alignment package
    state: ConsoleState,
    roles: Roles,
    policy: WritePolicy,
    *,
    delay_ms: float,
    polarity: int,
    fader_change_db: float = 0.0,
) -> list[Change]:
    """Writes for an alignment: output delays, sub polarity and sub fader.

    A positive ``delay_ms`` delays the outputs of the sub; a negative one delays the outputs of
    the tops and of the other speakers fed with the same programme.

    Args:
        state: Console state.
        roles: Which matrices are sub, tops and others.
        policy: Write policy; every change is validated in the ``sub`` scope.
        delay_ms: Sub delay from the alignment search.
        polarity: +1 to leave the sub polarity, -1 to flip it.
        fader_change_db: Sub fader change; the result never exceeds the policy's fader limit.

    Returns:
        The writes: delays, then polarity, then fader.

    Raises:
        UnsafeWrite: A change is outside the policy (for example an unverified delay write).
    """
    laws = policy.laws
    changes: list[Change] = []
    if delay_ms > MIN_DELAY_MS:
        delayed, add_ms = roles.sub, delay_ms
    elif delay_ms < -MIN_DELAY_MS:
        delayed, add_ms = (*roles.mains, *roles.others), -delay_ms
    else:
        delayed, add_ms = (), 0.0
    for matrix in delayed:
        for output in state.matrices[matrix].outputs:
            changes += _delay_changes(state, output, add_ms, policy)
    if polarity == INVERTED_POLARITY:
        for matrix in roles.sub:
            inverted = state.matrices[matrix].invert
            changes.append(
                Change(
                    f"/mtx/{matrix:02d}/preamp/invert",
                    int(inverted),
                    int(not inverted),
                    "INV" if inverted else "norm",
                    "norm" if inverted else "INV",
                )
            )
    if abs(fader_change_db) >= MIN_FADER_CHANGE_DB:
        for matrix in roles.sub:
            level_db = state.matrices[matrix].fader_db
            new_db = min(level_db + fader_change_db, policy.limits.sub_max_fader_db)
            new_norm = laws.fader_wire(new_db)
            changes.append(
                Change(
                    f"/mtx/{matrix:02d}/mix/fader",
                    laws.fader_wire(level_db),
                    new_norm,
                    laws.fader_display(level_db),
                    laws.fader_display(laws.fader_db(new_norm)),
                )
            )
    return [policy.checked_change(change, WriteScope.SUB) for change in changes]
