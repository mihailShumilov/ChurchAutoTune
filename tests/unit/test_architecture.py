"""Layer rule from SPEC §5, enforced by parsing imports.

``ALLOWED`` lists, for every top-level package of ``churchtune``, the other packages it may import.
An entry such as ``"mixers.base"`` allows only that module of the package. ``errors`` (exception
classes) is importable from everywhere and imports nothing itself.

Clarifications on top of the SPEC table, recorded in docs/adr:
  * ``platform`` is a leaf holding the dev-safe guard; ``mixers`` and ``audio`` import it to refuse
    real consoles and real audio devices.
  * ``measure``, ``design`` and ``report`` may import ``analysis`` (shared response math), never the
    other way round.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "churchtune"
ROOT_PACKAGE = "churchtune"
EVERYWHERE = "errors"

ALLOWED: dict[str, set[str]] = {
    "__main__": {"cli"},
    "errors": set(),
    "domain": set(),
    "platform": set(),
    "i18n": set(),
    "safety": {"domain"},
    "analysis": {"domain"},
    "measure": {"domain", "analysis"},
    "design": {"domain", "analysis"},
    "storage": {"domain"},
    "report": {"domain", "analysis", "design"},
    "mixers": {"domain", "safety", "platform"},
    "audio": {"domain", "platform"},
    "wizard": {
        "domain",
        "mixers.base",
        "audio.base",
        "measure",
        "analysis",
        "design",
        "safety",
        "storage",
        "report",
    },
    "app": {"wizard", "i18n", "domain"},
    "cli": {
        "app",
        "wizard",
        "domain",
        "mixers",
        "audio",
        "measure",
        "analysis",
        "design",
        "safety",
        "storage",
        "report",
        "i18n",
        "platform",
    },
}

# Third-party / stdlib modules that only one file may import.
SOLE_IMPORTERS: dict[str, str] = {
    "socket": "mixers/x32/transport.py",
    "sounddevice": "audio/sounddevice_backend.py",
}


def _module_name(path: Path, src: Path) -> str:
    parts = path.relative_to(src).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join((ROOT_PACKAGE, *parts))


def _imports(source: str, module: str, is_package: bool) -> set[str]:
    """Absolute dotted names of everything ``source`` imports."""
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - (node.level - 1)]
                target = ".".join([*base, *(node.module.split(".") if node.module else [])])
            else:
                target = node.module or ""
            # Only the imported names: `from pkg import base` is judged as `pkg.base`.
            found.update(f"{target}.{alias.name}" for alias in node.names)
    return found


def _layer(module: str) -> str | None:
    """Top-level package of a ``churchtune`` module, or None for the root and foreign modules."""
    parts = module.split(".")
    if parts[0] != ROOT_PACKAGE or len(parts) < 2:
        return None
    return parts[1]


def _imported_layer(name: str) -> str | None:
    """Layer of an imported name; None for foreign modules and root attributes (``__version__``)."""
    layer = _layer(name)
    return layer if layer in ALLOWED else None


def _is_allowed(importer_layer: str, imported: str) -> bool:
    imported_layer = _imported_layer(imported)
    if imported_layer is None or imported_layer in (importer_layer, EVERYWHERE):
        return True
    inner = imported.split(".", 1)[1]
    return any(inner == rule or inner.startswith(rule + ".") for rule in ALLOWED[importer_layer])


def violations(src: Path) -> list[str]:
    """Every import under ``src`` that breaks the layer rule or a sole-importer rule."""
    problems: list[str] = []
    for path in sorted(src.rglob("*.py")):
        module = _module_name(path, src)
        layer = _layer(module)
        rel = path.relative_to(src).as_posix()
        imported = _imports(path.read_text(encoding="utf-8"), module, path.name == "__init__.py")
        for name in sorted(imported):
            top = name.split(".")[0]
            if top in SOLE_IMPORTERS and rel != SOLE_IMPORTERS[top]:
                problems.append(f"{rel}: imports {top} (only {SOLE_IMPORTERS[top]} may)")
            if layer is None or _imported_layer(name) is None:
                continue
            if layer not in ALLOWED:
                problems.append(f"{rel}: package '{layer}' is not in the layer table")
                break
            if not _is_allowed(layer, name):
                problems.append(f"{rel}: {layer} must not import {name}")
    return problems


def test_layer_rule_holds_for_all_source_files():
    assert violations(SRC) == []


def test_every_package_on_disk_is_in_the_layer_table():
    on_disk = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}

    assert on_disk <= set(ALLOWED)


def _write(root: Path, rel: str, source: str) -> None:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    ("rel", "source", "expected"),
    [
        ("safety/policy.py", "from churchtune.mixers.x32 import writer\n", "safety must not"),
        ("safety/policy.py", "from ..mixers import base\n", "safety must not"),
        ("domain/eq.py", "import churchtune.storage.paths\n", "domain must not"),
        ("app/screens.py", "from churchtune.mixers.x32.client import X32Client\n", "app must not"),
        ("wizard/steps.py", "from churchtune.mixers import x32\n", "wizard must not"),
        ("analysis/rt60.py", "from churchtune.measure import gates\n", "analysis must not"),
        ("mixers/x32/client.py", "import socket\n", "imports socket"),
        ("measure/point.py", "def f():\n    import sounddevice\n", "imports sounddevice"),
    ],
)
def test_checker_reports_forbidden_import(tmp_path, rel, source, expected):
    _write(tmp_path, rel, source)

    problems = violations(tmp_path)

    assert len(problems) == 1
    assert expected in problems[0]


@pytest.mark.parametrize(
    ("rel", "source"),
    [
        ("wizard/steps.py", "from churchtune.mixers.base import MixerDriver\n"),
        ("mixers/x32/writer.py", "from churchtune.safety import policy\n"),
        ("mixers/x32/transport.py", "import socket\n"),
        ("audio/factory.py", "from churchtune.platform.devsafe import is_dev_safe\n"),
        ("design/target.py", "from churchtune.errors import UnsafeWrite\nimport numpy\n"),
        ("measure/point.py", "from . import gates\nfrom ..analysis import response\n"),
    ],
)
def test_checker_accepts_allowed_import(tmp_path, rel, source):
    _write(tmp_path, rel, source)

    assert violations(tmp_path) == []
