"""Topology fixtures: a real dump plus line overrides, with the expected roles.

Each ``tests/fixtures/x32/topologies/*.toml`` names a base dump, a model string, the node lines it
overrides and the expected guess. The emulator can serve the same state.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from churchtune.domain.snapshot import ConsoleIdentity, ConsoleSnapshot
from churchtune.mixers.x32.models import family_of
from churchtune.mixers.x32.snapshot import parse_snapshot, raw_from_dump
from tests.fixtures.paths import X32_DUMPS

TOPOLOGIES = X32_DUMPS.parent / "topologies"


@dataclass(frozen=True)
class TopologyFixture:
    name: str
    description: str
    model: str
    raw: dict[str, str]
    expected: dict = field(default_factory=dict)

    def identity(self, host: str = "127.0.0.1") -> ConsoleIdentity:
        return ConsoleIdentity(family_of(self.model), self.model, "FIXTURE", "4.09", host)

    def snapshot(self) -> ConsoleSnapshot:
        return parse_snapshot(self.identity(), self.raw, "2026-10-14T00:00:00")

    def dump_text(self) -> str:
        return "\n".join(self.raw.values()) + "\n"


def load(path: Path) -> TopologyFixture:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    raw = raw_from_dump((X32_DUMPS / data["base"]).read_text(encoding="utf-8"))
    raw.update(data.get("overrides", {}))
    return TopologyFixture(path.stem, data["description"], data["model"], raw, data["expected"])


def all_fixtures() -> list[TopologyFixture]:
    return [load(p) for p in sorted(TOPOLOGIES.glob("*.toml"))]
