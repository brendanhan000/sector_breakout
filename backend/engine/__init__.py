"""Pure signal engine. No I/O, no database, no HTTP, no global state.

Every function here is a deterministic transform of its arguments. That
property is what makes the no-look-ahead regression test a real experiment
rather than a formality, so please keep it: if you find yourself wanting to read
a config file, query the database or call ``datetime.now()`` inside this
package, the value belongs in a parameter instead.
"""

from .params import (
    ATRParams,
    BetaParams,
    BreadthParams,
    ChannelParams,
    CorrelationParams,
    DispersionParams,
    EngineParams,
    RegimeParams,
    RRGParams,
    RVolParams,
    SectorSpec,
    StateParams,
    UniverseSpec,
)
from .state import LEGAL_TRANSITIONS, State
from .rrg import Quadrant

__all__ = [
    "ATRParams",
    "BetaParams",
    "BreadthParams",
    "ChannelParams",
    "CorrelationParams",
    "DispersionParams",
    "EngineParams",
    "RegimeParams",
    "RRGParams",
    "RVolParams",
    "SectorSpec",
    "StateParams",
    "UniverseSpec",
    "State",
    "LEGAL_TRANSITIONS",
    "Quadrant",
]
