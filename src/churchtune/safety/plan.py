"""Turning a target EQ into an ordered, validated list of parameter writes."""

from churchtune.domain.changes import Change, WireValue, WriteScope
from churchtune.domain.eq import SystemEq
from churchtune.errors import UnsafeWrite
from churchtune.safety.laws import ValueLaws
from churchtune.safety.policy import SWITCH_KINDS, WritePolicy

MAIN_EQ_PATH = "/main/st/eq"
_INT_KINDS = SWITCH_KINDS | {"type"}
# Gain goes first when it goes down and last when it goes up, so no intermediate state is louder.
_ORDER_GAIN_DOWN = ("g", "type", "f", "q")
_ORDER_GAIN_UP = ("type", "f", "q", "g")
_EPS = 1e-6


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def same_step(laws: ValueLaws, kind: str, a: WireValue, b: WireValue) -> bool:
    """Compare two wire values of one parameter kind by grid step (or as ints for switches).

    Args:
        laws: The console's value encoding.
        kind: Last path segment of the parameter (``type``, ``on``, ``f``, ``g``, ...).
        a: First value.
        b: Second value.
    """
    if kind in _INT_KINDS:
        # Switches and types are integers on the wire; a float or bool is never "the same".
        return _is_int(a) and _is_int(b) and a == b
    return laws.step_index(kind, float(a)) == laws.step_index(kind, float(b))


def plan_changes(current: SystemEq, target: SystemEq, policy: WritePolicy) -> list[Change]:
    """Diff two Main LR EQ states into an ordered list of writes.

    Only parameters whose grid step differs are written. Per band, gain is written first when it
    goes down and last when it goes up. Switching the EQ on is the last write of the plan, and is
    accepted only if every band of the target passes the boost limit, so a band that was left
    boosted while the EQ was off is never switched in.

    Args:
        current: EQ as read from the console.
        target: EQ to reach.
        policy: Write policy; every resulting change is validated in the ``main`` scope.

    Returns:
        The writes, in the order they must be sent.

    Raises:
        UnsafeWrite: The target switches the EQ off, touches or creates a cut-filter band, or
            asks for a boost above the limit (even when the gain itself does not change).
    """
    laws = policy.laws
    changes: list[Change] = []
    if current.on and not target.on:
        raise UnsafeWrite("switching the Main LR EQ off is refused (it may hold a high-pass)")
    switch_on = target.on and not current.on
    if switch_on:
        for band in target.bands:
            band_gain_db = laws.gain_db(float(laws.band_wire_values(band)[0]["g"]))
            if band.adjustable and band_gain_db > policy.limits.main_max_boost_db + _EPS:
                raise UnsafeWrite(
                    f"band {band.band} holds {band_gain_db:+.2f} dB; refusing to switch the EQ on "
                    f"with a boost above +{policy.limits.main_max_boost_db} dB"
                )
    for cur, tgt in zip(current.bands, target.bands, strict=True):
        cur_values, cur_display = laws.band_wire_values(cur)
        tgt_values, tgt_display = laws.band_wire_values(tgt)
        differing = [
            k for k in _ORDER_GAIN_UP if not same_step(laws, k, cur_values[k], tgt_values[k])
        ]
        if not differing:
            continue
        if not cur.adjustable:
            raise UnsafeWrite(
                f"band {cur.band} is a cut filter ({cur.type}); the tool never changes it"
            )
        if not tgt.adjustable:
            raise UnsafeWrite(f"band {tgt.band}: target type {tgt.type} is a cut filter; refused")
        target_gain_db = laws.gain_db(float(tgt_values["g"]))
        if target_gain_db > policy.limits.main_max_boost_db + _EPS:
            raise UnsafeWrite(
                f"band {tgt.band}: boost {tgt.gain_db:+.2f} dB > "
                f"+{policy.limits.main_max_boost_db} dB refused"
            )
        order = _ORDER_GAIN_DOWN if tgt_values["g"] <= cur_values["g"] else _ORDER_GAIN_UP
        changes.extend(
            Change(
                f"{MAIN_EQ_PATH}/{cur.band}/{k}",
                cur_values[k],
                tgt_values[k],
                cur_display[k],
                tgt_display[k],
            )
            for k in order
            if k in differing
        )
    if switch_on:
        # Last: until here the EQ block stays out of the signal path.
        changes.append(Change(f"{MAIN_EQ_PATH}/on", 0, 1, "OFF", "ON"))
    for change in changes:
        policy.validate(change.path, change.new_value, WriteScope.MAIN)
    return changes
