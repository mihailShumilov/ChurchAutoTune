"""OSC wire format as the X32/M32 speaks it: encoding, decoding and meter blobs. Pure, no IO.

Gotchas seen on a real console (fw 4.09):
  * the reply to ``/node`` arrives with the address ``node`` (no leading slash), so addresses
    are compared with :func:`same_addr`;
  * meter blobs hold a little-endian int32 count followed by that many little-endian float32
    values, linear (1.0 = 0 dBFS), although OSC itself is big-endian.
"""

import math
import struct

OscArg = int | float | str | bytes

# Level reported for a meter value of zero or below, in dBFS.
SILENCE_DBFS = -200.0

_ALIGN = 4
_INT32 = struct.Struct(">i")
_FLOAT32 = struct.Struct(">f")
_LE_INT32 = struct.Struct("<i")


def _pad(raw: bytes) -> bytes:
    """Terminate an OSC string with at least one NUL and pad it to a multiple of four bytes."""
    return raw + b"\0" * (_ALIGN - len(raw) % _ALIGN)


def encode(address: str, *args: OscArg) -> bytes:
    """Encode one OSC message.

    Args:
        address: OSC address, for example ``/main/st/eq/2/f``.
        *args: Arguments; ``bool`` is sent as int, ``int`` as int32, ``float`` as float32,
            ``bytes`` as a blob and ``str`` as a string.

    Returns:
        The datagram payload.

    Raises:
        TypeError: An argument has a type OSC cannot carry here.
    """
    tags = ","
    data = b""
    for arg in args:
        if isinstance(arg, bool | int):
            tags += "i"
            data += _INT32.pack(int(arg))
        elif isinstance(arg, float):
            tags += "f"
            data += _FLOAT32.pack(arg)
        elif isinstance(arg, bytes):
            tags += "b"
            data += _INT32.pack(len(arg)) + arg + b"\0" * (-len(arg) % _ALIGN)
        elif isinstance(arg, str):
            tags += "s"
            data += _pad(arg.encode())
        else:
            raise TypeError(f"cannot encode OSC argument of type {type(arg).__name__}")
    return _pad(address.encode()) + _pad(tags.encode()) + data


def _read_str(buf: bytes, start: int) -> tuple[str, int]:
    end = buf.index(b"\0", start)
    return buf[start:end].decode(errors="replace"), (end + _ALIGN) & ~(_ALIGN - 1)


def decode(buf: bytes) -> tuple[str | None, list[OscArg]]:
    """Decode one OSC message.

    Args:
        buf: A received datagram.

    Returns:
        ``(address, args)``; ``(None, [])`` when the datagram is not a well-formed message.
        Arguments with an unknown type tag are skipped.
    """
    try:
        address, pos = _read_str(buf, 0)
        if pos >= len(buf):
            return address, []
        tags, pos = _read_str(buf, pos)
    except ValueError:
        return None, []
    args: list[OscArg] = []
    try:
        for tag in tags[1:]:
            if tag == "s":
                text, pos = _read_str(buf, pos)
                args.append(text)
            elif tag == "i":
                args.append(_INT32.unpack_from(buf, pos)[0])
                pos += _ALIGN
            elif tag == "f":
                args.append(_FLOAT32.unpack_from(buf, pos)[0])
                pos += _ALIGN
            elif tag == "b":
                size = _INT32.unpack_from(buf, pos)[0]
                pos += _ALIGN
                args.append(buf[pos : pos + size])
                pos += (size + _ALIGN - 1) & ~(_ALIGN - 1)
    except (struct.error, ValueError):
        return None, []
    return address, args


def same_addr(received: str | None, expected: str) -> bool:
    """Compare two OSC addresses, ignoring leading slashes (``node`` equals ``/node``)."""
    return received is not None and received.lstrip("/") == expected.lstrip("/")


def meter_floats(blob: bytes) -> list[float]:
    """Decode a meter blob into linear levels (1.0 = 0 dBFS).

    The count in the blob is clamped to the number of values actually present.
    """
    if len(blob) < _LE_INT32.size:
        return []
    count = _LE_INT32.unpack_from(blob, 0)[0]
    count = max(0, min(count, (len(blob) - _LE_INT32.size) // _ALIGN))
    return list(struct.unpack_from(f"<{count}f", blob, _LE_INT32.size))


def lin_to_db(level: float) -> float:
    """Convert a linear meter level to dBFS; zero and below map to ``SILENCE_DBFS``."""
    return 20 * math.log10(level) if level > 0 else SILENCE_DBFS
