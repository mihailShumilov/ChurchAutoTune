"""The reference hall (X32 Producer, measured 2026-10-02) as test data.

Hall-specific values live here and never in ``src/``.
"""

from churchtune.mixers.x32.laws import X32ValueLaws
from churchtune.safety.policy import WritePolicy

# Tops on matrices 1/2, sub on matrix 3, balcony on 5/6. Matrix 4 is unused and never written.
SUB_MATRIX = 3
TOP_MATRICES = (1, 2)
OTHER_MATRICES = (5, 6)
MATRICES = frozenset({SUB_MATRIX, *TOP_MATRICES, *OTHER_MATRICES})


def reference_policy(*, allow_unverified_writes: bool = False) -> WritePolicy:
    """Write policy of the reference hall.

    Args:
        allow_unverified_writes: Accept output-delay and polarity writes, which were never
            confirmed on hardware. Tests that exercise those paths ask for it explicitly.
    """
    return WritePolicy(X32ValueLaws(), MATRICES, allow_unverified_writes=allow_unverified_writes)


# venue.toml of the reference hall: measurement mic on USB input 10 (console channel 10,
# headamp 041), computer playback on channels 23/24.
REFERENCE_VENUE_TOML = """\
name = "Reference hall"
audio_device = "X-USB"
mic_usb_input = 10
mic_channel = 10
mic_headamp = 41
mic_label = "ECM8000"
playback_channels = [23, 24]
card_routing = "A1-8 A9-16 A17-24 A25-32"
firmware = "4.09"
"""
