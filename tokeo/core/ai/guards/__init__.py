"""
Guard derivations: one check per module, around one tool call.

The base guard exception ```TokeoAiGuardError``` is re-exported here, so the
short path ```from tokeo.core.ai.guards import TokeoAiGuardError``` reaches it.

The full reference for writing a guard -- the stages, the write contract for a
result-changing guard, and the memory note -- is the included guide below.

.. include:: ./GUARDS.md
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError

__all__ = [
    'TokeoAiGuardError',
]
