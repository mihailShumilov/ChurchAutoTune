# Coding Standards — ChurchTune

These rules apply to every line of code in this repository. `code-reviewer` checks PRs against them.
When a rule and the SPEC disagree, the SPEC wins; record the exception in an ADR.

## 1. Language and tooling
- Python **3.12**. Managed by **uv**: `uv sync`, `uv run …`, `uv add` (never `pip install` into the env).
- Formatting and lint: **ruff** (`ruff format`, `ruff check`). Line length 100.
  Rule sets: `E, F, W, I, B, UP, SIM, RUF, N, PL, PT, PTH, C4, ARG, RET, TRY, PERF, NPY, S, ANN, D (google)`,
  with project-level ignores justified in `pyproject.toml` comments.
- Types: **mypy --strict** on `src/` (tests: `--strict` minus `disallow-untyped-defs` for fixtures).
  No `Any` in public signatures; `# type: ignore[code]` only with a reason comment.
- pre-commit runs ruff, mypy, i18n key check, and a secret scan. CI runs the same.

## 2. Architecture
- Respect the layer rule (SPEC §5). `domain`, `analysis`, `design` are pure: no IO, no sockets, no clocks,
  no global state, no randomness without an explicit `numpy.random.Generator` argument.
- Side effects at the edges: `mixers/*/client.py` (network), `audio/` (devices), `storage/` (files),
  `app/` and `cli/` (terminal). Everything else receives data and returns data.
- Depend on protocols, not implementations (`MixerDriver`, `AudioBackend`, `Prompter`, `Clock`).
  Inject them; no module-level singletons. Configuration is passed as frozen objects.
- One responsibility per module; modules ≤ ~400 lines, functions ≤ ~50 lines (split before that).
- No circular imports. No `from x import *`. Public API of a package is what `__init__.py` re-exports.

## 3. Naming and units
- Units are part of the name: `freq_hz`, `gain_db`, `delay_ms`, `level_dbfs`, `spl_db`, `duration_s`,
  `distance_m`, `fs_hz`. Never bare `freq`, `gain`, `t`.
- Normalized console values: `x_norm`; quantized indices: `step_idx`.
- Mic/point/output identifiers are typed `NewType`s (`MicId`, `PointId`, `OutputId`), not raw str/int.
- Constants in `UPPER_SNAKE`, in the module that owns them or in `config` objects — no magic numbers in logic.
  Thresholds (quality gates, limits, acceptance) live in typed config with defaults documented in SPEC.

## 4. Data modeling
- Persisted data: **pydantic v2** models with `model_config = ConfigDict(frozen=True, extra="forbid")`,
  a `schema_version: int`, and migrations in `storage/migrations.py` (each with a test).
- Internal numeric data: `@dataclass(frozen=True, slots=True)` holding numpy arrays; document shapes and
  dtypes in the docstring (`ir: float64[n_samples]`, `curves_db: float64[n_points, n_freqs]`).
- Enums for closed sets (`EqType`, `SpeakerRole`, `WriteScope`, `Verdict`) — `StrEnum` where serialized.
- Never mutate inputs. Return new objects.

## 5. Numerics and DSP
- `float64` everywhere in analysis; audio IO may use `float32` at the device boundary only.
- Vectorize with numpy; no Python loops over samples. Use `scipy.signal` for filters/convolution.
- Every DSP function has a test with a synthetic signal whose answer is known analytically
  (e.g. a delayed impulse → known delay; a biquad → known magnitude at fc; an exponential decay → known RT60).
- Tolerances are explicit in tests (`np.testing.assert_allclose(..., atol=0.1)` with a comment why).
- Never silently clip or NaN: validate inputs, raise `ValueError` with a clear message.
- Log-domain math: guard `log10(0)` with a documented floor (e.g. `1e-12`).

## 6. Errors and safety
- Domain errors are explicit exception classes in `churchtune.errors` (`ConsoleUnreachable`,
  `UnsafeWrite`, `ReadbackMismatch`, `AudioDeviceError`, `MeasurementAborted`, `QualityGateFailed`).
  Each carries a user-facing i18n key and context; the UI renders "what happened + what to do".
- Never `except Exception:` without re-raising or a justified, logged recovery. Never bare `except:`.
- Resource cleanup with context managers. Anything that changes console state (temporary mutes)
  restores it in `finally` and via `pending_restore.json`.
- Safety-critical code (`safety/`, writers, clients, audio limiter) — no cleverness: plain, explicit,
  table-driven allowlists; 100 % line+branch coverage; reviewed by `safety-reviewer`.

## 7. Concurrency
- Core is synchronous. The Textual app runs blocking work in thread workers; communication back to the UI
  only via messages/events, never by touching widgets from worker threads.
- The audio callback does only buffer copies and level math; no allocation-heavy work, no logging, no locks
  that the UI thread may hold.
- Every network call has a timeout; retries are bounded and logged.

## 8. Testing
- pytest; tests mirror `src/` layout. Names: `test_<unit>_<behavior>_<condition>`.
- Arrange–Act–Assert, one behavior per test. Fixtures in `conftest.py`, factories over large literal fixtures.
- hypothesis for invariants (quantization round-trips, allowlists, write ordering, undo).
- Use the emulator and fake audio; never mock away the thing under test. No sleeps in tests — inject a clock.
- Coverage gates per SPEC §14.3. Snapshot tests for TUI; update snapshots only after looking at the diff.
- A bug fix starts with a failing test.

## 9. Logging
- `logging.getLogger(__name__)`; no `print` outside `cli/` rendering.
- Session events also go to `session.log.jsonl` (structured: `event`, `step`, `params`, `result`).
- Log every console write (path, old, new, readback) at INFO. Never log secrets or personal data.

## 10. Documentation
- Google-style docstrings on every public function/class: purpose, args with units, returns, raises.
  Comments explain *why*, not *what*.
- ADR for each significant decision (`docs/adr/NNNN-title.md`: context, decision, consequences).
- User docs (`docs/user-guide/*`) updated with behavior changes. CHANGELOG in Keep-a-Changelog format.

## 11. UI and text
- No user-facing literal strings in code — only i18n keys. Follow the `wizard-ux` skill.
- Colors only through the Textual theme/CSS variables; meaning never by color alone.

## 12. Git
- Conventional Commits (`feat(measure): …`, `fix(safety): …`, `test:`, `docs:`, `refactor:`, `chore:`).
- Branch per milestone/task. PR description: what, why, how tested, reviewer agents run and their verdicts.
- Never commit generated measurement data except approved fixtures; respect `.gitignore`.

## 13. Dependencies
- Add a dependency only with justification in the PR and an ADR if it is runtime. Pin via `uv.lock`.
- Prefer the standard library and the existing stack (numpy, scipy, sounddevice, soundfile, matplotlib,
  pydantic, textual, typer, rich, jinja2, fluent.runtime, tomli-w).
