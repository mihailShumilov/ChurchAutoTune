"""Translation catalogues (SPEC §11): same keys and variables everywhere, nothing missing or unused."""

import re
from pathlib import Path

import pytest
from fluent.syntax import FluentParser, FluentSerializer, ast

from churchtune import errors
from churchtune.cli.wizard import PLAIN_TEXTS
from churchtune.design.channel_audit import AuditCode
from churchtune.design.packages import NoteCode as PackageNoteCode
from churchtune.design.packages import PackageKind, Risk
from churchtune.domain.snapshot import OutputKind, SourceKind
from churchtune.domain.topology import SpeakerRole, Variant
from churchtune.i18n import LANGUAGES, Translator, catalogue_files, detect_language
from churchtune.measure.gates import WarningCode
from churchtune.measure.level import RampStop, SweepStop
from churchtune.measure.mic_mapping import MappingOutcome
from churchtune.measure.noise import NoiseHint
from churchtune.measure.plan import ROWS, SIDES
from churchtune.wizard.protocol import Kind
from churchtune.wizard.steps.setup import MIC_MODELS

SRC = Path(__file__).resolve().parents[2] / "src" / "churchtune"
CODE = [
    *sorted((SRC / "wizard").rglob("*.py")),
    *sorted((SRC / "app").rglob("*.py")),
    SRC / "cli" / "wizard.py",
]
STATIC_KEY = re.compile(r'(?:\bt|\bText|\btranslate)\(\s*"([a-z0-9-]+)"')
TOPOLOGY_CODE = sorted((SRC / "mixers" / "x32").glob("topology*.py"))
ISSUE_CODE = re.compile(
    r'Issue\(\s*"([a-z_]+)"|code = "([a-z_]+)" if|"([a-z_]+)" if audible else "([a-z_]+)"'
)


def kebab(value: str) -> str:
    return value.replace("_", "-")


def _messages(lang: str) -> dict[str, set[str]]:
    """Message id -> variables it uses."""
    found: dict[str, set[str]] = {}
    for path in catalogue_files(lang):
        resource = FluentParser().parse(path.read_text(encoding="utf-8"))
        for entry in resource.body:
            if isinstance(entry, ast.Junk):
                pytest.fail(f"{path}: syntax error {entry.annotations}")
            if isinstance(entry, ast.Message):
                text = FluentSerializer().serialize_entry(entry)
                found[entry.id.name] = set(re.findall(r"\$([a-z_]+)", text))
    return found


def _issue_codes() -> set[str]:
    codes: set[str] = set()
    for path in TOPOLOGY_CODE:
        for groups in ISSUE_CODE.findall(path.read_text(encoding="utf-8")):
            codes.update(g for g in groups if g)
    # Codes chosen by a condition rather than written inside Issue(...).
    return codes | {"several_subs", "sub_pair", "other_speakers_audible", "other_speakers_found"}


def used_keys() -> set[str]:
    keys: set[str] = set()
    for path in CODE:
        keys.update(STATIC_KEY.findall(path.read_text(encoding="utf-8")))
    keys |= {f"role-{kebab(r.value)}" for r in SpeakerRole}
    keys |= {f"variant-{kebab(v.value)}" for v in Variant}
    keys |= {f"issue-{kebab(c)}" for c in _issue_codes()}
    keys |= {f"gate-{w.value}" for w in WarningCode}
    keys |= {f"noise-{h.value}" for h in NoiseHint}
    keys |= {f"step-level-ramp-{kebab(s.value)}" for s in RampStop}
    keys |= {f"step-level-sweep-{kebab(s.value)}" for s in SweepStop if s is not SweepStop.FOUND}
    keys |= {f"step-mics-{kebab(o.value)}" for o in MappingOutcome if o is not MappingOutcome.FOUND}
    keys |= {f"step-welcome-goal-{g}" for g in ("full", "verify", "sub", "report")}
    keys |= {f"topology-h-{k}" for k in ("output", "path", "name", "role", "confidence")}
    keys |= {"topology-name", "topology-confidence"}
    keys |= {f"finding-rt60-{v}" for v in ("dry", "good", "boomy", "treatment")}
    keys |= {"finding-spread-large", "finding-spread-small"}
    keys |= {f"keys-{k.value}" for k in Kind}
    keys |= {f"tui-keys-{k.value}" for k in Kind if k is not Kind.CONFIRM}
    keys |= {"common-step", "glossary-crossover", "glossary-rt60"}
    keys |= {f"signal-{kebab(k.value)}" for k in SourceKind}
    keys |= {f"output-{k.value}" for k in OutputKind}
    keys |= set(PLAIN_TEXTS.values())
    keys |= {f"point-{r}-{s}" for r in ROWS for s in SIDES}
    keys |= {f"package-{kebab(k.value)}" for k in PackageKind}
    keys |= {f"package-{kebab(k.value)}-why" for k in PackageKind}
    keys |= {f"risk-{r.value}" for r in Risk}
    keys |= {f"audit-{a.value}" for a in AuditCode}
    keys |= {f"note-{c.value}" for c in PackageNoteCode}
    keys |= {f"effect-{m}" for m in ("junction-score", "rms-deviation")}
    keys |= {f"audio-problem-{p}" for p in ("channels", "sample-rate")}
    keys |= {f"verify-{v}" for v in ("better", "no-change", "worse")}
    keys |= {"apply-word", "undo-word", "apply-word-mismatch", "undo-word-mismatch"}
    keys |= {f"write-{p}" for p in ("eq-on", "fader", "on")}
    keys |= {f"write-band-{p}" for p in ("type", "freq", "gain", "q")}
    keys |= {"step-equipment-model-other"} if "other" in MIC_MODELS else set()
    keys |= {
        cls.i18n_key
        for cls in vars(errors).values()
        if isinstance(cls, type) and issubclass(cls, errors.ChurchTuneError)
    }
    keys |= {f"step-{s}-title" for s in ("welcome", "mac", "connect", "read", "topology", "equipment",
                                          "mics", "path", "level", "calibration", "points", "results",
                                          "apply", "verify", "finish")}  # fmt: skip
    return keys


@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_language_has_the_same_keys_and_variables(lang):
    source = _messages("en")
    other = _messages(lang)
    assert set(other) == set(source), sorted(set(source) ^ set(other))
    for key, variables in source.items():
        assert other[key] == variables, (lang, key)


def test_every_key_used_in_code_exists_and_none_is_unused():
    catalogue = set(_messages("en"))
    used = used_keys()
    assert not used - catalogue, sorted(used - catalogue)
    assert not catalogue - used, sorted(catalogue - used)


def test_plurals_and_decimal_commas():
    assert Translator("ru")("step-points-bad", {"count": 3}).startswith("3")
    assert "21" in Translator("uk")("step-equipment-mics", {"count": 21})
    assert "1,5" in Translator("ru")("finding-rt60-boomy", {"rt60_s": 1.5})
    assert "1.5" in Translator("en")("finding-rt60-boomy", {"rt60_s": 1.5})


def test_missing_keys_render_visibly_and_unknown_languages_are_refused():
    assert Translator("en")("no-such-key") == "no-such-key"
    with pytest.raises(ValueError, match="language"):
        Translator("de")


@pytest.mark.parametrize(
    ("environ", "lang"),
    [
        ({"CTUNE_LANG": "uk"}, "uk"),
        ({"LANG": "ru_RU.UTF-8"}, "ru"),
        ({"LANG": "de_DE"}, "en"),
        ({}, "en"),
    ],
)
def test_detect_language(environ, lang):
    assert detect_language(environ) == lang


def test_keys_in_test_screens_exist():
    """Snapshot fixtures must show real texts, not raw keys."""
    catalogue = set(_messages("en"))
    tests = Path(__file__).resolve().parents[1]
    used: set[str] = set()
    for path in [*sorted((tests / "tui").glob("*.py")), *sorted((tests / "e2e").glob("*.py"))]:
        used.update(STATIC_KEY.findall(path.read_text(encoding="utf-8")))
    assert not used - catalogue, sorted(used - catalogue)
