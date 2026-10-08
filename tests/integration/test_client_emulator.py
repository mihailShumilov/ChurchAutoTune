"""Read-only client and transport against the emulator over loopback UDP."""

import pytest

from churchtune.domain.eq import EqType
from churchtune.errors import ConsoleUnreachable
from churchtune.mixers.x32.nodes import read_main_eq


def test_ping_returns_console_identity(client):
    assert client.ping() == ["127.0.0.1", "X32P-EMU", "X32P", "4.09"]


def test_node_reads_a_line_from_the_dump(client):
    assert client.node("mtx/03/config") == '/mtx/03/config "Sub buffer " 25 RD'


def test_node_survives_one_lost_packet(emu, client):
    emu.drop_next = 1

    assert client.node("main/st/eq", timeout_s=0.1) == "/main/st/eq ON"


def test_node_returns_none_when_the_console_stays_silent(emu, client):
    emu.drop_next = 2

    assert client.node("main/st/eq", timeout_s=0.05) is None


def test_query_returns_normalized_float_and_int(client):
    assert client.query("/main/st/eq/2/f") == pytest.approx(71 / 200)
    assert client.query("/main/st/eq/2/type") == 2
    assert client.query("/mtx/03/mix/on") == 1


def test_read_main_eq_matches_the_dump(client):
    eq = read_main_eq(client)

    assert eq.on is True
    assert [b.type for b in eq.bands] == [
        EqType.BU18, EqType.PEQ, EqType.PEQ, EqType.PEQ, EqType.PEQ, EqType.HSHV,
    ]  # fmt: skip
    assert eq.band(4).freq_hz == pytest.approx(1970)
    assert eq.band(4).gain_db == -3.5


def test_read_main_eq_raises_when_the_console_does_not_answer(emu, client):
    emu.drop_next = 10**6

    class Impatient:
        def node(self, path):
            return client.node(path, timeout_s=0.02)

    with pytest.raises(ConsoleUnreachable) as excinfo:
        read_main_eq(Impatient())

    assert excinfo.value.context == {"node": "main/st/eq"}


def test_meters0_receives_frames_with_the_mic_level(emu, client):
    emu.mic_level = 0.25
    frames = []

    client.meters0(0.3, frames.append)

    assert frames
    assert len(frames[0]) == 70
    assert frames[0][9] == pytest.approx(0.25)


def test_reading_never_writes(emu, client):
    client.ping()
    client.node("main/st/eq/2")
    client.query("/mtx/03/mix/fader")
    client.meters0(0.1, lambda frame: None)
    read_main_eq(client)

    assert emu.writes == []
    assert emu.unknown == []
