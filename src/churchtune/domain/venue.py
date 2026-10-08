"""What is specific to one hall: where the console is and how the measurement rig is patched.

Nothing in the package assumes these values; they come from ``venue.toml`` or command options.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Venue:
    """A hall's console and measurement patch. Every field is optional knowledge.

    Attributes:
        name: Name of the hall.
        console_host: IPv4 address of the console.
        console_port: UDP port of the console.
        audio_device: Part of the name of the console's USB audio device.
        mic_usb_input: 1-based USB input that carries the measurement mic.
        mic_channel: Console channel fed by the measurement mic (must stay muted).
        mic_headamp: Headamp number of the measurement mic (for gain and phantom checks).
        mic_label: Microphone model, for reports.
        playback_channels: Console channels fed by the computer's playback.
        card_routing: Expected USB card routing, as the console prints it.
        firmware: Firmware the OSC formulas were verified on in this hall.
    """

    name: str = ""
    console_host: str | None = None
    console_port: int | None = None
    audio_device: str | None = None
    mic_usb_input: int | None = None
    mic_channel: int | None = None
    mic_headamp: int | None = None
    mic_label: str = ""
    playback_channels: tuple[int, ...] = ()
    card_routing: str | None = None
    firmware: str | None = None
