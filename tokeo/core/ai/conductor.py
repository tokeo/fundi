"""
The conductor role: a governor that regulates by steering the run.

A conductor is a governor (see ```TokeoAiGovernor``` for the shared mechanic: the
stages, override-to-participate, the per-stage config, reflection). It shares that
mechanic with the guard and the transformer and narrows the contract to
**steering**: it directs how the run proceeds -- ordering, redirecting, driving
the flow at its stages -- rather than only checking (the guard) or only reshaping
(the transformer). A conductor never denies a single call the way a guard does;
it shapes the course of the run.

## Do not derive from this class directly

Write a conductor by deriving from one of the conductor *types*, not from
```TokeoAiConductor``` itself. The type states the sub-role and the contract your
subclass keeps. Deriving straight from ```TokeoAiConductor``` makes a conductor
with no declared type -- avoid it.
"""

from tokeo.core.ai.governor import TokeoAiGovernor


class TokeoAiConductor(TokeoAiGovernor):
    """
    The conductor role: a governor that regulates by steering the run.

    A conductor shares the whole governor mechanic and narrows the contract to
    steering: it directs how the run proceeds at its stages rather than only
    checking or only reshaping. Derive from a conductor *type*, not from this
    class directly; see the module docstring.

    """
