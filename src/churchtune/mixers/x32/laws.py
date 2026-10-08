"""The X32/M32 value encoding as the write policy sees it (``safety.laws.ValueLaws``)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from churchtune.domain.changes import WireValue
from churchtune.domain.eq import Band, EqType
from churchtune.mixers.x32 import params

# Grid size by the last path segment of a continuous parameter.
STEPS_BY_KIND: Mapping[str, int] = {
    "f": params.FREQ_STEPS,
    "g": params.GAIN_STEPS,
    "q": params.Q_STEPS,
    "fader": params.FADER_STEPS,
    "time": params.DELAY_STEPS,
}


@dataclass(frozen=True, slots=True)
class X32ValueLaws:
    """Normalized values and type indices of the X32/M32, translated to real units."""

    @property
    def eq_types(self) -> Sequence[EqType]:
        """Band types in wire order."""
        return params.EQ_TYPES

    def gain_db(self, x_norm: float) -> float:
        """EQ gain in dB of a normalized value."""
        return params.norm_to_gain(x_norm)

    def fader_db(self, x_norm: float) -> float:
        """Fader level in dB of a normalized value."""
        return params.norm_to_fader(x_norm)

    def delay_ms(self, x_norm: float) -> float:
        """Output delay in ms of a normalized value."""
        return params.norm_to_delay(x_norm)

    def step_index(self, kind: str, x_norm: float) -> int:
        """Grid step of a normalized value of the parameter kind ``f``, ``g``, ``q``, ...

        Raises:
            ValueError: ``kind`` has no grid, or ``x_norm`` is not finite.
        """
        if kind not in STEPS_BY_KIND:
            raise ValueError(f"parameter kind {kind!r} has no value grid")
        return params.step_index(x_norm, STEPS_BY_KIND[kind])

    def band_wire_values(self, band: Band) -> tuple[Mapping[str, WireValue], Mapping[str, str]]:
        """Quantized wire values ``{type, f, g, q}`` of a band and their display strings."""
        values: dict[str, WireValue] = {
            "type": params.EQ_TYPES.index(band.type),
            "f": params.quantize_freq(params.freq_to_norm(band.freq_hz)),
            "g": params.quantize_gain(params.gain_to_norm(band.gain_db)),
            "q": params.quantize_q(params.q_to_norm(band.q)),
        }
        display = {
            "type": str(band.type),
            "f": params.fmt_freq(params.norm_to_freq(float(values["f"]))) + " Hz",
            "g": params.fmt_gain(params.norm_to_gain(float(values["g"]))) + " dB",
            "q": "Q " + params.fmt_q(params.norm_to_q(float(values["q"]))),
        }
        return values, display
