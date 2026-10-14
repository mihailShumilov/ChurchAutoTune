"""The in-process demo of ``--emulator``: the emulator and the simulated hall work together.

A person at a real console mutes the measurement microphones' channels when the wizard asks;
in the demo nobody can press the emulator's buttons, so the demo mutes them once at the start.
The simulated hall then hears the console: the playback goes through Main LR's EQ and each
speaker sounds only while its matrix is switched on, so writes, mutes and the check measurement
behave as in a hall. Development only (loopback emulator, simulated audio).
"""

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from churchtune.audio.fake.hall import FakeHall
from churchtune.domain.eq import SystemEq
from churchtune.domain.snapshot import SourceKind
from churchtune.mixers.x32.client import X32Client
from churchtune.mixers.x32.driver import X32Driver
from churchtune.mixers.x32.transport import UdpTransport

LOOPBACK = "127.0.0.1"


class DemoConsole(Protocol):
    """What the demo needs from the in-process emulator.

    The emulator package itself is loaded lazily by the entry point (``cli.main``).
    """

    @property
    def port(self) -> int:
        """The UDP port it listens on."""
        ...

    @property
    def mtx(self) -> Mapping[int, Mapping[str, Any]]:
        """Matrix states by number ("on", "fader", ...)."""
        ...

    def press_mute(self, channel: int) -> None:
        """A person pressing MUTE on an input channel."""
        ...

    def main_eq(self) -> SystemEq:
        """The Main LR EQ as the emulator holds it."""
        ...


# USB inputs of the demo rig's microphones (audio.fake.hall.demo_rig).
DEMO_MIC_INPUTS = (10, 11, 12, 13)

HallLink = Callable[[object], None]


def prepare_demo(emulator: DemoConsole) -> HallLink:
    """Mute the demo microphones' channels and return the link for the simulated hall."""
    with UdpTransport(LOOPBACK, emulator.port) as transport:
        driver = X32Driver.connect(X32Client(transport))
        snapshot = driver.read_snapshot()
        usb = driver.usb_audio_map(snapshot)
        topology = driver.infer_topology(snapshot)
    for usb_input in DEMO_MIC_INPUTS:
        entry = usb.input_info(usb_input)
        for channel in entry.channels if entry is not None else ():
            emulator.press_mute(channel)
    tops = [r.number for r in topology.tops if r.kind is SourceKind.MATRIX]
    subs = [r.number for r in topology.subs if r.kind is SourceKind.MATRIX]

    def switch(matrices: list[int], index: int) -> float:
        if not matrices:
            return 1.0
        return float(emulator.mtx[matrices[min(index, len(matrices) - 1)]]["on"])

    def link(backend: object) -> None:
        if not isinstance(backend, FakeHall):
            return
        backend.main_eq = emulator.main_eq
        backend.speaker_gains = lambda: {
            "top_l": switch(tops, 0),
            "top_r": switch(tops, 1),
            "sub": switch(subs, 0),
        }

    return link
