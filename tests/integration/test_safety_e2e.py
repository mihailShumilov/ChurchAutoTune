"""End-to-end safety checks against the emulator. Never weaken, skip or xfail these tests."""

import pytest

from churchtune.errors import ReadOnlyViolation


# ------------------------------------------------------------ ported from the original tool
def test_reader_refuses_writes(emu, client):
    for address, args in [
        ("/main/st/eq/2/g", (0.5,)),
        ("/ch/10/mix/on", (1,)),
        ("/main/st/mix/fader", (0.0,)),
        ("/headamp/041/phantom", (1,)),
        ("/save", ()),
        ("/-action/goscene", (1,)),
    ]:
        with pytest.raises(ReadOnlyViolation):
            client.send(address, *args)
    # Give the emulator a chance to log anything that slipped through before asserting.
    assert client.ping() is not None
    assert emu.writes == []
    assert emu.unknown == []
