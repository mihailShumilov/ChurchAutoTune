"""Name dictionary, routing tables, USB map and model capabilities of the X32 driver."""

import pytest

from churchtune.domain.snapshot import ConsoleFamily, InputKind, InputRef, SignalRef, SourceKind
from churchtune.domain.topology import SpeakerRole
from churchtune.errors import UnsupportedConsole
from churchtune.mixers.base import Feature, Support
from churchtune.mixers.x32 import routing
from churchtune.mixers.x32.models import (
    capabilities,
    console_model,
    family_of,
    identity_from_xinfo,
)
from churchtune.mixers.x32.names import (
    Side,
    is_non_speaker,
    name_hint,
    normalize,
    role_hint,
    side_hint,
)
from churchtune.mixers.x32.routing import headamp_of
from churchtune.mixers.x32.usb import usb_audio_map
from tests.fixtures.topologies import TOPOLOGIES as TOPOLOGY_DIR
from tests.fixtures.topologies import all_fixtures
from tests.fixtures.topologies import load as load_fixture


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("Sub buffer", SpeakerRole.SUB),
        ("Саб", SpeakerRole.SUB),
        ("Сабвуфер", SpeakerRole.SUB),
        ("BASS", SpeakerRole.SUB),
        ("Main L", SpeakerRole.TOP_MONO),
        ("Портал лівий", SpeakerRole.TOP_MONO),
        ("PA", SpeakerRole.TOP_MONO),
        ("Mono", SpeakerRole.TOP_MONO),
        ("Front R", SpeakerRole.TOP_MONO),
        ("Frontfill", SpeakerRole.FILL),
        ("Balcon L", SpeakerRole.DELAY),
        ("Балкон", SpeakerRole.DELAY),
        ("Delay", SpeakerRole.DELAY),
        ("Monitor", SpeakerRole.MONITOR),
        ("Mon voc 1", SpeakerRole.MONITOR),
        ("Мон 2", SpeakerRole.MONITOR),
        ("Монітор", SpeakerRole.MONITOR),
        ("main sub", SpeakerRole.SUB),
        ("", None),
        ("Piano", None),
        # Spanish / Portuguese church usage
        ("Platea", SpeakerRole.TOP_MONO),
        ("Plateia", SpeakerRole.TOP_MONO),
        ("Altavoces", SpeakerRole.TOP_MONO),
        ("Graves", SpeakerRole.SUB),
        ("Retorno 1", SpeakerRole.MONITOR),
        ("Coro", SpeakerRole.MONITOR),
        ("Recepción", SpeakerRole.ZONE),
        # stage words win over sub / fill
        ("Bass Mon", SpeakerRole.MONITOR),
        ("Bass IEM", SpeakerRole.MONITOR),
        ("Drum fill", SpeakerRole.MONITOR),
        ("Side fill", SpeakerRole.MONITOR),
        # other rooms, delays, clusters
        ("Lobby", SpeakerRole.ZONE),
        ("Foyer", SpeakerRole.ZONE),
        ("Overflow", SpeakerRole.ZONE),
        ("Kids", SpeakerRole.ZONE),
        ("Dly 1", SpeakerRole.DELAY),
        ("UB", SpeakerRole.DELAY),
        ("Cluster", SpeakerRole.TOP_MONO),
        # glued names
        ("Mon1", SpeakerRole.MONITOR),
        ("MainL", SpeakerRole.TOP_MONO),
        ("SubR", SpeakerRole.SUB),
        # groups are not subs
        ("Submix", None),
        ("Субгруппа", None),
        ("Transept", None),
        ("PA Principal", SpeakerRole.TOP_MONO),
        ("Central", SpeakerRole.TOP_MONO),
    ],
)
def test_role_hint(name, role):
    assert role_hint(name) == role


@pytest.mark.parametrize(
    ("name", "side"),
    [
        ("Main L", Side.LEFT),
        ("Main R", Side.RIGHT),
        ("Портал левый", Side.LEFT),
        ("Портал правий", Side.RIGHT),
        ("Izquierdo", Side.LEFT),
        ("Derecho", Side.RIGHT),
        ("Sub", None),
        ("MainL", Side.LEFT),
        ("SubR", Side.RIGHT),
        ("ПорталЛ", Side.LEFT),
        ("Портал П", Side.RIGHT),
        ("Справа", Side.RIGHT),
        ("Слева", Side.LEFT),
        ("Esquerda", Side.LEFT),
        ("Direita", Side.RIGHT),
        ("L/R", None),
        ("LR", None),
        ("Live", None),
        ("Level", None),
        ("Derek", None),
        ("P", None),
        ("Central", None),
        ("Frontal", None),
        ("Sala Central", None),
        ("Monitor", None),
        ("Монитор", None),
        ("Subwoofer", None),
        ("Infill", None),
        ("Frontfill", None),
        ("Central R", Side.RIGHT),
        ("TopR", Side.RIGHT),
    ],
)
def test_side_hint(name, side):
    assert side_hint(name) == side


def test_normalize_and_non_speaker_names():
    assert normalize("  Саб Буфер ") == "sab bufer"
    assert is_non_speaker("Fx 1")
    assert is_non_speaker("Stream")
    assert is_non_speaker("Запис")
    assert is_non_speaker("Submix")
    assert is_non_speaker("Record")
    assert is_non_speaker("Livestream")
    assert not is_non_speaker("Main L")
    assert not is_non_speaker("Platea")
    assert not is_non_speaker("Recital hall")


def test_bass_alone_is_ambiguous_but_other_sub_words_are_not():

    assert name_hint("Bass").ambiguous
    assert not name_hint("Sub").ambiguous
    assert not name_hint("Bass sub").ambiguous


@pytest.mark.parametrize(
    ("source_id", "ref"),
    [
        (0, SignalRef(SourceKind.OFF)),
        (1, SignalRef(SourceKind.MAIN_L)),
        (2, SignalRef(SourceKind.MAIN_R)),
        (3, SignalRef(SourceKind.MONO)),
        (4, SignalRef(SourceKind.BUS, 1)),
        (19, SignalRef(SourceKind.BUS, 16)),
        (20, SignalRef(SourceKind.MATRIX, 1)),
        (25, SignalRef(SourceKind.MATRIX, 6)),
        (26, SignalRef(SourceKind.DIRECT_CHANNEL, 1)),
        (58, SignalRef(SourceKind.DIRECT_AUX, 1)),
        (66, SignalRef(SourceKind.DIRECT_FX, 1)),
        (74, SignalRef(SourceKind.MONITOR, 1)),
        (75, SignalRef(SourceKind.MONITOR, 2)),
        (76, SignalRef(SourceKind.TALKBACK)),
        (99, SignalRef(SourceKind.UNKNOWN, 99)),
    ],
)
def test_output_source_table(source_id, ref):
    assert routing.output_source(source_id) == ref


def test_signal_and_input_labels():
    assert SignalRef(SourceKind.MATRIX, 3).label() == "matrix 3"
    assert SignalRef(SourceKind.MONO).label() == "mono"
    assert InputRef(InputKind.AES50_A, 10).label() == "aes50_a 10"
    assert InputRef(InputKind.OFF).label() == "off"


@pytest.mark.parametrize(
    ("user_id", "ref"),
    [
        (0, InputRef(InputKind.OFF)),
        (13, InputRef(InputKind.LOCAL, 13)),
        (42, InputRef(InputKind.AES50_A, 10)),
        (81, InputRef(InputKind.AES50_B, 1)),
        (129, InputRef(InputKind.CARD, 1)),
        (161, InputRef(InputKind.AUX_IN, 1)),
        (200, InputRef(InputKind.UNKNOWN)),
    ],
)
def test_user_routing_ids(user_id, ref):
    assert routing.user_input_source(user_id) == ref


def test_routing_blocks():
    assert routing.parse_block("P169-16") == ("P16", 9, 16)
    assert routing.parse_block("A9-16") == ("A", 9, 16)
    assert routing.parse_block("??") is None
    assert routing.parse_block("A1-4") is None  # blocks carry eight channels
    assert routing.block_sources("A9-16")[1] == InputRef(InputKind.AES50_A, 10)
    assert routing.block_sources("AN1-8")[0] == InputRef(InputKind.LOCAL, 1)
    assert routing.block_sources("CARD1-8")[7] == InputRef(InputKind.CARD, 8)
    assert routing.block_sources("UOUT1-8") == (InputRef(InputKind.UNKNOWN),) * 8
    assert routing.block_sources("weird") == (InputRef(InputKind.UNKNOWN),) * 8
    users = tuple(range(1, 9))
    assert routing.block_sources("UIN1-8", users)[4] == InputRef(InputKind.LOCAL, 5)
    assert routing.block_sources("UIN9-16", users) == (InputRef(InputKind.UNKNOWN),) * 8


def test_headamps_and_channel_sources():
    assert routing.headamp_index(InputRef(InputKind.AES50_A, 10)) == 41
    assert routing.headamp_index(InputRef(InputKind.LOCAL, 1)) == 0
    assert routing.headamp_index(InputRef(InputKind.AES50_B, 48)) == 127
    assert routing.headamp_index(InputRef(InputKind.CARD, 1)) is None
    assert routing.headamp_index(InputRef(InputKind.AES50_A, 49)) is None
    assert routing.channel_input(5) == ("input", 5)
    assert routing.channel_input(33) == ("aux", 1)
    assert routing.channel_input(0) == ("off", 0)
    assert routing.channel_input(60) == ("unknown", 60)


def test_usb_map_of_the_reference_hall():
    hall = next(f for f in all_fixtures() if f.name == "hall-2026-10-02")
    usb = usb_audio_map(hall.snapshot())
    mic = usb.input_info(10)
    assert mic is not None
    assert mic.source == InputRef(InputKind.AES50_A, 10)
    assert mic.headamp is not None
    assert mic.headamp.phantom
    assert mic.channels == (10,)
    assert usb.input_info(99) is None
    playback = {o.usb: o.channels for o in usb.outputs}
    assert playback[1] == (23,)
    assert playback[2] == (24,)


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("X32P", ConsoleFamily.X32),
        ("X32RACK", ConsoleFamily.X32),
        ("x32c", ConsoleFamily.X32),
        ("M32R", ConsoleFamily.M32),
        ("M32", ConsoleFamily.M32),
    ],
)
def test_family_of(model, family):
    assert family_of(model) is family


def test_family_of_rejects_other_consoles():
    with pytest.raises(UnsupportedConsole):
        family_of("XR18")


def test_identity_from_xinfo():
    identity = identity_from_xinfo(["192.0.2.80", "Hall", "X32P", "4.09"], "127.0.0.1")
    assert identity.model == "X32P"
    assert identity.host == "127.0.0.1"
    with pytest.raises(UnsupportedConsole):
        identity_from_xinfo(["only", "three", "items"], "127.0.0.1")
    with pytest.raises(UnsupportedConsole):
        identity_from_xinfo(["a", "b", 3, "4.09"], "127.0.0.1")


def test_capabilities_by_model_and_firmware():
    x32 = identity_from_xinfo(["ip", "n", "X32P", "4.09"], "h")
    caps = capabilities(x32)
    assert caps.verified_firmware
    assert caps.can_write(Feature.MAIN_EQ)
    assert caps.can_write(Feature.MATRIX_FADER)
    assert caps.of(Feature.OUTPUT_DELAY) is Support.SUGGEST_ONLY
    assert caps.of(Feature.MATRIX_POLARITY) is Support.SUGGEST_ONLY
    assert not caps.can_write(Feature.BUS_EQ)
    assert not caps.can_write(Feature.CHANNEL_MUTE)
    m32 = capabilities(identity_from_xinfo(["ip", "n", "M32", "4.09"], "h"))
    assert not any(m32.can_write(f) for f in Feature)
    old = capabilities(identity_from_xinfo(["ip", "n", "X32", "3.11"], "h"))
    assert not old.verified_firmware
    assert not any(old.can_write(f) for f in Feature)
    # Same family and firmware line, but never checked on hardware: suggest-only.
    for model, firmware in (("X32RACK", "4.06"), ("X32", "4.09"), ("X32P", "4.06")):
        caps = capabilities(identity_from_xinfo(["ip", "n", model, firmware], "h"))
        assert caps.verified_firmware
        assert not any(caps.can_write(f) for f in Feature), (model, firmware)
    assert capabilities(identity_from_xinfo(["ip", "n", "x32p ", "4.09"], "h")).can_write(
        Feature.MAIN_EQ
    )
    assert console_model(x32).identity is x32


def test_missing_feature_is_none():
    caps = capabilities(identity_from_xinfo(["ip", "n", "X32P", "4.09"], "h"))
    assert type(caps)({}, verified_firmware=True).of(Feature.MAIN_EQ) is Support.NONE


def test_the_preamp_behind_a_physical_input():
    snapshot = load_fixture(TOPOLOGY_DIR / "hall-2026-10-02.toml").snapshot()
    measurement_mic = headamp_of(snapshot, InputRef(InputKind.AES50_A, 10))
    assert measurement_mic is not None
    assert measurement_mic.index == 41
    assert measurement_mic.phantom
    assert headamp_of(snapshot, InputRef(InputKind.CARD, 1)) is None
