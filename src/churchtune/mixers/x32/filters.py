"""Cut filters and "crutch" shelves on a speaker path, read as evidence of its role."""

from collections.abc import Sequence

from churchtune.domain.eq import Band, EqType

FIRST_BAND, LAST_BAND = 1, 6
# A low cut this high is a crossover high-pass; lower ones protect a sub from subsonics.
HPF_RANGE_HZ = (55.0, 250.0)
SUBSONIC_RANGE_HZ = (15.0, 50.0)
LPF_RANGE_HZ = (50.0, 250.0)
# Shelves this deep act as filters ("crutches"; the reference sub matrix had a -15 dB high shelf).
CRUTCH_SHELF_DB = -10.0
CRUTCH_HIGH_SHELF_MAX_HZ = 1000.0
CRUTCH_LOW_SHELF_HZ = (60.0, 300.0)
MAIN_HPF_STARVES_SUB_HZ = 50.0
FIRST_BAND, LAST_BAND = 1, 6
_SLOPE_TYPES = frozenset(t for t in EqType if t.value[:2] in ("BU", "BS", "LR"))


def between(value: float, span: tuple[float, float]) -> bool:
    """True when ``value`` lies in the closed range ``span``."""
    return span[0] <= value <= span[1]


def is_low_cut(band: Band) -> bool:
    """True for a low cut: an LCut anywhere, or a slope filter on band 1."""
    return band.type is EqType.LCUT or (band.band == FIRST_BAND and band.type in _SLOPE_TYPES)


def has_hpf(eq: Sequence[Band]) -> Band | None:
    """The band that acts as a crossover high-pass (55-250 Hz), or None."""
    return next((b for b in eq if is_low_cut(b) and between(b.freq_hz, HPF_RANGE_HZ)), None)


def has_subsonic(eq: Sequence[Band]) -> Band | None:
    """A low cut below a crossover's range (15-50 Hz): sub protection, not a crossover."""
    return next((b for b in eq if is_low_cut(b) and between(b.freq_hz, SUBSONIC_RANGE_HZ)), None)


def has_lpf(eq: Sequence[Band]) -> Band | None:
    """The band that acts as a low-pass: a high cut, a cut filter on band 6 or a deep high shelf."""
    for band in eq:
        is_cut = band.type is EqType.HCUT or (band.band == LAST_BAND and band.type in _SLOPE_TYPES)
        if is_cut and between(band.freq_hz, LPF_RANGE_HZ):
            return band
        crutch = (
            band.type is EqType.HSHV
            and band.freq_hz <= CRUTCH_HIGH_SHELF_MAX_HZ
            and band.gain_db <= CRUTCH_SHELF_DB
        )
        if crutch:
            return band
    return None


def has_crutch_low_shelf(eq: Sequence[Band]) -> Band | None:
    """A deep low shelf (60-300 Hz, <= -10 dB) used instead of a high-pass on tops."""
    return next(
        (
            b
            for b in eq
            if b.type is EqType.LSHV
            and between(b.freq_hz, CRUTCH_LOW_SHELF_HZ)
            and b.gain_db <= CRUTCH_SHELF_DB
        ),
        None,
    )
