"""
Exceptions for redact guards.

A leaf module importing only the base ```TokeoAiGuardError```, so a redact guard
can raise ```TokeoAiRedactGuardError``` at the top level without a circular
import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiRedactGuardError(TokeoAiGuardError):
    """
    Raised by a redact guard that needs to stop the run.

    A redact guard normally only masks and never raises; this exists so a
    derived redact guard that *does* need to abort (e.g. a required masking
    backend is unreachable and the run must not proceed unmasked) has a typed
    error to raise, distinct from other guards' aborts.
    """
