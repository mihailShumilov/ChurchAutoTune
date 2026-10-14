"""Steps S0-S5 (SPEC §7): venue, Mac check, connection, console read, topology, equipment.

Nothing here makes a sound or writes to the console.
"""

from dataclasses import dataclass

from churchtune.domain.snapshot import ConsoleSnapshot
from churchtune.domain.text import slugify
from churchtune.domain.topology import TopologyGuess, output_key
from churchtune.errors import ChurchTuneError
from churchtune.mixers.base import MixerDriver
from churchtune.storage.files import read_snapshot, read_wizard_state, write_snapshot
from churchtune.storage.models import HallSizeModel, SnapshotFile, WizardStateFile
from churchtune.wizard.labels import issue_args, output_text, signal_text
from churchtune.wizard.protocol import Option, Table, Text
from churchtune.wizard.services import WizardContext
from churchtune.wizard.state import Outcome
from churchtune.wizard.ui import ask_text, choose, confirm, info, kebab, t, task

NEW_VENUE = "__new__"
GOALS = ("full", "verify", "sub", "report")
MEASURING_GOALS = ("full", "verify", "sub")
ROLES = ("top_l", "top_r", "top_mono", "sub", "fill", "delay", "zone", "monitor", "unused")
MIC_MODELS = ("ECM8000", "dbx RTA-M", "Dayton EMM-6", "Sonarworks XREF20", "other")
MAX_MICS = 8
LOW_CONFIDENCE = 0.6
MIN_HALL_M, MAX_HALL_M = 3.0, 200.0


# ------------------------------------------------------------------ shared helpers
def ensure_driver(ctx: WizardContext) -> MixerDriver:
    """The connected console; reconnects after a resume.

    Raises:
        ChurchTuneError: No console address is known or the console does not answer.
    """
    if ctx.driver is None:
        state = ctx.box.state
        if state.console_host is None:
            raise ChurchTuneError("no console address in this session", step="connect")
        ctx.driver = ctx.services.console.connect(
            state.console_host, state.console_port or ctx.services.default_port
        )
    return ctx.driver


def load_snapshot(ctx: WizardContext) -> ConsoleSnapshot:
    """The snapshot of this session, parsed with the current parser.

    Raises:
        ChurchTuneError: The session has no snapshot yet.
    """
    path = ctx.box.state.snapshot_file
    if path is None:
        raise ChurchTuneError("no console snapshot in this session", step="read")
    stored = read_snapshot(ctx.services.workspace.root / path)
    return ensure_driver(ctx).parse_snapshot(stored.identity(), stored.raw, stored.taken_at)


def measuring(state: WizardStateFile) -> bool:
    """True for goals that measure the hall."""
    return state.goal in MEASURING_GOALS


def venue_name(ctx: WizardContext, slug: str) -> str:
    """The name the person gave a known venue (from its newest session), or its slug."""
    for session in reversed(ctx.services.workspace.sessions_of(slug)):
        try:
            name = read_wizard_state(session).venue_name
        except (ValueError, OSError):
            continue
        if name:
            return name
    return slug


# ------------------------------------------------------------------ S0
@dataclass(frozen=True, slots=True)
class Welcome:
    """S0: venue and goal."""

    id: str = "welcome"
    title: str = "step-welcome-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:  # noqa: ARG002 - always
        """Always the first step."""
        return True

    def run(self, ctx: WizardContext) -> Outcome:
        """Pick or name the venue, then the goal."""
        names = {slug: venue_name(ctx, slug) for slug in ctx.services.workspace.venues()}
        options = [
            Option(slug, t("step-welcome-venue-known", venue=name)) for slug, name in names.items()
        ]
        options.append(Option(NEW_VENUE, t("step-welcome-venue-new"), recommended=not names))
        choice = choose(ctx.prompter, [t("step-welcome-what"), t("step-welcome-venue")], options)
        if choice == NEW_VENUE:
            name = ""
            while not name.strip():
                name = ask_text(ctx.prompter, t("step-welcome-venue-name"))
        else:
            name = names[choice]
        goal = choose(
            ctx.prompter,
            [t("step-welcome-goal")],
            [Option(g, t(f"step-welcome-goal-{g}"), recommended=g == "full") for g in GOALS],
        )
        slug = slugify(name)
        ctx.box.update(venue=slug, venue_name=name, goal=goal)
        ctx.session_dir = ctx.services.workspace.session_dir(slug, ctx.services.now(), "baseline")
        return Outcome.DONE


# ------------------------------------------------------------------ S1
@dataclass(frozen=True, slots=True)
class MacCheck:
    """S1: dev-safe notice and the audio device."""

    id: str = "mac"
    title: str = "step-mac-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal but the report needs the Mac."""
        return state.goal != "report"

    def run(self, ctx: WizardContext) -> Outcome:
        """Show OK / WARN / FAIL per check; a FAIL stops the step."""
        rows: list[tuple[Text, ...]] = []
        if ctx.services.dev_safe:
            rows.append((t("check-warn"), t("step-mac-dev-safe")))
        devices = ctx.services.audio.devices()
        best = devices[0] if devices else None
        if best is None:
            rows.append((t("check-fail"), t("step-mac-no-audio")))
        elif best.problems:
            problems = tuple(t(f"audio-problem-{kebab(p)}") for p in best.problems)
            rows.append(
                (t("check-warn"), t("step-mac-audio-problems", name=best.name, problems=problems))
            )
        else:
            rows.append((t("check-ok"), t("step-mac-audio-ok", name=best.name)))
        table = Table((t("check-result"), t("check-what")), tuple(rows))
        info(ctx.prompter, t("step-mac-what"), table=table)
        if best is None:
            info(ctx.prompter, t("step-mac-fix-audio"))
            return Outcome.QUIT
        return Outcome.DONE


# ------------------------------------------------------------------ S2
@dataclass(frozen=True, slots=True)
class Connect:
    """S2: find the console and check the link."""

    id: str = "connect"
    title: str = "step-connect-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal but the report talks to the console."""
        return state.goal != "report"

    @staticmethod
    def _choose_console(ctx: WizardContext, known: list[tuple[str, int]]) -> tuple[str, int] | None:
        """The console to connect: found and confirmed, picked, or typed; None to quit."""
        searching = t("step-connect-searching")
        ctx.prompter.progress(searching, 0, 1)
        found = ctx.services.console.discover(known)
        ctx.prompter.progress(searching, 1, 1)
        if not found:
            return Connect._typed(ctx, t("step-connect-none"))
        if len(found) == 1:
            identity = found[0].identity
            found_text = t(
                "step-connect-found",
                model=identity.model,
                name=identity.name,
                host=identity.host,
                firmware=identity.firmware,
            )
            if confirm(ctx.prompter, found_text):
                return (identity.host, found[0].port)
            return Connect._typed(ctx, t("step-connect-not-this-one"))
        options = [
            Option(
                f"{c.identity.host}:{c.port}",
                t(
                    "step-connect-option",
                    model=c.identity.model,
                    name=c.identity.name,
                    host=c.identity.host,
                ),
            )
            for c in found
        ]
        host, _, port = choose(ctx.prompter, [t("step-connect-several")], options).partition(":")
        return (host, int(port))

    @staticmethod
    def _typed(ctx: WizardContext, why: Text) -> tuple[str, int]:
        typed = ask_text(ctx.prompter, why, t("step-connect-enter-ip"))
        host, _, port = typed.strip().partition(":")
        return (host, int(port) if port.isdigit() else ctx.services.default_port)

    def run(self, ctx: WizardContext) -> Outcome:
        """Discover, confirm or pick, connect, measure the link."""
        state = ctx.box.state
        known = list(ctx.services.known_consoles)
        if state.console_host:
            known.insert(0, (state.console_host, state.console_port or ctx.services.default_port))
        while True:  # Back and Save & quit leave through the prompts
            chosen = self._choose_console(ctx, known)
            if chosen is None:
                return Outcome.QUIT
            if not chosen[0]:
                continue
            try:
                driver = ctx.services.console.connect(*chosen)
            except ChurchTuneError:
                info(ctx.prompter, t("err-console-unreachable"), t("step-connect-fix"))
                continue
            quality = driver.link_quality(20, 0.5)
            if quality.median_ms is not None:
                median_ms = quality.median_ms
                break
            info(ctx.prompter, t("err-console-unreachable"), t("step-connect-fix"))
        ctx.driver = driver
        model = ctx.driver.model
        body = [
            t(
                "step-connect-link",
                loss_pct=round(100 * quality.loss),
                median_ms=round(median_ms, 1),
            )
        ]
        if not model.capabilities.verified_firmware:
            body.append(t("step-connect-firmware-unverified", firmware=model.identity.firmware))
        info(ctx.prompter, *body)
        ctx.box.update(
            console_host=chosen[0], console_port=chosen[1], console_model=model.identity.model
        )
        return Outcome.DONE


# ------------------------------------------------------------------ S3
@dataclass(frozen=True, slots=True)
class ReadConsole:
    """S3: the full read-only snapshot, saved before anything else; save a scene."""

    id: str = "read"
    title: str = "step-read-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal but the report."""
        return state.goal != "report"

    def run(self, ctx: WizardContext) -> Outcome:
        """Read with progress, store, ask for a scene save."""
        driver = ensure_driver(ctx)
        label = t("step-read-progress")
        snapshot = driver.read_snapshot(
            lambda done, total: ctx.prompter.progress(label, done, total)
        )
        services = ctx.services
        path = write_snapshot(
            services.workspace,
            SnapshotFile.from_snapshot(snapshot),
            services.now(),
            ctx.box.state.venue,
        )
        relative = str(path.relative_to(services.workspace.root))
        ctx.box.update(snapshot_file=relative)
        body = [t("step-read-done", count=len(snapshot.raw))]
        if snapshot.unparsed:
            body.append(t("step-read-unparsed", count=len(snapshot.unparsed)))
        info(ctx.prompter, *body)
        task(ctx.prompter, t("step-read-save-scene", date=services.now().strftime("%Y-%m-%d")))
        return Outcome.DONE


# ------------------------------------------------------------------ S4
def topology_table(guess: TopologyGuess) -> Table:
    """The "output · path · name · role · confidence" table of a guess."""
    rows = tuple(
        (
            output_text(g.kind.value, g.number),
            signal_text(g.path),
            t("topology-name", name=g.name or "-"),
            t(f"role-{kebab(g.role.value)}"),
            t("topology-confidence", pct=round(100 * g.confidence)),
        )
        for g in guess.outputs
    )
    keys = ("output", "path", "name", "role", "confidence")
    headers = tuple(t(f"topology-h-{k}") for k in keys)
    return Table(headers, rows)


@dataclass(frozen=True, slots=True)
class Topology:
    """S4: the guessed speaker roles, confirmed or corrected by the person."""

    id: str = "topology"
    title: str = "step-topology-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Every goal but the report."""
        return state.goal != "report"

    def run(self, ctx: WizardContext) -> Outcome:
        """Show the guess and its issues; let the person correct roles one by one."""
        snapshot = load_snapshot(ctx)
        guess = ensure_driver(ctx).infer_topology(snapshot)
        issues = [Text(f"issue-{kebab(i.code)}", issue_args(i.params)) for i in guess.issues]
        roles = {output_key(g.kind.value, g.number): g.role.value for g in guess.outputs}
        variant = t(f"variant-{kebab(guess.variant.value)}")
        info(ctx.prompter, t("step-topology-what"), variant, *issues, table=topology_table(guess))
        must_ask = [
            g for g in guess.outputs if g.confidence < LOW_CONFIDENCE and g.role.value != "unused"
        ]
        if must_ask or not confirm(ctx.prompter, t("step-topology-correct")):
            for g in must_ask or guess.outputs:
                key = output_key(g.kind.value, g.number)
                roles[key] = choose(
                    ctx.prompter,
                    [
                        t(
                            "step-topology-ask-role",
                            output=output_text(g.kind.value, g.number),
                            path=signal_text(g.path),
                            name=g.name or "-",
                        )
                    ],
                    [
                        Option(r, t(f"role-{kebab(r)}"), recommended=r == g.role.value)
                        for r in ROLES
                    ],
                )
        ctx.box.update(roles=roles, variant=guess.variant.value)
        return Outcome.DONE


# ------------------------------------------------------------------ S5
def parse_hall_size(text: str) -> HallSizeModel | None:
    """``"14 x 22"`` (also ``14х22``, ``14,5 22``) as a hall size; None when it is not one."""
    parts = text.replace(",", ".").lower().replace("x", " ").replace("х", " ").split()
    if len(parts) != 2:  # noqa: PLR2004 - width and depth
        return None
    try:
        width, depth = (float(p) for p in parts)
    except ValueError:
        return None
    if not (MIN_HALL_M <= width <= MAX_HALL_M and MIN_HALL_M <= depth <= MAX_HALL_M):
        return None
    return HallSizeModel(width_m=width, depth_m=depth)


def _hall_size(ctx: WizardContext) -> HallSizeModel | None:
    """Ask until the answer is empty (the standard plan) or a valid size."""
    body = [t("step-equipment-hall")]
    while True:
        text = ask_text(ctx.prompter, *body, default="")
        if not text.strip():
            return None
        size = parse_hall_size(text)
        if size is not None:
            return size
        body = [t("step-equipment-hall-invalid"), t("step-equipment-hall")]


@dataclass(frozen=True, slots=True)
class Equipment:
    """S5: only what cannot be read: speakers, hall size, measurement microphones."""

    id: str = "equipment"
    title: str = "step-equipment-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that measure."""
        return measuring(state)

    def run(self, ctx: WizardContext) -> Outcome:
        """Ask speakers type, hall size (optional), microphone count and model."""
        active = choose(
            ctx.prompter,
            [t("step-equipment-what"), t("step-equipment-speakers")],
            [
                Option("active", t("step-equipment-active"), True),
                Option("passive", t("step-equipment-passive")),
            ],
        )
        hall = _hall_size(ctx)
        count = choose(
            ctx.prompter,
            [t("step-equipment-mic-count")],
            [
                Option(str(n), t("step-equipment-mics", count=n), recommended=n == 1)
                for n in range(1, MAX_MICS + 1)
            ],
        )
        model = choose(
            ctx.prompter,
            [t("step-equipment-mic-model")],
            [
                Option(
                    m,
                    t("step-equipment-model-other")
                    if m == "other"
                    else t("step-equipment-model", model=m),
                    recommended=m == "ECM8000",
                )
                for m in MIC_MODELS
            ],
        )
        ctx.box.update(
            speakers_active=active == "active", hall=hall, mic_count=int(count), mic_model=model
        )
        return Outcome.DONE
