"""The packaged X32 emulator, seeded by default from the dump of 2026-09-29 like the original tests."""

from churchtune_emulator.x32 import LOOPBACK
from churchtune_emulator.x32 import X32Emulator as _PackagedEmulator
from tests.fixtures.paths import DUMP_2026_09_29

__all__ = ["LOOPBACK", "X32Emulator"]


class X32Emulator(_PackagedEmulator):
    def __init__(self, dump_file=DUMP_2026_09_29, **options):
        super().__init__(dump_file, **options)
