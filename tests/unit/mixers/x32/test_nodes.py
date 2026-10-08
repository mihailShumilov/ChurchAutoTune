"""Parsing of ``/node`` lines, checked against the real console dump.

``test_parse_main_eq_from_real_dump`` and ``test_display_roundtrip_all_eq_lines`` are ported from
the original tool.
"""

import pytest

from churchtune.domain.eq import Band, EqType
from churchtune.mixers.x32 import params as P  # noqa: N812 - short alias kept from the original
from churchtune.mixers.x32.nodes import (
    parse_band_line,
    parse_eq_from_dump,
    parse_eq_node_line,
    parse_eq_on_line,
)
from tests.fixtures.paths import DUMP_2026_09_29, DUMP_2026_10_02

DUMP = DUMP_2026_09_29.read_text(encoding="utf-8")


# ------------------------------------------------------------ ported from the original tool
def test_parse_main_eq_from_real_dump():
    eq = parse_eq_from_dump(DUMP)

    assert eq is not None
    assert eq.on is True
    assert [str(b.type) for b in eq.bands] == ["BU18", "PEQ", "PEQ", "PEQ", "PEQ", "HShv"]
    assert eq.bands[3].freq_hz == pytest.approx(1970)
    assert eq.bands[3].gain_db == -3.5
    assert not eq.bands[0].adjustable  # HPF band locked


def test_display_roundtrip_all_eq_lines():
    checked = 0
    for raw in DUMP.splitlines():
        if raw.startswith(("/main/st/eq/", "/bus/01/eq/")):
            line = raw.replace("/bus/01/", "/main/st/")
            band = parse_eq_node_line(line)
            x_norm = P.quantize_freq(P.freq_to_norm(band.freq_hz))
            assert P.fmt_freq(P.norm_to_freq(x_norm)) == line.split()[2]
            assert P.fmt_gain(P.snap_gain_db(band.gain_db)) == line.split()[3]
            checked += 1
    assert checked == 12


# ------------------------------------------------------------ added
def test_every_eq_band_line_of_both_dumps_parses_and_round_trips_through_the_display_format():
    checked = 0
    for dump in (DUMP_2026_09_29, DUMP_2026_10_02):
        for raw in dump.read_text(encoding="utf-8").splitlines():
            if not raw.startswith(("/main/st/eq/", "/mtx/", "/bus/")) or "/eq/" not in raw:
                continue
            prefix, band = parse_band_line(raw)
            shown = raw.split()
            assert raw.startswith(f"/{prefix}/{band.band} ")
            assert str(band.type) == shown[1]
            assert P.fmt_freq(P.snap_freq_hz(band.freq_hz)) == shown[2]
            assert P.fmt_gain(P.snap_gain_db(band.gain_db)) == shown[3]
            assert P.fmt_q(P.snap_q(band.q)) == shown[4]
            checked += 1
    # 1 main + 6 matrices + 16 buses, 6 bands each, in two dumps.
    assert checked == 2 * (1 + 6 + 16) * 6


def test_parse_main_eq_of_the_session_dump():
    eq = parse_eq_from_dump(DUMP_2026_10_02.read_text(encoding="utf-8"))

    assert eq is not None
    assert eq.band(2) == Band(2, EqType.PEQ, 496.6, -4.0, 1.2)
    assert eq.band(6) == Band(6, EqType.PEQ, 49.1, -3.0, 1.4)


@pytest.mark.parametrize(
    ("line", "prefix", "band"),
    [
        ("/mtx/03/eq/6 HShv 751.7 -15.0 2.0", "mtx/03/eq", Band(6, EqType.HSHV, 751.7, -15.0, 2.0)),
        ("mtx/01/eq/1 LCut 112.5 +0.00 2.0\n", "mtx/01/eq", Band(1, EqType.LCUT, 112.5, 0.0, 2.0)),
        ("/bus/16/eq/3 PEQ 1k97 -3.50 6.4", "bus/16/eq", Band(3, EqType.PEQ, 1970.0, -3.5, 6.4)),
        ("/main/st/eq/1 BU18 23.0 -7.75 7.8", "main/st/eq", Band(1, EqType.BU18, 23.0, -7.75, 7.8)),
    ],
)
def test_parse_band_line(line, prefix, band):
    assert parse_band_line(line) == (prefix, band)


@pytest.mark.parametrize(
    "line",
    [
        "/mtx/07/eq/1 PEQ 100.0 +0.00 2.0",
        "/mtx/03/eq/7 PEQ 100.0 +0.00 2.0",
        "/bus/17/eq/1 PEQ 100.0 +0.00 2.0",
        "/ch/01/eq/1 PEQ 100.0 +0.00 2.0",
        "/mtx/03/eq/1 PEQ 100.0 +0.00",
        "/mtx/03/mix ON -37.5",
        "",
    ],
)
def test_parse_band_line_rejects_other_lines(line):
    with pytest.raises(ValueError, match="not an EQ band line"):
        parse_band_line(line)


def test_parse_band_line_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown EQ type 'XYZ'"):
        parse_band_line("/mtx/03/eq/1 XYZ 100.0 +0.00 2.0")


@pytest.mark.parametrize("line", ["/mtx/03/eq/1 PEQ 100.0 +0.00 2.0", "/main/st/eq ON", "junk"])
def test_parse_eq_node_line_accepts_only_main_band_lines(line):
    with pytest.raises(ValueError, match="not a main EQ band line"):
        parse_eq_node_line(line)


@pytest.mark.parametrize(
    ("line", "on"),
    [("/main/st/eq ON", True), ("main/st/eq OFF", False), ("/main/st/eq on\n", True)],
)
def test_parse_eq_on_line(line, on):
    assert parse_eq_on_line(line) is on


@pytest.mark.parametrize("line", ["/main/st/eq", "/mtx/01/eq ON", ""])
def test_parse_eq_on_line_rejects_other_lines(line):
    with pytest.raises(ValueError, match="not a main EQ on/off line"):
        parse_eq_on_line(line)


def test_parse_eq_from_dump_is_none_when_incomplete():
    without_switch = "\n".join(f"/main/st/eq/{i} PEQ 100.0 +0.00 2.0" for i in range(1, 7))
    without_a_band = "/main/st/eq ON\n" + "\n".join(
        f"/main/st/eq/{i} PEQ 100.0 +0.00 2.0" for i in range(1, 6)
    )

    assert parse_eq_from_dump(without_switch) is None
    assert parse_eq_from_dump(without_a_band) is None
    assert parse_eq_from_dump("") is None
