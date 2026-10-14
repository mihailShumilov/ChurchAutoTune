"""USB audio map of an X32/M32: what each USB input carries, which channels each output feeds."""

from churchtune.domain.snapshot import (
    ConsoleSnapshot,
    InputKind,
    UsbAudioMap,
    UsbInputInfo,
    UsbOutputInfo,
)
from churchtune.mixers.x32.routing import headamp_index


def usb_audio_map(snapshot: ConsoleSnapshot) -> UsbAudioMap:
    """Build the USB audio map of a snapshot. Pure.

    A USB input that carries a physical input lists the channels that listen to the same source,
    so the wizard can keep a measurement microphone out of the speakers (SPEC S6).
    """
    inputs: list[UsbInputInfo] = []
    for usb, source in enumerate(snapshot.usb.to_computer, start=1):
        index = headamp_index(source)
        headamp = snapshot.headamps.get(index) if index is not None else None
        channels = tuple(
            n
            for n, ch in snapshot.channels.items()
            if source.kind not in (InputKind.UNKNOWN, InputKind.OFF) and ch.source == source
        )
        inputs.append(UsbInputInfo(usb, source, headamp, channels))
    feeds: dict[int, list[int]] = {}
    for number, channel in snapshot.channels.items():
        if channel.source.kind is InputKind.CARD:
            feeds.setdefault(channel.source.number, []).append(number)
    outputs = tuple(UsbOutputInfo(usb, tuple(feeds[usb])) for usb in sorted(feeds))
    return UsbAudioMap(tuple(inputs), outputs)
