"""
Exceptions for truncate guards.

A leaf module importing only the base ```TokeoAiGuardError```, so a truncate
guard can raise ```TokeoAiTruncateGuardError``` at the top level without a
circular import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiTruncateGuardError(TokeoAiGuardError):
    """
    Raised by a truncate guard that needs to stop the run.

    A truncate guard normally only shortens text and never raises; this exists
    so a derived truncate guard that *does* need to abort (e.g. a required
    shortening step cannot be applied and the run must not carry the oversized
    payload on) has a typed error to raise, distinct from other guards' aborts.
    """
