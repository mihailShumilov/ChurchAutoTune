"""Choosing the audio backend. In dev-safe mode only the simulator can be opened (SPEC §9.1)."""

from collections.abc import Callable

from churchtune.audio.base import AudioBackend
from churchtune.audio.fake.backend import FakeBackend
from churchtune.audio.fake.room import demo_rooms
from churchtune.domain.eq import SystemEq
from churchtune.errors import DevSafeViolation
from churchtune.platform.devsafe import is_dev_safe

FAKE = "fake"
DEFAULT_FAKE_NOISE_DBFS = -80.0


def open_backend(
    audio: str,
    *,
    fs_hz: float,
    mic_input: int,
    eq_provider: Callable[[], SystemEq | None] | None = None,
    fake_noise_dbfs: float = DEFAULT_FAKE_NOISE_DBFS,
) -> AudioBackend:
    """Open the simulator (``audio == "fake"``) or a real device by name.

    Args:
        audio: ``fake`` or part of a device name.
        fs_hz: Sample rate.
        mic_input: 1-based input channel of the measurement mic (real device only).
        eq_provider: Returns the console's current Main LR EQ for the simulator.
        fake_noise_dbfs: Mic noise of the simulator.

    Returns:
        The backend.

    Raises:
        DevSafeViolation: Dev-safe mode is on and a real device was requested; no device is
            touched.
        AudioDeviceError: The real device cannot be opened.
    """
    if audio.strip().lower() == FAKE:
        return FakeBackend(
            demo_rooms(fs_hz), fs_hz, noise_dbfs=fake_noise_dbfs, eq_provider=eq_provider
        )
    if is_dev_safe():
        raise DevSafeViolation(
            f"dev-safe mode refuses the audio device {audio!r}; only 'fake' is allowed",
            audio=audio,
        )
    # Imported here so that the simulator never needs PortAudio.
    from churchtune.audio.sounddevice_backend import SoundDeviceBackend  # noqa: PLC0415

    return SoundDeviceBackend(audio, fs_hz, mic_input)
