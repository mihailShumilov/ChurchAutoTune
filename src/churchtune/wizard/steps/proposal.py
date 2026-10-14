"""S11 proposal screens: one screen per package, then the channel advice (SPEC S11).

Every package shows what changes (before → after), why, the modelled effect and the risk, and
the person keeps it or skips it. Nothing is written to the console here: the choice is saved in
the session's ``proposal.json`` for the apply step (M5).
"""

from churchtune.analysis.points import Analysis
from churchtune.design.channel_audit import Finding, audit_channels
from churchtune.design.packages import (
    DesignContext,
    EffectMetric,
    NoteCode,
    Package,
    PackageKind,
    build_packages,
)
from churchtune.domain.eq import Band
from churchtune.domain.snapshot import SignalRef, SourceKind
from churchtune.domain.topology import with_roles
from churchtune.storage.files import write_packages
from churchtune.wizard.labels import issue_args, output_text, signal_text
from churchtune.wizard.proposals import packages_file
from churchtune.wizard.protocol import Option, Table, Text
from churchtune.wizard.services import FS_HZ, WizardContext
from churchtune.wizard.steps.setup import ensure_driver, load_snapshot
from churchtune.wizard.ui import choose, info, kebab, t

KEEP, SKIP = "keep", "skip"


def band_text(band: Band) -> Text:
    """One EQ band in words: ``LR24 95 Hz`` or ``PEQ 480 Hz −4 dB Q 2.2``."""
    if not band.adjustable:
        return t("eq-band-filter", type=band.type.value, freq_hz=round(band.freq_hz))
    if band.gain_db == 0:
        return t("eq-band-flat")
    return t(
        "eq-band-peq",
        type=band.type.value,
        freq_hz=round(band.freq_hz),
        gain_db=band.gain_db,
        q=round(band.q, 1),
    )


def path_text(ref: SignalRef) -> Text:
    """A path in words; the Main LR strip is one block, not Main L."""
    if ref.kind is SourceKind.MAIN_L:
        return t("signal-main-lr")
    return signal_text(ref)


def _polarity(inverted: bool) -> Text:
    return t("polarity-inverted") if inverted else t("polarity-normal")


def _rows(package: Package) -> tuple[tuple[Text, ...], ...]:
    rows: list[tuple[Text, ...]] = []
    # Old corrections the crossover sets to 0 dB are one note line per path, not table rows.
    reset_noted = any(n.code == NoteCode.CROSSOVER_RESET for n in package.notes)
    for path in package.eq:
        for old, new in zip(path.before.bands, path.after.bands, strict=True):
            if old != new and not (reset_noted and new.adjustable and new.gain_db == 0):
                where = t("eq-path-band", path=path_text(path.path), band=new.band)
                rows.append((where, band_text(old), band_text(new)))
    rows.extend(
        (
            t("delay-of", output=output_text("main", delay.output)),
            t("value-ms", ms=delay.before_ms),
            t("value-ms", ms=delay.after_ms),
        )
        for delay in package.delays
    )
    rows.extend(
        (
            t("fader-of", path=path_text(level.path)),
            t("value-db", db=level.before_db),
            t("value-db", db=level.after_db),
        )
        for level in package.levels
    )
    rows.extend(
        (
            t("polarity-of", path=path_text(polarity.path)),
            _polarity(polarity.before),
            _polarity(polarity.after),
        )
        for polarity in package.polarities
    )
    return tuple(rows)


def effect_text(package: Package) -> Text | None:
    """The expected effect in words (the sub level says louder or quieter by how much)."""
    effect = package.effect
    if effect is None:
        return None
    if effect.metric == EffectMetric.SUB_MINUS_TOPS:
        db = round(abs(effect.before_db), 1)
        if effect.before_db >= 0:
            return t("effect-sub-louder", db=db)
        return t("effect-sub-quieter", db=db)
    return t(
        f"effect-{effect.metric}",
        before_db=round(effect.before_db, 1),
        after_db=round(effect.after_db, 1),
    )


def package_screen(ctx: WizardContext, package: Package) -> bool:
    """Show one package on one screen; True when the person adds it to the plan."""
    kind = kebab(package.kind.value)
    title = t(f"package-{kind}")
    body = [title, t(f"package-{kind}-why")]
    body.extend(Text(f"note-{n.code}", issue_args(n.params)) for n in package.notes)
    effect = effect_text(package)
    if effect is not None:
        body.append(effect)
    body.append(t(f"risk-{package.risk.value}"))
    if package.suggest_only:
        body.append(t("package-suggest-only"))
    rows = _rows(package)
    table = Table((t("change-what"), t("change-before"), t("change-after")), rows) if rows else None
    keep = t("package-keep-advice") if package.suggest_only else t("package-keep")
    options = [Option(KEEP, keep, recommended=True), Option(SKIP, t("package-skip"))]
    body.append(t("package-question", package=title))
    return choose(ctx.prompter, body, options, table=table) == KEEP


def audit_screen(ctx: WizardContext, findings: tuple[Finding, ...]) -> None:
    """The channel advice, if there is any."""
    if not findings:
        return
    lines = [
        t(
            f"audit-{f.code.value}",
            channels=", ".join(str(c) for c in f.channels),
            count=len(f.channels),
            freq_hz=f.freq_hz or 0,
            gain_db=f.gain_db or 0.0,
        )
        for f in findings
    ]
    info(ctx.prompter, t("audit-what"), *lines, t("audit-by-ear"))


def propose(ctx: WizardContext, analysis: Analysis) -> None:
    """Design the packages, let the person choose, show the advice, save the proposal.

    Packages are shown in the order of application. When one is left out, the later ones are
    designed again without it, so nothing in the plan depends on a change that will not happen.
    """
    snapshot = load_snapshot(ctx)
    driver = ensure_driver(ctx)
    topology = with_roles(driver.infer_topology(snapshot), ctx.box.state.roles)
    context = DesignContext(analysis, snapshot, topology, driver.eq_grid, FS_HZ)
    left_out: set[PackageKind] = set()
    packages = build_packages(context)
    intro = (
        [t("proposal-what", count=len(packages.packages))]
        if packages.packages
        else [t("proposal-nothing")]
    )
    notes = [Text(f"note-{n.code}", issue_args(n.params)) for n in packages.notes]
    if any(n.code == NoteCode.JUNCTION_NOT_MEASURED for n in packages.notes):
        notes.append(t("glossary-crossover"))  # the word appears here first
    info(ctx.prompter, *intro, *notes)
    decided: dict[PackageKind, tuple[Package, bool]] = {}
    for kind in PackageKind:
        package = packages.of(kind)
        if package is None:
            continue
        if package_screen(ctx, package):
            decided[kind] = (package, True)
        else:
            decided[kind] = (package, False)
            left_out.add(kind)
            packages = build_packages(context, left_out)
    system_eq = packages.of(PackageKind.SYSTEM_EQ)
    system_cuts = [
        band.freq_hz
        for path in (system_eq.eq if system_eq else ())
        for band in path.after.bands
        if band.adjustable and band.gain_db < 0
    ]
    findings = audit_channels(
        snapshot, lambda source: driver.headamp_of(snapshot, source), system_cuts
    )
    audit_screen(ctx, findings)
    stored = packages_file(
        packages.fc_hz,
        [decided[k] for k in PackageKind if k in decided],
        packages.notes,
        findings,
        session=ctx.session_dir.name,
        created=ctx.services.now().isoformat(timespec="seconds"),
    )
    path = write_packages(ctx.session_dir, stored)
    ctx.box.update(proposal_file=str(path.relative_to(ctx.services.workspace.root)))
