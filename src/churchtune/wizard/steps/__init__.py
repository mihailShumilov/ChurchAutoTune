"""The wizard steps in SPEC order (S0-S14)."""

from churchtune.wizard.engine import Step
from churchtune.wizard.steps.apply import Apply
from churchtune.wizard.steps.finish import Finish, Results
from churchtune.wizard.steps.junction import Junction
from churchtune.wizard.steps.measure import (
    Calibration,
    Level,
    MeasurePoints,
    Microphones,
    PlaybackPath,
)
from churchtune.wizard.steps.setup import (
    Connect,
    Equipment,
    MacCheck,
    ReadConsole,
    Topology,
    Welcome,
)
from churchtune.wizard.steps.verify import Verify


def all_steps() -> list[Step]:
    """Every step, in order."""
    return [
        Welcome(),
        MacCheck(),
        Connect(),
        ReadConsole(),
        Topology(),
        Equipment(),
        Microphones(),
        PlaybackPath(),
        Level(),
        Calibration(),
        MeasurePoints(),
        Junction(),
        Results(),
        Apply(),
        Verify(),
        Finish(),
    ]
