"""
Exceptions for audit guards.

A leaf module importing only the base ```TokeoAiGuardError```, so an audit guard
can raise ```TokeoAiAuditGuardError``` at the top level without a circular
import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiAuditGuardError(TokeoAiGuardError):
    """
    Raised by an audit guard that needs to stop the run.

    An audit guard normally only observes and never raises; this exists so a
    derived audit guard that *does* need to abort (e.g. an external audit store
    is unreachable and the run must not proceed unrecorded) has a typed error to
    raise, distinct from other guards' aborts.
    """
