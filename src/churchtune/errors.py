"""Domain errors.

Every error carries a user-facing i18n key and a context mapping; the UI renders
"what happened + what to do" from them (CODING_STANDARDS §6).
"""

from typing import ClassVar


class ChurchTuneError(Exception):
    """Base class for all errors raised by ChurchTune.

    Attributes:
        i18n_key: Key of the user-facing message in the locale catalogues.
        context: Values for the message placeholders and for logs.
    """

    i18n_key: ClassVar[str] = "err-unexpected"

    def __init__(self, message: str, **context: object) -> None:
        super().__init__(message)
        self.context: dict[str, object] = dict(context)


class DevSafeViolation(ChurchTuneError):
    """A real console or a real audio device was requested while dev-safe mode is on."""

    i18n_key: ClassVar[str] = "err-dev-safe-violation"


class ConsoleUnreachable(ChurchTuneError):
    """The console did not answer, or the network refused to carry the packet."""

    i18n_key: ClassVar[str] = "err-console-unreachable"


class ReadOnlyViolation(ChurchTuneError):
    """The read-only client was asked to send a message that could change the console."""

    i18n_key: ClassVar[str] = "err-read-only-violation"


class UnsupportedSchema(ChurchTuneError, ValueError):
    """The file was written by a newer version of ChurchTune, or is not a ChurchTune file."""

    i18n_key: ClassVar[str] = "err-unsupported-schema"


class UnsafeWrite(ChurchTuneError):
    """A write was refused by the safety policy; nothing was sent."""

    i18n_key: ClassVar[str] = "err-unsafe-write"


class LevelError(ChurchTuneError, ValueError):
    """A stimulus level above the hard output limit was requested; nothing is generated."""

    i18n_key: ClassVar[str] = "err-level-above-limit"


class AudioDeviceError(ChurchTuneError):
    """The audio device is missing, too small or cannot run at the needed sample rate."""

    i18n_key: ClassVar[str] = "err-audio-device"


class MeasurementAborted(ChurchTuneError):
    """Playback was interrupted; the output was silenced at once."""

    i18n_key: ClassVar[str] = "err-measurement-aborted"
