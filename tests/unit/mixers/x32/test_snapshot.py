"""Parsing a full snapshot from the real dumps of the reference hall."""

import math

import pytest

from churchtune.domain.eq import EqType
from churchtune.domain.snapshot import (
    ConsoleFamily,
    ConsoleIdentity,
    InputKind,
    InputRef,
    OutputKind,
    SignalRef,
    SourceKind,
)
from churchtune.mixers.x32.snapshot import parse_snapshot, raw_from_dump, read_raw, snapshot_nodes
from tests.fixtures.paths import DUMP_2026_09_29, DUMP_2026_10_02, DumpReader

IDENTITY = ConsoleIdentity(ConsoleFamily.X32, "X32P", "X32P-00-00-00", "4.09", "192.0.2.80")


@pytest.fixture(scope="module")
def snap():
    raw = raw_from_dump(DUMP_2026_10_02.read_text(encoding="utf-8"))
    return parse_snapshot(IDENTITY, raw, "2026-10-02T18:00:00")


def test_matrices_names_levels_and_eq(snap):
    sub = snap.matrices[3]
    assert sub.name == "Sub buffer"
    assert sub.on
    assert sub.fader_db == pytest.approx(-37.5)
    assert sub.eq[0].type is EqType.LSHV
    assert sub.eq[5].type is EqType.HSHV
    assert sub.eq[5].freq_hz == pytest.approx(751.7)
    assert snap.matrices[1].name == "Main L"
    assert snap.matrices[1].eq[0].type is EqType.LCUT
    assert snap.matrices[4].name == ""


def test_main_strip_and_matrix_sends(snap):
    assert snap.main.on
    assert snap.main.fader_db == pytest.approx(2.0)
    assert len(snap.main.eq) == 6
    sends = {s.target: s for s in snap.main.matrix_sends}
    assert all(sends[n].open for n in range(1, 7))
    assert sends[5].level_db == pytest.approx(4.3)
    assert snap.mono is not None
    assert not snap.mono.on
    assert snap.mono.fader_db == -math.inf
    assert snap.mono_mode == "LR+M"


def test_outputs_sources(snap):
    by_number = {o.number: o for o in snap.outputs if o.kind is OutputKind.MAIN}
    assert by_number[6].source == SignalRef(SourceKind.MATRIX, 3)
    assert by_number[8].source == SignalRef(SourceKind.MATRIX, 1)
    assert by_number[7].source == SignalRef(SourceKind.MATRIX, 2)
    assert by_number[1].source == SignalRef(SourceKind.BUS, 1)
    assert by_number[3].source == SignalRef(SourceKind.MATRIX, 6)
    assert len(by_number) == 16
    assert snap.outputs_fed_by(SignalRef(SourceKind.MATRIX, 4)) == ()


def test_channels_sources_and_routing(snap):
    mic = snap.channels[10]
    assert mic.source == InputRef(InputKind.AES50_A, 10)
    assert not mic.on
    playback = snap.channels[23]
    assert playback.name == "PC"
    assert playback.source == InputRef(InputKind.CARD, 1)
    assert playback.on
    assert playback.to_main
    assert snap.channels[24].source == InputRef(InputKind.CARD, 2)
    assert snap.channels[13].source == InputRef(InputKind.LOCAL, 13)
    vocal = snap.channels[1]
    assert vocal.name == "Vocal 1"
    assert vocal.hpf_on
    assert vocal.hpf_hz == pytest.approx(189)
    assert vocal.eq[0].type is EqType.VEQ
    assert vocal.sends[0].on
    assert vocal.sends[0].level_db == pytest.approx(-4.8)


def test_usb_routing_and_headamps(snap):
    assert snap.usb.to_computer[9] == InputRef(InputKind.AES50_A, 10)
    assert len(snap.usb.to_computer) == 32
    assert snap.usb.from_computer[23] == 1
    assert snap.usb.from_computer[24] == 2
    assert snap.usb.channels == 32
    assert snap.headamps[41].phantom
    assert snap.headamps[41].gain_db == pytest.approx(50.0)
    assert not snap.headamps[40].phantom


def test_clock_links_and_raw(snap):
    assert snap.clock is not None
    assert snap.clock.fs_hz == 48000
    assert snap.clock.source == "INT"
    assert ("matrix", 1) in snap.stereo_links
    assert ("matrix", 5) in snap.stereo_links
    assert ("matrix", 3) not in snap.stereo_links
    assert snap.raw["mtx/03/mix"].startswith("/mtx/03/mix")


def test_missing_nodes_are_listed_not_fatal(snap):
    # The dump was taken before matrix polarity and output delays were part of the read.
    assert "mtx/01/preamp" in snap.unparsed
    assert all(o.delay is None for o in snap.outputs)
    assert not snap.matrices[1].invert


def test_malformed_lines_are_listed():
    raw = raw_from_dump(DUMP_2026_10_02.read_text(encoding="utf-8"))
    raw["mtx/03/mix"] = "/mtx/03/mix ON loud"
    raw["mtx/03/eq/2"] = "/mtx/03/eq/2 XYZ 100 0 1"
    raw["outputs/main/06"] = "/outputs/main/06 sub POST OFF"
    raw["headamp/041"] = "/headamp/041 high ON"
    raw["-prefs"] = "/-prefs nothing"
    snap = parse_snapshot(IDENTITY, raw, "t")
    for path in ("mtx/03/mix", "mtx/03/eq/2", "outputs/main/06", "headamp/041", "-prefs"):
        assert path in snap.unparsed
    assert not snap.matrices[3].on
    assert len(snap.matrices[3].eq) == 5
    assert snap.clock is None
    assert 41 not in snap.headamps


def test_both_real_dumps_parse_without_unexpected_gaps():
    for dump in (DUMP_2026_09_29, DUMP_2026_10_02):
        raw = raw_from_dump(dump.read_text(encoding="utf-8"))
        snap = parse_snapshot(IDENTITY, raw, "t")
        expected_missing = {f"mtx/{n:02d}/preamp" for n in range(1, 7)}
        unexpected = set(snap.unparsed) - expected_missing - {"-prefs", "-prefs/card"}
        assert not unexpected


def test_read_raw_reports_progress_and_skips_silent_nodes():
    reader = DumpReader(DUMP_2026_10_02)
    seen = []
    raw = read_raw(reader, lambda done, total: seen.append((done, total)))
    total = len(snapshot_nodes())
    assert seen[-1] == (total, total)
    assert len(seen) == total
    assert "mtx/03/mix" in raw
    assert "mtx/03/preamp" not in raw  # the dump has no answer for it


def test_snapshot_nodes_are_unique_and_include_polarity_and_delays():
    nodes = snapshot_nodes()
    assert len(nodes) == len(set(nodes))
    assert "mtx/03/preamp" in nodes
    assert "outputs/main/16/delay" in nodes
