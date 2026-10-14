"""The X32 driver against the packaged emulator on loopback (SPEC M1 acceptance)."""

import time
from datetime import datetime

import pytest

from churchtune.domain.snapshot import ConsoleFamily, OutputKind
from churchtune.domain.topology import SpeakerRole, Variant
from churchtune.errors import ConsoleUnreachable, UnsupportedConsole
from churchtune.mixers.base import Feature, MeterSet
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.driver import X32Driver, discover_consoles
from churchtune.mixers.x32.snapshot import parse_snapshot, snapshot_nodes
from churchtune.mixers.x32.transport import UdpTransport
from churchtune_emulator.x32 import LOOPBACK, X32Emulator
from tests.fixtures.topologies import all_fixtures

# SPEC M1: the snapshot is read in at most 20 s with 5 ms latency per reply.
LATENCY_S = 0.005
MAX_READ_S = 20.0


@pytest.fixture
def emulator():
    emu = X32Emulator().start()
    yield emu
    emu.stop()


def _driver(emu, **options):
    transport = UdpTransport(LOOPBACK, emu.port)
    return X32Driver.connect(X32Client(transport), **options), transport


def test_connect_reads_identity_and_capabilities(emulator):
    driver, transport = _driver(emulator)
    with transport:
        identity = driver.model.identity
        assert identity.model == "X32P"
        assert identity.family is ConsoleFamily.X32
        assert identity.firmware == "4.09"
        assert identity.host == LOOPBACK
        assert driver.model.capabilities.can_write(Feature.MATRIX_EQ)


def test_snapshot_from_emulator_equals_snapshot_from_the_dump(emulator):
    driver, transport = _driver(emulator)
    progress = []
    with transport:
        snap = driver.read_snapshot(lambda done, total: progress.append(done))
    assert progress[-1] == len(snapshot_nodes())
    expected = parse_snapshot(driver.model.identity, emulator.nodes, snap.taken_at)
    assert snap.matrices == expected.matrices
    assert snap.channels == expected.channels
    assert snap.usb == expected.usb
    assert snap.matrices[3].name == "Sub buffer"
    # The emulator also answers matrix polarity and output delays.
    assert all(o.delay is not None for o in snap.outputs if o.kind is OutputKind.MAIN)
    assert emulator.writes == []


@pytest.mark.parametrize("fixture", all_fixtures(), ids=lambda f: f.name)
def test_topology_through_the_emulator(fixture):
    emu = X32Emulator(raw=fixture.raw, model=fixture.model).start()
    try:
        driver, transport = _driver(emu)
        with transport:
            guess = driver.infer_topology(driver.read_snapshot())
        assert guess.variant == Variant(fixture.expected["variant"])
        for key, role in fixture.expected["outputs"].items():
            kind, number = key.split()
            assert guess.role_of(OutputKind(kind), int(number)) == SpeakerRole(role), key
        assert emu.writes == []
    finally:
        emu.stop()


def test_snapshot_read_time_with_latency():
    emu = X32Emulator(latency_s=LATENCY_S).start()
    try:
        driver, transport = _driver(emu)
        with transport:
            start = time.monotonic()
            snap = driver.read_snapshot()
            elapsed = time.monotonic() - start
        assert elapsed <= MAX_READ_S
        assert not snap.unparsed or set(snap.unparsed) <= {"-prefs/card"}
    finally:
        emu.stop()


def test_snapshot_survives_packet_loss():
    emu = X32Emulator(drop_rate=0.02, seed=7).start()
    try:
        driver, transport = _driver(emu)
        with transport:
            snap = driver.read_snapshot()
        # One retry per node: a 2 % loss leaves at most a handful of gaps, never a crash.
        assert len(snap.unparsed) < 10
        assert snap.matrices[1].name in ("Main L", "")
    finally:
        emu.stop()


def test_one_late_reply_does_not_shift_the_rest_of_the_snapshot():
    emu = X32Emulator().start()
    # Later than both attempts of its own node (2 x 0.15 s): it arrives during later reads.
    emu.slow_nodes["ch/05/config"] = 0.4
    try:
        driver, transport = _driver(emu)
        with transport:
            snap = driver.read_snapshot()
        assert set(snap.unparsed) <= {"ch/05/config"}
        assert snap.matrices[3].name == "Sub buffer"
        assert len(snap.raw) >= len(snapshot_nodes()) - 1
    finally:
        emu.stop()


def test_usb_map_meters_and_link_quality(emulator):
    driver, transport = _driver(emulator)
    with transport:
        usb = driver.usb_audio_map(driver.read_snapshot())
        assert usb.input_info(10).channels == (10,)
        frames = list(driver.meters(MeterSet.STRIPS, 0.3))
        assert frames
        assert frames[0].channels[9] == pytest.approx(0.01)
        assert len(frames[0].matrices) == 6
        quality = driver.link_quality(count=5)
    assert quality.answered == 5
    assert quality.loss == 0.0
    assert quality.median_ms is not None


def test_link_quality_when_nothing_answers(emulator):
    driver, transport = _driver(emulator)
    emulator.stop()
    with transport:
        quality = driver.link_quality(count=2, timeout_s=0.05)
    assert quality.answered == 0
    assert quality.loss == 1.0
    assert quality.median_ms is None


def test_connect_errors(emulator):
    transport = UdpTransport(LOOPBACK, emulator.port)
    emulator.stop()
    with transport, pytest.raises(ConsoleUnreachable):
        X32Driver.connect(X32Client(transport), timeout_s=0.1)
    other = X32Emulator(model="XR18").start()
    try:
        with UdpTransport(LOOPBACK, other.port) as udp, pytest.raises(UnsupportedConsole):
            X32Driver.connect(X32Client(udp))
    finally:
        other.stop()


def test_discovery_finds_the_emulator_only_on_loopback(emulator):
    found = discover_consoles(0.3, extra=((LOOPBACK, emulator.port),))
    assert [(c.identity.model, c.port) for c in found] == [("X32P", emulator.port)]
    assert discover_consoles(0.05) == []  # dev-safe: no broadcast targets at all


def test_discovery_skips_other_consoles():
    other = X32Emulator(model="WING").start()
    try:
        assert discover_consoles(0.2, extra=((LOOPBACK, other.port),)) == []
    finally:
        other.stop()


def test_snapshot_time_stamp_comes_from_the_injected_clock(emulator):
    transport = UdpTransport(LOOPBACK, emulator.port)
    with transport:
        base = X32Driver.connect(X32Client(transport))
        driver = X32Driver(
            X32Client(transport), base.model, now=lambda: datetime(2026, 10, 14, 9, 0, 0)
        )
        assert driver.read_snapshot().taken_at == "2026-10-14T09:00:00"
