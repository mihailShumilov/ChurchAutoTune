"""OSC encoding, decoding and meter blobs.

``test_osc_roundtrip`` and ``test_meter_blob`` are ported from the original tool.
"""

import struct

import pytest
from hypothesis import given
from hypothesis import strategies as st

from churchtune.mixers.x32.codec import (
    SILENCE_DBFS,
    decode,
    encode,
    lin_to_db,
    meter_floats,
    same_addr,
)

osc_text = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=40)
osc_args = st.lists(
    st.one_of(
        st.integers(min_value=-(2**31), max_value=2**31 - 1),
        st.floats(width=32, allow_nan=False),
        osc_text,
        st.binary(max_size=64),
    ),
    max_size=6,
)


# ------------------------------------------------------------ ported from the original tool
def test_osc_roundtrip():
    address, args = decode(encode("/main/st/eq/2/f", 0.355))
    assert address == "/main/st/eq/2/f"
    assert args[0] == pytest.approx(0.355)

    address, args = decode(encode("node", "/main/st/eq/2 PEQ 232.3 -1.75 3.9\n"))
    assert address == "node"
    assert isinstance(args[0], str)
    assert args[0].startswith("/main/st/eq/2")

    assert same_addr("node", "/node")
    assert same_addr("/meters/0", "meters/0")


def test_meter_blob():
    values = [0.5, 1.0, 0.0]
    blob = struct.pack("<i", 3) + struct.pack("<3f", *values)

    _, args = decode(encode("/meters/0", blob))

    assert isinstance(args[0], bytes)
    assert meter_floats(args[0]) == pytest.approx(values)


# ------------------------------------------------------------ wire format
def test_encode_known_bytes_for_a_query_without_arguments():
    # Address padded to 8 bytes, then the type tag string "," padded to 4.
    assert encode("/xinfo") == b"/xinfo\0\0,\0\0\0"


def test_encode_known_bytes_for_int_and_float():
    assert encode("/a", 1) == b"/a\0\0,i\0\0\0\0\0\x01"
    assert encode("/a", 0.5) == b"/a\0\0,f\0\0\x3f\x00\x00\x00"


def test_encode_string_whose_length_is_a_multiple_of_four_gets_a_full_pad():
    assert encode("/abc", "wxyz") == b"/abc\0\0\0\0,s\0\0wxyz\0\0\0\0"


def test_encode_sends_bool_as_int():
    assert encode("/a", True) == encode("/a", 1)


def test_encode_rejects_unsupported_argument_types():
    with pytest.raises(TypeError, match="NoneType"):
        encode("/a", None)  # type: ignore[arg-type]


@given(address=osc_text.filter(lambda s: s and "\0" not in s), args=osc_args)
def test_decode_inverts_encode(address, args):
    decoded_address, decoded_args = decode(encode(address, *args))

    assert decoded_address == address
    assert decoded_args == args


@given(garbage=st.binary(max_size=200))
def test_decode_never_raises_on_garbage(garbage):
    address, args = decode(garbage)

    assert address is None or isinstance(address, str)
    assert isinstance(args, list)


@pytest.mark.parametrize("packet", [b"", b"abc", b"/a\0\0,f\0\0\x3f", b"/a\0\0,s\0\0abc"])
def test_decode_returns_none_for_truncated_packets(packet):
    assert decode(packet) == (None, [])


def test_decode_skips_unknown_type_tags():
    packet = b"/a\0\0,Ti\0" + struct.pack(">i", 7)

    assert decode(packet) == ("/a", [7])


def test_decode_address_only_packet():
    assert decode(b"/xinfo\0\0") == ("/xinfo", [])


# ------------------------------------------------------------ helpers
@pytest.mark.parametrize(
    ("received", "expected", "same"),
    [
        ("node", "/node", True),
        ("/node", "node", True),
        ("/meters/0", "/meters/0", True),
        ("/meters/1", "/meters/0", False),
        (None, "/node", False),
    ],
)
def test_same_addr(received, expected, same):
    assert same_addr(received, expected) is same


def test_meter_floats_clamps_count_to_available_data():
    blob = struct.pack("<i", 70) + struct.pack("<2f", 0.25, 0.5)

    assert meter_floats(blob) == [0.25, 0.5]


@pytest.mark.parametrize("blob", [b"", b"\x01\x00", struct.pack("<i", -3) + b"\0" * 8])
def test_meter_floats_is_empty_for_short_or_negative_count_blobs(blob):
    assert meter_floats(blob) == []


@pytest.mark.parametrize(
    ("level", "db"), [(1.0, 0.0), (0.1, -20.0), (0.0, SILENCE_DBFS), (-1.0, SILENCE_DBFS)]
)
def test_lin_to_db(level, db):
    assert lin_to_db(level) == pytest.approx(db)
