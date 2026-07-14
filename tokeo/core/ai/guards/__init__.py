"""
Guard derivations: one check per module, around one tool call.

The base guard exception ```TokeoAiGuardError``` is re-exported here, so the
short path ```from tokeo.core.ai.guards import TokeoAiGuardError``` reaches it.

The guard-role contract (the deny power, the guard types) is the included guide
below; the shared governor mechanic it builds on -- the stages, the write contract
for a result-changing step, coherence, the memory note -- is on the
`tokeo.core.ai.governor.TokeoAiGovernor` base class.

.. include:: ./GUARDS.md
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError

__all__ = [
    'TokeoAiGuardError',
]
