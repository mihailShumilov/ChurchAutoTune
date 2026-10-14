"""From a saved proposal to ordered steps, treating the proposal file as untrusted (SPEC S12).

Only packages the person chose, that are not suggestions and whose required packages come
before them in the same run, are written. Every package starts from a fresh read of the console:
if what the console has now differs from the package's "before", the package is refused and the
person re-measures (the default the owner chose). The steps come from ``safety.order``, so no
intermediate state is louder than before or after; turning them into console writes is the
driver's job.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace

from churchtune.domain.changes import WireValue
from churchtune.domain.console import ConsoleState
from churchtune.domain.eq import Band, SystemEq
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.errors import UnsafeWrite
from churchtune.safety.cut_filters import PathRole, check_cut_changes
from churchtune.safety.laws import ValueLaws
from churchtune.safety.order import Chains, Step, StripState, order_steps
from churchtune.safety.plan import same_step
from churchtune.storage.models import (
    ChangeModel,
    EqModel,
    PackageModel,
    PackagesFile,
    PathRefModel,
    ResultModel,
)

MAIN = SignalRef(SourceKind.MAIN_L)
CROSSOVER = "crossover"
_BAND_KINDS = ("type", "f", "g", "q")


def strip_states(fresh: ConsoleState) -> dict[SignalRef, StripState]:
    """Main LR and every matrix as strip states, from a fresh read."""
    states = {MAIN: StripState(fresh.main_eq)}
    for number, matrix in fresh.matrices.items():
        states[SignalRef(SourceKind.MATRIX, number)] = StripState(
            matrix.eq, matrix.fader_db, matrix.on
        )
    return states


def speaker_chains(states: Mapping[SignalRef, StripState]) -> Chains:
    """Every matrix behind Main LR, and Main LR alone.

    Counting Main LR in front of every matrix is the strict choice: a path that does not really
    go through it only gets more constraints, never fewer.
    """
    chains: dict[str, tuple[SignalRef, ...]] = {"main": (MAIN,)}
    for ref in states:
        if ref.kind is SourceKind.MATRIX:
            chains[ref.label()] = (MAIN, ref)
    return chains


def ref_of(path: PathRefModel) -> SignalRef:
    """The strip a stored path names."""
    return SignalRef(SourceKind(path.kind), path.number)


def chosen_packages(stored: PackagesFile) -> list[PackageModel]:
    """The chosen packages in order, each after what it requires.

    A chosen suggestion (advice set by hand: output delay, polarity) counts for what requires
    it; the program never writes it (``writable_packages``) and shows it as a task right after
    the writes, before the check measurement.

    Raises:
        UnsafeWrite: A chosen package requires one that is not chosen before it.
    """
    chosen: list[PackageModel] = []
    done: set[str] = set()
    for package in stored.packages:
        if not package.selected:
            continue
        missing = [kind for kind in package.requires if kind not in done]
        if missing:
            raise UnsafeWrite(
                f"package {package.kind} needs {', '.join(missing)} applied first",
                package=package.kind,
            )
        chosen.append(package)
        done.add(package.kind)
    return chosen


def is_advice(package: PackageModel) -> bool:
    """A suggestion, or a package with output delays or polarity: set by hand (ADR 0010)."""
    return bool(package.suggest_only or package.delays or package.polarities or package.delay_ms)


def writable_packages(
    packages: Sequence[PackageModel],
) -> tuple[list[PackageModel], list[PackageModel]]:
    """The packages the program writes, and the advice it shows as a task after the writes.

    A package designed on top of an advice package is still written: its "before" is checked
    against the console like any other, and the check measurement judges the result with the
    advice set by hand.
    """
    kept = [p for p in packages if not is_advice(p)]
    advice = [p for p in packages if is_advice(p)]
    return kept, advice


def same_band(laws: ValueLaws, a: Band, b: Band) -> bool:
    """True when two bands are the same on the console's grid."""
    wire_a, wire_b = laws.band_wire_values(a)[0], laws.band_wire_values(b)[0]
    return all(same_step(laws, kind, wire_a[kind], wire_b[kind]) for kind in _BAND_KINDS)


def same_eq(laws: ValueLaws, a: SystemEq, b: SystemEq) -> bool:
    """True when two EQ blocks are the same on the console's grid."""
    if a.on != b.on:
        return False
    return all(same_band(laws, x, y) for x, y in zip(a.bands, b.bands, strict=True))


def check_eq_change(  # noqa: PLR0913, PLR0917 - every input of the rule is explicit
    kind: str,
    label: str,
    role: PathRole,
    now: SystemEq,
    wanted: SystemEq,
    laws: ValueLaws,
) -> None:
    """Refuse what no package may do to an EQ block.

    A package never switches an EQ on or off: a block that was off may hold bands that were
    never checked (a +15 dB leftover), and switching it off again could drop a cut filter. A
    cut filter is set or moved only by the crossover package, within ``safety.cut_filters``.

    Raises:
        UnsafeWrite: The package does one of these.
    """
    if now.on != wanted.on:
        raise UnsafeWrite(
            f"package {kind} would switch the EQ of {label} on or off", eq_switch=label
        )
    if kind == CROSSOVER:
        check_cut_changes(
            label, role, now, wanted, lambda a, b: same_band(laws, a, b), may_remove=False
        )
        return
    for old, new in zip(now.bands, wanted.bands, strict=True):
        if (not old.adjustable or not new.adjustable) and not same_band(laws, old, new):
            raise UnsafeWrite(
                f"only the crossover package may change the cut filter in band {new.band} of "
                f"{label}",
                cut_filter=label,
            )


def role_of(ref: SignalRef, roles: Mapping[SignalRef, PathRole]) -> PathRole:
    """What a strip feeds: Main LR is the common path, a matrix as the fresh read says."""
    if ref.kind is SourceKind.MAIN_L:
        return PathRole.MAIN
    return roles.get(ref, PathRole.OTHER)


@dataclass(frozen=True, slots=True)
class PackageTarget:
    """Where one package takes the console, and what no longer matches its "before".

    Attributes:
        kind: Package kind.
        before: Fresh state of every strip it touches.
        after: Wanted state of the same strips.
        mismatches: Strips whose fresh state differs from the package's "before" (labels).
    """

    kind: str
    before: dict[SignalRef, StripState]
    after: dict[SignalRef, StripState]
    mismatches: tuple[str, ...]


def package_target(
    package: PackageModel,
    fresh: Mapping[SignalRef, StripState],
    laws: ValueLaws,
    roles: Mapping[SignalRef, PathRole],
) -> PackageTarget:
    """The fresh and wanted states of the strips one package changes.

    Raises:
        UnsafeWrite: The package names a strip the console does not have.
    """
    before: dict[SignalRef, StripState] = {}
    after: dict[SignalRef, StripState] = {}
    mismatches: list[str] = []

    def strip(ref: SignalRef) -> StripState:
        if ref not in fresh:
            raise UnsafeWrite(f"the console has no {ref.label()}", strip=ref.label())
        return after.get(ref, fresh[ref])

    for path_eq in package.eq:
        ref = ref_of(path_eq.path)
        current = strip(ref)
        before.setdefault(ref, fresh[ref])
        if not same_eq(laws, fresh[ref].eq, _eq(path_eq.before)):
            mismatches.append(ref.label())
        role = role_of(ref, roles)
        check_eq_change(package.kind, ref.label(), role, current.eq, _eq(path_eq.after), laws)
        after[ref] = replace(current, eq=_eq(path_eq.after))
    for level in package.levels:
        ref = ref_of(level.path)
        current = strip(ref)
        before.setdefault(ref, fresh[ref])
        fresh_wire = laws.fader_wire(fresh[ref].fader_db)
        if not same_step(laws, "fader", fresh_wire, laws.fader_wire(level.before_db)):
            mismatches.append(ref.label())
        after[ref] = replace(current, fader_db=level.after_db)
    return PackageTarget(package.kind, before, after, tuple(dict.fromkeys(mismatches)))


def _eq(model: EqModel) -> SystemEq:
    return model.to_domain()


def ordered_steps(
    target: PackageTarget,
    fresh: Mapping[SignalRef, StripState],
    fs_hz: float,
    mutable: Collection[SignalRef] = (),
) -> tuple[Step, ...]:
    """The package's steps in a never-louder order, checked on every speaker path.

    ``mutable`` are the speaker matrices that may be muted briefly for a change that has no
    never-louder order otherwise (``safety.order``).

    Raises:
        UnsafeWrite: The fresh state no longer matches the package, or no safe order exists.
    """
    if target.mismatches:
        raise UnsafeWrite(
            "the console changed since the measurement: " + ", ".join(target.mismatches),
            mismatches=", ".join(target.mismatches),
        )
    whole_before = dict(fresh)
    whole_after = {**whole_before, **target.after}
    chains = speaker_chains(whole_before)
    return order_steps(whole_before, whole_after, chains, fs_hz, mutable=mutable)


def combined_target(
    packages: Sequence[PackageModel],
    fresh: Mapping[SignalRef, StripState],
    laws: ValueLaws,
    roles: Mapping[SignalRef, PathRole],
) -> PackageTarget:
    """Where the chosen packages take the console together.

    The packages were designed one on top of the other (the EQ starts from the crossover), so
    each "before" is compared with the fresh state plus the packages written before it.

    Raises:
        UnsafeWrite: A package names a strip the console does not have.
    """
    current = dict(fresh)
    after: dict[SignalRef, StripState] = {}
    mismatches: list[str] = []
    for package in packages:
        target = package_target(package, current, laws, roles)
        mismatches.extend(f"{package.kind}: {label}" for label in target.mismatches)
        current.update(target.after)
        after.update(target.after)
    before = {ref: fresh[ref] for ref in after}
    kinds = "+".join(p.kind for p in packages)
    return PackageTarget(kinds, before, after, tuple(mismatches))


def collapsed_changes(changes: Sequence[ChangeModel]) -> list[ChangeModel]:
    """One entry per path: the old value of its first write, the new value of its last.

    A never-louder order may write a parameter twice (a gain to 0 dB, then to its value); the
    console then holds the last value (or, after a stop, an earlier one: see
    ``written_values``) and the way back is the first old one.
    """
    first: dict[str, ChangeModel] = {}
    last: dict[str, ChangeModel] = {}
    for change in changes:
        first.setdefault(change.path, change)
        last[change.path] = change
    collapsed = []
    for path, start in first.items():
        end = last[path]
        if end.scope != start.scope:
            raise UnsafeWrite(f"undo file names {path} in two scopes", path=path)
        collapsed.append(
            start.model_copy(update={"new_value": end.new_value, "new_display": end.new_display})
        )
    return collapsed


def written_values(
    changes: Sequence[ChangeModel], results: Sequence[ResultModel] = ()
) -> dict[str, list[WireValue]]:
    """The values a plan may have left on the console, per path.

    With the results of the undo file: the last confirmed value of each path, plus the values
    of later writes that went out but did not read back (the console may hold either). A
    value the plan wrote and then replaced (a matrix muted and switched back on) is not one of
    them: finding it now means someone set it by hand. Without results (an older file), every
    value the plan meant to write counts.
    """
    written: dict[str, list[WireValue]] = {}
    if not results:
        for change in changes:
            written.setdefault(change.path, []).append(change.new_value)
        return written
    for change, result in zip(changes, results, strict=False):
        if result.ok:
            written[change.path] = [change.new_value]
        elif result.sent:
            written.setdefault(change.path, []).append(change.new_value)
    return written
