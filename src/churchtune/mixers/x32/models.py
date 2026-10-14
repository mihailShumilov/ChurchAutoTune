"""X32 / M32 model strings, firmware lines and what may be written on each.

Model strings are the fourth argument of the ``/xinfo`` answer. Only ``X32P`` with firmware 4.09 was
seen on hardware; the other strings are from documentation (UNVERIFIED, docs/hardware-checklist.md
#6, #18). Capabilities start at ``suggest_only`` and move to ``full`` only together with a filled
row of the hardware checklist.
"""

from churchtune.domain.snapshot import ConsoleFamily, ConsoleIdentity
from churchtune.errors import UnsupportedConsole
from churchtune.mixers.base import Capabilities, ConsoleModel, Feature, Support

X32_MODELS = frozenset({"X32", "X32C", "X32P", "X32RACK", "X32CORE"})
M32_MODELS = frozenset({"M32", "M32R", "M32C"})
VERIFIED_FIRMWARE_MAJOR = "4"
XINFO_FIELDS = 4

# Written and read back on an X32 Producer with firmware 4.09 (skill x32-osc, session 2026-10-02).
_X32_VERIFIED: frozenset[Feature] = frozenset(
    {Feature.MAIN_EQ, Feature.MATRIX_EQ, Feature.MATRIX_MUTE, Feature.MATRIX_FADER}
)


def family_of(model: str) -> ConsoleFamily:
    """Console family of a model string.

    Raises:
        UnsupportedConsole: The model is not an X32 or an M32.
    """
    name = model.strip().upper()
    if name in X32_MODELS or name.startswith("X32"):
        return ConsoleFamily.X32
    if name in M32_MODELS or name.startswith("M32"):
        return ConsoleFamily.M32
    raise UnsupportedConsole(f"console model {model!r} is not an X32 or an M32", model=model)


def identity_from_xinfo(values: list[object], host: str) -> ConsoleIdentity:
    """Build an identity from an ``/xinfo`` answer ``[ip, name, model, firmware]``.

    Args:
        values: Arguments of the answer.
        host: Address the answer came from; preferred over the address inside the answer.

    Raises:
        UnsupportedConsole: The answer is malformed or names another console.
    """
    if len(values) < XINFO_FIELDS or not all(isinstance(v, str) for v in values[:XINFO_FIELDS]):
        raise UnsupportedConsole(f"unexpected /xinfo answer {values!r}", answer=repr(values))
    _ip, name, model, firmware = (str(v) for v in values[:XINFO_FIELDS])
    return ConsoleIdentity(family_of(model), model, name, firmware, host)


def is_verified_firmware(firmware: str) -> bool:
    """True for the firmware line the protocol was checked on (4.x)."""
    return firmware.split(".", 1)[0] == VERIFIED_FIRMWARE_MAJOR


def capabilities(identity: ConsoleIdentity) -> Capabilities:
    """What may be written on this console.

    Only the X32 family on firmware 4.x has verified writes; everything else is suggest-only.
    """
    verified_fw = is_verified_firmware(identity.firmware)
    verified = (
        _X32_VERIFIED if identity.family is ConsoleFamily.X32 and verified_fw else frozenset()
    )
    support = {f: Support.FULL if f in verified else Support.SUGGEST_ONLY for f in Feature}
    return Capabilities(support, verified_fw)


def console_model(identity: ConsoleIdentity) -> ConsoleModel:
    """Identity together with its capabilities."""
    return ConsoleModel(identity, capabilities(identity))
