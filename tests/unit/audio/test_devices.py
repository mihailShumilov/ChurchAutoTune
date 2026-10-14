import pytest

from churchtune.audio.devices import list_devices, rank_devices, simulator_device
from churchtune.errors import DevSafeViolation

DEVICES = [
    {"name": "MacBook Pro Speakers", "max_input_channels": 0, "max_output_channels": 2, "default_samplerate": 48000.0},
    {"name": "Scarlett 2i2", "max_input_channels": 2, "max_output_channels": 2, "default_samplerate": 44100.0},
    {"name": "X-USB", "max_input_channels": 32, "max_output_channels": 32, "default_samplerate": 44100.0},
    {"name": "Dante Virtual Soundcard", "max_input_channels": 64, "max_output_channels": 64, "default_samplerate": 48000.0},
]  # fmt: skip


def test_console_card_comes_first_and_output_only_devices_are_skipped():
    ranked = rank_devices(DEVICES)
    assert [d.name for d in ranked] == ["X-USB", "Dante Virtual Soundcard", "Scarlett 2i2"]
    assert ranked[0].console_card
    assert ranked[0].problems == ("sample_rate",)
    assert ranked[2].problems == ("channels", "sample_rate")
    assert ranked[1].problems == ()


def test_simulator_is_a_full_device():
    assert simulator_device().problems == ()


def test_listing_real_devices_is_refused_in_dev_safe():
    with pytest.raises(DevSafeViolation):
        list_devices()


def test_listing_through_a_given_module():
    class Fake:
        def query_devices(self):
            return DEVICES

    assert list_devices(Fake())[0].name == "X-USB"
