"""
Exceptions for policy guards.

A leaf module importing only the base ```TokeoAiGuardError```, so a policy guard
can raise ```TokeoAiPolicyGuardError``` at the top level without a circular
import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiPolicyGuardError(TokeoAiGuardError):
    """
    Raised by a policy guard that stops the run hard at its stage.

    A hard abort, as opposed to a soft tool denial
    (```invocation.decision = DENY```), which only skips one call. The abort
    policy guard raises this at every stage; the deny policy guard raises it at
    the non-tool stages while a soft denial there is still undefined.
    """
