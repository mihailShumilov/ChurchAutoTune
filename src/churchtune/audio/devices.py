"""Finding the console's USB audio device (SPEC S1, §17).

The card names are known only from documentation (X-USB, X-LIVE on the X32; DN32-USB, DN32-LIVE
on the M32); the real name is recorded in the venue once the person confirms it. Candidates are
ranked by name, channel count and default sample rate; ranking is pure and tested, listing needs
PortAudio and is refused in dev-safe mode.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from churchtune.errors import DevSafeViolation
from churchtune.platform.devsafe import is_dev_safe

# UNVERIFIED: CoreAudio names of the cards other than "X-USB" (docs/hardware-checklist.md #19).
CARD_NAMES = ("x-usb", "x-live", "dn32-usb", "dn32-live", "x32", "m32")
CONSOLE_CHANNELS = 32
WANTED_FS_HZ = 48000.0
SIMULATOR = "fake"


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """One audio device as the operator sees it.

    Attributes:
        index: PortAudio index (-1 for the simulator).
        name: Device name.
        inputs: Input channels.
        outputs: Output channels.
        default_fs_hz: Default sample rate.
        score: Ranking score; higher is a better console candidate.
        console_card: True when the name looks like a console USB card.
    """

    index: int
    name: str
    inputs: int
    outputs: int
    default_fs_hz: float
    score: int
    console_card: bool

    @property
    def problems(self) -> tuple[str, ...]:
        """Codes of what keeps this device from being a full console card."""
        found = []
        if self.inputs < CONSOLE_CHANNELS or self.outputs < CONSOLE_CHANNELS:
            found.append("channels")
        if self.default_fs_hz != WANTED_FS_HZ:
            found.append("sample_rate")
        return tuple(found)


def rank_devices(devices: Sequence[Mapping[str, Any]]) -> list[AudioDevice]:
    """Rank devices from ``sounddevice.query_devices()``: console cards first, then by fit."""
    ranked = []
    for index, device in enumerate(devices):
        name = str(device["name"])
        inputs, outputs = int(device["max_input_channels"]), int(device["max_output_channels"])
        if inputs == 0 or outputs == 0:
            continue  # playback and recording must share one duplex device
        fs_hz = float(device["default_samplerate"])
        card = any(token in name.lower() for token in CARD_NAMES)
        score = (
            100 * card
            + 10 * (inputs >= CONSOLE_CHANNELS and outputs >= CONSOLE_CHANNELS)
            + 5 * (fs_hz == WANTED_FS_HZ)
        )
        ranked.append(AudioDevice(index, name, inputs, outputs, fs_hz, score, card))
    return sorted(ranked, key=lambda d: (-d.score, d.index))


def simulator_device() -> AudioDevice:
    """The simulated hall, the only device in dev-safe mode."""
    return AudioDevice(-1, SIMULATOR, CONSOLE_CHANNELS, CONSOLE_CHANNELS, WANTED_FS_HZ, 0, False)


def list_devices(sd: Any = None) -> list[AudioDevice]:  # noqa: ANN401 - sounddevice-like module
    """Every duplex device, best console candidate first.

    Raises:
        DevSafeViolation: Dev-safe mode is on and no fake module was given; PortAudio is never
            loaded then.
    """
    if sd is None:
        if is_dev_safe():
            raise DevSafeViolation("dev-safe mode refuses to list real audio devices")
        from churchtune.audio.sounddevice_backend import load_sounddevice  # noqa: PLC0415 - lazy PortAudio

        sd = load_sounddevice()
    return rank_devices(list(sd.query_devices()))
