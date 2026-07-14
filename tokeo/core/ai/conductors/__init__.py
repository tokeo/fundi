"""
Conductor derivations: steer the run.

The base conductor exception ```TokeoAiConductorError``` is re-exported here, so
the short path ```from tokeo.core.ai.conductors import TokeoAiConductorError```
reaches it.

No ready-to-use conductor ships in the core yet; the shared mechanic is on the
`tokeo.core.ai.governor.TokeoAiGovernor` base class, the role contract is below.

.. include:: ./CONDUCTORS.md
"""

from tokeo.core.ai.conductors.exc import TokeoAiConductorError

__all__ = [
    'TokeoAiConductorError',
]
