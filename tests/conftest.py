import os

import pytest

# Tests never reach a real console or a real audio device (SPEC §9.1). Set before anything from
# churchtune reads the environment.
os.environ["CTUNE_DEV_SAFE"] = "1"

from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.transport import UdpTransport
from tests.emulator.x32_emulator import LOOPBACK, X32Emulator


@pytest.fixture
def emu():
    emulator = X32Emulator().start()
    yield emulator
    emulator.stop()


@pytest.fixture
def transport(emu):
    with UdpTransport(LOOPBACK, emu.port) as udp:
        yield udp


@pytest.fixture
def client(transport):
    return X32Client(transport)
