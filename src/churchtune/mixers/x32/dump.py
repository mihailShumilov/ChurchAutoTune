"""The list of nodes read for a full read-only dump of the console."""

from collections.abc import Iterator

CHANNELS, AUX_INPUTS, BUSES, MATRICES = 32, 8, 16, 6
CHANNEL_EQ_BANDS, BUS_EQ_BANDS = 4, 6
DCAS, EFFECTS, HEADAMPS = 8, 8, 128
MAIN_OUTPUTS, AUX_OUTPUTS, P16_OUTPUTS = 16, 6, 16
MATRIX_SENDS = 6
CONFIG_NODES = (
    "config/chlink", "config/auxlink", "config/fxlink", "config/buslink", "config/mtxlink",
    "config/mute", "config/linkcfg", "config/mono", "config/solo", "config/talk",
    "config/routing/IN", "config/routing/AES50A", "config/routing/AES50B", "config/routing/CARD",
    "config/routing/OUT", "config/userrout/in", "config/userrout/out",
)  # fmt: skip
EXTRA_NODES = ("-prefs", "-prefs/card", "main/st/eq", "main/st/mix")


def _two(count: int) -> list[str]:
    return [f"{i:02d}" for i in range(1, count + 1)]


def _strips() -> Iterator[str]:
    for number in _two(CHANNELS):
        base = f"ch/{number}"
        yield from (f"{base}/{leaf}" for leaf in ("config", "delay", "preamp", "gate"))
        yield from (f"{base}/{leaf}" for leaf in ("gate/filter", "dyn", "dyn/filter", "insert"))
        yield f"{base}/eq"
        yield from (f"{base}/eq/{band}" for band in range(1, CHANNEL_EQ_BANDS + 1))
        yield f"{base}/mix"
        yield from (f"{base}/mix/{send}" for send in _two(BUSES))
        yield from (f"{base}/grp", f"{base}/automix")
    for number in _two(AUX_INPUTS):
        for kind in ("auxin", "fxrtn"):
            base = f"{kind}/{number}"
            yield from (f"{base}/config", f"{base}/preamp", f"{base}/eq")
            yield from (f"{base}/eq/{band}" for band in range(1, CHANNEL_EQ_BANDS + 1))
            yield f"{base}/mix"
            yield from (f"{base}/mix/{send}" for send in _two(BUSES))
            yield f"{base}/grp"


def _buses() -> Iterator[str]:
    groups = [(f"bus/{n}", True) for n in _two(BUSES)]
    groups += [(f"mtx/{n}", False) for n in _two(MATRICES)]
    groups += [("main/st", True), ("main/m", True)]
    for base, has_matrix_sends in groups:
        yield from (f"{base}/{leaf}" for leaf in ("config", "dyn", "dyn/filter", "insert", "eq"))
        yield from (f"{base}/eq/{band}" for band in range(1, BUS_EQ_BANDS + 1))
        yield f"{base}/mix"
        if has_matrix_sends:
            yield from (f"{base}/mix/{send}" for send in _two(MATRIX_SENDS))
        yield f"{base}/grp"


def dump_nodes() -> list[str]:
    """Every node of a full dump, in the order of the original dump tool, without duplicates."""
    nodes = [*_strips(), *_buses()]
    for dca in range(1, DCAS + 1):
        nodes += [f"dca/{dca}", f"dca/{dca}/config"]
    for effect in range(1, EFFECTS + 1):
        nodes += [f"fx/{effect}", f"fx/{effect}/source", f"fx/{effect}/par"]
    nodes += [f"headamp/{i:03d}" for i in range(HEADAMPS)]
    nodes += CONFIG_NODES
    nodes += [f"outputs/main/{n}" for n in _two(MAIN_OUTPUTS)]
    nodes += [f"outputs/aux/{n}" for n in _two(AUX_OUTPUTS)]
    nodes += [f"outputs/p16/{n}" for n in _two(P16_OUTPUTS)]
    nodes += EXTRA_NODES
    return list(dict.fromkeys(nodes))
