"""
The conductor role: a governor whose character is directing the run.

A conductor is a governor (see ```TokeoAiGovernor``` for the shared mechanic: the
stages, override-to-participate, the per-stage config, reflection). It shares that
mechanic with the guard and the transformer; its character is **directing**:
it steers how the run proceeds -- ordering, redirecting, driving the flow at
its stages -- rather than securing (the guard) or reshaping (the
transformer). Shaping the course of the run is what it is for; the
implementation decides what it does.

## Do not derive from this class directly

Write a conductor by deriving from one of the conductor *types*, not from
```TokeoAiConductor``` itself. The type states the sub-role and the contract your
subclass keeps. Deriving straight from ```TokeoAiConductor``` makes a conductor
with no declared type -- avoid it.
"""

from tokeo.core.ai.governor import TokeoAiGovernor


class TokeoAiConductor(TokeoAiGovernor):
    """
    The conductor role: a governor whose character is directing the run.

    A conductor shares the whole governor mechanic; its character is directing:
    it steers how the run proceeds at its stages rather than securing or
    reshaping. Derive from a conductor *type*, not from this class directly; see
    the module docstring.

    """
