"""Steps S6-S10 (SPEC §7): microphones, playback path, level, calibration, points.

Every sound is announced first and stops on Ctrl+C / Space (SPEC §9.4). Before the first sound
of a run the console is read again: a measurement microphone whose channel still reaches Main or
a send would feed back, so no sound plays until those channels are muted (SPEC §7 S6).
"""

from dataclasses import dataclass

import numpy as np

from churchtune.analysis.response import GRID_HZ, magnitude_db
from churchtune.audio.base import MultiChannelBackend
from churchtune.domain.snapshot import ConsoleSnapshot, InputKind, UsbAudioMap
from churchtune.errors import MeasurementAborted
from churchtune.measure.calibration import (
    CalibrationError,
    MicSetCalibration,
    calibrate,
    mic_set_id,
)
from churchtune.measure.config import QualityGates
from churchtune.measure.level import (
    LevelRamp,
    RampReading,
    RampStop,
    SweepLevelSearch,
    SweepStop,
    find_sweep_level,
    run_level_ramp,
)
from churchtune.measure.mic_mapping import MappingOutcome, ScratchResult, find_scratch
from churchtune.measure.multi import RoundMeasurement, measure_round
from churchtune.measure.noise import measure_background
from churchtune.measure.path_check import check_path
from churchtune.measure.plan import PointPlan, ascii_map, plan_points
from churchtune.measure.rounds import Round, plan_rounds
from churchtune.storage.files import (
    read_plan,
    reusable_mic_calibration,
    save_point,
    write_mic_calibration,
    write_plan,
    write_session_mic_calibration,
)
from churchtune.storage.models import (
    CodedText,
    MicCalibrationFile,
    MicCalibrationModel,
    PointMeta,
    WizardStateFile,
)
from churchtune.wizard.proposals import plan_file, plan_from_file
from churchtune.wizard.protocol import Option, Table, Text
from churchtune.wizard.services import FS_HZ, WizardContext
from churchtune.wizard.state import Navigate, Outcome
from churchtune.wizard.steps.setup import ensure_driver, load_snapshot, measuring
from churchtune.wizard.ui import choose, info, kebab, t, task

# The scratch search listens in short takes, up to 15 s, and stops at the first clear answer.
LISTEN_TAKE_S = 3.0
LISTEN_TAKES = 5
COUNTDOWN_S = 3
MIN_RUN = 3  # three or more numbers in a row are written as a range
RETRY, SKIP, ACCEPT, PLAY, LATER = "retry", "skip", "accept", "play", "later"
GATES = QualityGates()
RAMP_FOUND = (RampStop.TARGET, RampStop.ABOVE_TARGET)


def ensure_backend(ctx: WizardContext) -> MultiChannelBackend:
    """The open audio device; opened on first use."""
    if ctx.backend is None:
        ctx.backend = ctx.services.audio.open(ctx.services.audio_name)
    return ctx.backend


def mic_text(state: WizardStateFile, usb: int) -> Text:
    """The name "Microphone 2 (USB input 11)", numbered in the order the mics were found."""
    number = state.mic_inputs.index(usb) + 1 if usb in state.mic_inputs else 0
    return t("common-mic", number=number, usb=usb)


def _numbers(numbers: list[int]) -> str:
    """Sorted numbers with runs shortened: ``1–7, 11, 12``."""
    parts: list[str] = []
    values = sorted(set(numbers))
    start = 0
    for index in range(1, len(values) + 1):
        if index < len(values) and values[index] == values[index - 1] + 1:
            continue
        first, last = values[start], values[index - 1]
        parts.append(
            f"{first}–{last}"
            if last - first >= MIN_RUN - 1
            else ", ".join(str(v) for v in values[start:index])
        )
        start = index
    return ", ".join(parts)


def open_mic_channels(
    snapshot: ConsoleSnapshot, usb: UsbAudioMap, usb_inputs: tuple[int, ...]
) -> list[int]:
    """Channels that listen to a measurement microphone and still reach Main or a send."""
    found: list[int] = []
    for usb_input in usb_inputs:
        entry = usb.input_info(usb_input)
        if entry is None:
            continue
        for number in entry.channels:
            channel = snapshot.channels.get(number)
            if channel is None or not channel.on:
                continue
            if channel.to_main or channel.to_mono or any(s.open for s in channel.sends):
                found.append(number)
    return sorted(set(found))


def ensure_mics_cannot_feed_back(ctx: WizardContext) -> None:
    """Read the console again; ask to mute measurement channels until none reaches the speakers.

    Done once per run, right before the first sound: the snapshot of S3 may be hours old and
    the person may not have muted what S6 asked for.
    """
    if ctx.mics_checked:
        return
    driver = ensure_driver(ctx)
    label = t("step-mics-rereading")
    while True:
        snapshot = driver.read_snapshot(
            lambda done, total: ctx.prompter.progress(label, done, total)
        )
        channels = open_mic_channels(
            snapshot, driver.usb_audio_map(snapshot), ctx.box.state.mic_inputs
        )
        if not channels:
            ctx.mics_checked = True
            return
        task(
            ctx.prompter,
            t("step-mics-still-open", count=len(channels), channels=_numbers(channels)),
            t("step-mics-why-mute"),
        )


def warn_before_sound(ctx: WizardContext, what: Text, *, countdown: bool = False) -> None:
    """The warning screen before a sound (SPEC §9.4); repeated until the person is ready.

    "Not yet" shows the warning again; Back and Save & quit leave through the prompt. The stop
    flag is cleared only here, right before the sound, and never once the UI is gone.

    Raises:
        Navigate: The person went back or quit, or the UI went away.
        MeasurementAborted: Stopped during the countdown.
    """
    ensure_mics_cannot_feed_back(ctx)
    options = [Option(PLAY, t("sound-play")), Option(LATER, t("sound-later"))]
    while choose(ctx.prompter, [what, t("sound-warning"), t("sound-stop-keys")], options) != PLAY:
        continue
    services = ctx.services
    if not services.reset_stop():
        raise Navigate(Outcome.QUIT)
    if countdown:
        label = t("sound-countdown")
        for left in range(COUNTDOWN_S, 0, -1):
            ctx.prompter.progress(label, COUNTDOWN_S - left, COUNTDOWN_S)
            services.sleep(1.0)
            if services.stop.is_set():
                raise MeasurementAborted("stopped during the countdown")
    ctx.prompter.progress(t("sound-playing"), 0, 1)


# ------------------------------------------------------------------ S6
def _listen(
    ctx: WizardContext, backend: MultiChannelBackend, number: int, found: list[int]
) -> ScratchResult:
    """Listen in takes until a clear answer or 15 s."""
    candidates = list(range(1, backend.input_count + 1))
    for take in range(LISTEN_TAKES):
        left = round((LISTEN_TAKES - take) * LISTEN_TAKE_S)
        label = t("step-mics-listening", number=number, seconds=left)
        ctx.prompter.progress(label, take, LISTEN_TAKES)
        # Silent: Space does not cut it, closing the app does.
        recording = backend.record_multi(LISTEN_TAKE_S, candidates, ctx.services.shutdown)
        samples = np.asarray(recording.samples, dtype=np.float64)
        result = find_scratch(samples, candidates, FS_HZ, found)
        if result.outcome is not MappingOutcome.NOTHING:
            break
    ctx.prompter.progress(
        t("step-mics-listening", number=number, seconds=0), LISTEN_TAKES, LISTEN_TAKES
    )
    return result


@dataclass(frozen=True, slots=True)
class Microphones:
    """S6: find each microphone's input by a scratch; check phantom power and open paths."""

    id: str = "mics"
    title: str = "step-mics-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that measure."""
        return measuring(state)

    @staticmethod
    def _map(ctx: WizardContext, backend: MultiChannelBackend) -> list[int]:
        found: list[int] = []
        while len(found) < ctx.box.state.mic_count:
            number = len(found) + 1
            task(ctx.prompter, t("step-mics-scratch", number=number))
            result = _listen(ctx, backend, number, found)
            if result.outcome is MappingOutcome.FOUND and result.usb_input is not None:
                found.append(result.usb_input)
                info(ctx.prompter, t("step-mics-found", number=number, usb=result.usb_input))
                continue
            options = [Option(RETRY, t("common-retry"), True)]
            if found:
                options.append(Option(SKIP, t("step-mics-stop-here")))
            message = t(f"step-mics-{kebab(result.outcome.value)}", usb=result.usb_input or 0)
            if choose(ctx.prompter, [message], options) == SKIP:
                break
        return found

    def run(self, ctx: WizardContext) -> Outcome:
        """Map, then check the console side of each microphone."""
        info(ctx.prompter, t("step-mics-what"), t("step-mics-plug"))
        found = self._map(ctx, ensure_backend(ctx))
        ctx.box.update(mic_inputs=tuple(found), mic_count=len(found))
        usb = ensure_driver(ctx).usb_audio_map(load_snapshot(ctx))
        notes: list[Text] = []
        to_mute: list[int] = []
        for usb_input in found:
            entry = usb.input_info(usb_input)
            mic = mic_text(ctx.box.state, usb_input)
            if entry is None or entry.source.kind in (InputKind.CARD, InputKind.UNKNOWN):
                notes.append(t("step-mics-not-preamp", mic=mic))
                continue
            if entry.headamp is not None and not entry.headamp.phantom:
                notes.append(t("step-mics-phantom-off", mic=mic, headamp=entry.headamp.index))
            to_mute.extend(entry.channels)
        if to_mute:
            channels = sorted(set(to_mute))
            notes.append(
                t("step-mics-mute-channels", count=len(channels), channels=_numbers(channels))
            )
        if notes:
            task(ctx.prompter, t("step-mics-console-checks"), *notes)
        ctx.mics_checked = False  # read the console again before the first sound
        return Outcome.DONE


# ------------------------------------------------------------------ S7
def _send_notes(snapshot: ConsoleSnapshot) -> list[Text]:
    """Playback channels with open bus sends, grouped by the buses they reach."""
    by_buses: dict[tuple[int, ...], list[int]] = {}
    for channel in snapshot.channels.values():
        if channel.source.kind is not InputKind.CARD:
            continue
        buses = tuple(sorted(s.target for s in channel.sends if s.open))
        if buses:
            by_buses.setdefault(buses, []).append(channel.number)
    return [
        t(
            "step-path-close-sends",
            count=len(channels),
            channels=_numbers(channels),
            buses=_numbers(list(buses)),
        )
        for buses, channels in by_buses.items()
    ]


@dataclass(frozen=True, slots=True)
class PlaybackPath:
    """S7: the computer's playback reaches Main, not the monitors; a quiet path test."""

    id: str = "path"
    title: str = "step-path-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that measure."""
        return measuring(state)

    def run(self, ctx: WizardContext) -> Outcome:
        """Check the playback channels in the snapshot, then play the quiet test."""
        snapshot = load_snapshot(ctx)
        playback = [ch for ch in snapshot.channels.values() if ch.source.kind is InputKind.CARD]
        notes: list[Text] = []
        if not playback:
            notes.append(t("step-path-no-playback"))
        notes.extend(
            t("step-path-assign", channel=ch.number)
            for ch in playback
            if not ch.on or not ch.to_main
        )
        notes.extend(_send_notes(snapshot))
        if notes:
            task(ctx.prompter, t("step-path-what"), t("step-path-console-checks"), *notes)
        warn_before_sound(ctx, t("step-path-test"))
        state = ctx.box.state
        results = check_path(
            ensure_backend(ctx), list(state.mic_inputs), FS_HZ, stop=ctx.services.stop
        )
        rows = tuple(
            (
                mic_text(state, r.usb_input),
                t("check-ok") if r.heard else t("check-fail"),
                t("step-path-latency", ms=round(r.latency_ms or 0.0, 1)),
            )
            for r in results
        )
        table = Table((t("common-microphone"), t("check-result"), t("step-path-h-latency")), rows)
        if all(r.heard for r in results):
            info(ctx.prompter, t("step-path-heard"), table=table)
            return Outcome.DONE
        info(ctx.prompter, t("step-path-not-heard"), table=table)
        return Outcome.RETRY


# ------------------------------------------------------------------ S8
@dataclass(frozen=True, slots=True)
class Level:
    """S8: background noise, the pink-noise level ramp and the sweep level."""

    id: str = "level"
    title: str = "step-level-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that measure."""
        return measuring(state)

    def run(self, ctx: WizardContext) -> Outcome:
        """Noise first (silence), then the ramp with live meters, then the sweep level.

        Only a found level, or the output limit the person accepts, leads to the trial sweeps:
        after clipping, a jump, no response or a stop the step starts again.
        """
        backend = ensure_backend(ctx)
        state = ctx.box.state
        inputs = list(state.mic_inputs)
        info(ctx.prompter, t("step-level-what"), t("step-level-quiet"))
        # Silent: Space does not cut it, closing the app does.
        noises = measure_background(backend, inputs, FS_HZ, stop=ctx.services.shutdown)
        hints = [
            t(f"noise-{h.value}", mic=mic_text(state, n.usb_input)) for n in noises for h in n.hints
        ]
        if hints:
            info(ctx.prompter, t("step-level-noise-hints"), *hints)
        warn_before_sound(ctx, t("step-level-ramp"))

        def on_step(_ramp: LevelRamp, reading: RampReading) -> None:
            levels = reading.levels_dbfs[: len(inputs)]
            ctx.prompter.meter({str(n): level for n, level in enumerate(levels, start=1)})

        ramp, reading = run_level_ramp(
            backend, inputs, inputs[0], FS_HZ, stop=ctx.services.stop, on_step=on_step
        )
        stop = ramp.stopped or RampStop.USER
        level = round(reading.mic_rms_dbfs, 1)
        info(ctx.prompter, t(f"step-level-ramp-{kebab(stop.value)}", level=level))
        if stop is RampStop.OUTPUT_LIMIT:
            again = choose(
                ctx.prompter,
                [t("step-level-not-target")],
                [Option(RETRY, t("common-retry"), True), Option(ACCEPT, t("step-level-continue"))],
            )
            if again == RETRY:
                return Outcome.RETRY
        elif stop not in RAMP_FOUND:
            return Outcome.RETRY
        search = find_sweep_level(
            backend,
            inputs,
            FS_HZ,
            reference=inputs[0],
            search=SweepLevelSearch(max_level_dbfs=ramp.output_dbfs),
            stop=ctx.services.stop,
        )
        outcome = search.stopped or SweepStop.OUTPUT_LIMIT
        if outcome is not SweepStop.FOUND:
            info(ctx.prompter, t(f"step-level-sweep-{kebab(outcome.value)}"))
            if outcome in (SweepStop.NO_RESPONSE, SweepStop.TOO_LOUD_AT_MIN):
                return Outcome.RETRY
        ctx.box.update(sweep_level_dbfs=search.level_dbfs)
        info(ctx.prompter, t("step-level-sweep", level=round(search.level_dbfs, 1)))
        return Outcome.DONE


# ------------------------------------------------------------------ S9
def _calibration_file(
    result: MicSetCalibration, set_id: str, model: str, now: str
) -> MicCalibrationFile:
    mics = tuple(
        MicCalibrationModel(
            usb_input=m.usb_input,
            model=model,
            sensitivity_db=m.sensitivity_db,
            delay_ms=m.delay_ms,
            inverted=m.inverted,
            shape_db=m.shape_db,
            problems=tuple(p.value for p in m.problems),
        )
        for m in result.mics
    )
    return MicCalibrationFile(set_id=set_id, created=now, centres_hz=result.centres_hz, mics=mics)


@dataclass(frozen=True, slots=True)
class Calibration:
    """S9: calibrate the microphones against each other (only with two or more)."""

    id: str = "calibration"
    title: str = "step-calibration-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Several microphones in a measuring goal."""
        return measuring(state) and state.mic_count > 1

    def run(self, ctx: WizardContext) -> Outcome:
        """Reuse a recent calibration or measure a new one; offer to leave out rejected mics."""
        state = ctx.box.state
        services = ctx.services
        set_id = mic_set_id([(usb, state.mic_model) for usb in state.mic_inputs])
        reused = reusable_mic_calibration(services.workspace, set_id, services.now())
        if reused is not None:
            write_session_mic_calibration(ctx.session_dir, reused)
            info(ctx.prompter, t("step-calibration-reuse"))
            ctx.box.update(calibration_set=set_id)
            return Outcome.DONE
        task(ctx.prompter, t("step-calibration-what"), t("step-calibration-place"))
        warn_before_sound(ctx, t("step-calibration-sweeps"), countdown=True)
        measurement = measure_round(
            ensure_backend(ctx),
            list(state.mic_inputs),
            fs_hz=FS_HZ,
            sweep=services.sweep,
            gates=GATES,
            level_dbfs=state.sweep_level_dbfs,
            stop=services.stop,
        )
        try:
            result = calibrate(measurement)
        except CalibrationError:
            info(ctx.prompter, t("step-calibration-bad-sweeps"))
            return Outcome.RETRY
        rows = tuple(
            (
                mic_text(state, m.usb_input),
                t("step-calibration-sensitivity", db=m.sensitivity_db),
                t("check-ok") if m.ok else t("check-fail"),
            )
            for m in result.mics
        )
        table = Table(
            (t("common-microphone"), t("step-calibration-h-sens"), t("check-result")), rows
        )
        notes = [t("step-calibration-done")]
        if result.polarity_ambiguous:
            notes.append(t("step-calibration-polarity-ambiguous"))
        if result.pair and result.rejected:
            notes.append(t("step-calibration-pair-differs"))
        info(ctx.prompter, *notes, table=table)
        stamp = services.now().isoformat(timespec="seconds")
        stored = _calibration_file(result, set_id, state.mic_model, stamp)
        write_mic_calibration(services.workspace, stored)
        # The session keeps its own copy: a later calibration of the same set must not change
        # how these points are corrected.
        write_session_mic_calibration(ctx.session_dir, stored)
        inputs = state.mic_inputs
        if result.rejected:
            bad = tuple(m.usb_input for m in result.rejected)
            exclude = choose(
                ctx.prompter,
                [
                    t(
                        "step-calibration-exclude",
                        count=len(bad),
                        mics=tuple(mic_text(state, usb) for usb in bad),
                    )
                ],
                [Option(ACCEPT, t("common-yes"), True), Option(SKIP, t("common-no"))],
            )
            if exclude == ACCEPT:
                inputs = tuple(i for i in inputs if i not in bad)
        if not inputs:
            return Outcome.BACK
        ctx.box.update(calibration_set=set_id, mic_inputs=inputs, mic_count=len(inputs))
        return Outcome.DONE


# ------------------------------------------------------------------ S10
def venue_plan(ctx: WizardContext) -> PointPlan:
    """The venue's point plan, made from the hall size the first time and kept for verify.

    Without a hall size this session, any stored plan is used as it is; a stored plan for
    another hall size is replaced.
    """
    state = ctx.box.state
    workspace = ctx.services.workspace
    hall = state.hall
    width, depth = (hall.width_m, hall.depth_m) if hall else (None, None)
    stored = read_plan(workspace, state.venue)
    if stored is not None:
        plan = plan_from_file(stored)
        if hall is None or (plan.width_m, plan.depth_m) == (width, depth):
            return plan
    plan = plan_points(width, depth)
    write_plan(workspace, state.venue, plan_file(plan))
    return plan


def place_text(plan: PointPlan, point: str) -> Text:
    """Where a point is, in words.

    "front rows, left, about 4 m from the stage, 2 m left of the centre aisle".
    """
    where = plan.point(point)
    side = plan.from_centre_m(point)
    return t(
        "point-where",
        area=t(f"point-{where.row}-{where.side}"),
        from_stage_m=round(plan.from_stage_m(point)),
        from_centre_m=abs(side),
        side=t("point-right-of-aisle") if side > 0 else t("point-left-of-aisle"),
    )


def _save_round(ctx: WizardContext, measurement: RoundMeasurement, points: dict[int, str]) -> None:
    for mic in measurement.mics:
        point = points[mic.usb_input]
        quality = mic.quality
        meta = PointMeta(
            point=point,
            time=ctx.services.now().isoformat(timespec="seconds"),
            level_dbfs=measurement.level_dbfs,
            avg=measurement.n_avg,
            backend=ctx.services.audio_name,
            peak_dbfs=quality.peak_dbfs,
            clipped_samples=quality.clipped_samples,
            noise_dbfs=quality.noise_dbfs,
            sweep_rms_dbfs=quality.sweep_rms_dbfs,
            snr_db=quality.snr_db,
            pnr_db=quality.pnr_db,
            latency_ms=quality.latency_ms,
            verdict=quality.verdict.value,
            warnings=tuple(
                CodedText(code=w.code.value, params=dict(w.params)) for w in quality.warnings
            ),
            mic_input=mic.usb_input,
            repeat_db=mic.repeat_db,
        )
        response = magnitude_db(mic.ir, measurement.fs_hz)
        save_point(
            ctx.session_dir,
            meta,
            recording=None,
            ir=mic.ir,
            fs_hz=measurement.fs_hz,
            grid_hz=GRID_HZ,
            response_db=response,
        )
        ctx.box.update(measured={**ctx.box.state.measured, point: point})


@dataclass(frozen=True, slots=True)
class MeasurePoints:
    """S10: the point plan and the measurement rounds."""

    id: str = "points"
    title: str = "step-points-title"
    changes_console: bool = False

    def applies(self, state: WizardStateFile) -> bool:
        """Goals that measure."""
        return measuring(state)

    @staticmethod
    def _place(ctx: WizardContext, rounds: Round, plan: PointPlan) -> None:
        state = ctx.box.state
        points = plan.ids
        lines = [
            t(
                "step-points-place",
                point=points.index(p.point) + 1,
                total=len(points),
                mic=mic_text(state, p.usb_input),
                place=place_text(plan, p.point),
            )
            for p in rounds.placements
        ]
        put = t("step-points-put", count=len(lines))
        here = {p.point for p in rounds.placements}
        task(
            ctx.prompter,
            put,
            *lines,
            t("step-points-height"),
            t("step-points-map-round"),
            picture=ascii_map(plan, here),
        )

    def run(self, ctx: WizardContext) -> Outcome:
        """Plan, then for each round: place, warn, count down, sweep, check the quality."""
        state = ctx.box.state
        plan = venue_plan(ctx)
        if state.points:  # a resumed session keeps its own points
            points, junction = state.points, state.junction_points or plan.junction_ids
        else:
            points, junction = plan.ids, plan.junction_ids
        ctx.box.update(points=points, junction_points=junction)
        # The rounds may start long after the first sound: read the console again before them.
        ctx.mics_checked = False
        about = [t("step-points-template")] if plan.from_template else []
        info(
            ctx.prompter,
            t("step-points-what"),
            t("step-points-plan", count=len(points)),
            *about,
            t("step-points-map"),
            picture=ascii_map(plan),
        )
        todo = [p for p in points if p not in state.measured]
        for one in plan_rounds(todo, list(state.mic_inputs)):
            placement = {p.usb_input: p.point for p in one.placements}
            self._place(ctx, one, plan)
            while True:
                warn_before_sound(ctx, t("step-points-sweeps"), countdown=True)
                measurement = measure_round(
                    ensure_backend(ctx),
                    list(one.inputs),
                    fs_hz=FS_HZ,
                    sweep=ctx.services.sweep,
                    gates=GATES,
                    level_dbfs=ctx.box.state.sweep_level_dbfs,
                    stop=ctx.services.stop,
                )
                bad = [m for m in measurement.mics if m.quality.verdict.value == "bad"]
                if not bad:
                    break
                codes = sorted({w.code.value for m in bad for w in m.quality.warnings})
                choice = choose(
                    ctx.prompter,
                    [t("step-points-bad", count=len(bad)), *(t(f"gate-{c}") for c in codes)],
                    [Option(RETRY, t("common-retry"), True), Option(ACCEPT, t("step-points-keep"))],
                )
                if choice == ACCEPT:
                    break
            _save_round(ctx, measurement, placement)
        info(ctx.prompter, t("step-points-done", count=len(ctx.box.state.measured)))
        return Outcome.DONE
