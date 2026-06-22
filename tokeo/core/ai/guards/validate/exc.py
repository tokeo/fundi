"""
Exceptions for validate guards.

A leaf module importing only the base ```TokeoAiGuardError```, so a validate
guard can raise ```TokeoAiValidateGuardError``` at the top level without a
circular import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiValidateGuardError(TokeoAiGuardError):
    """
    Raised by a validate guard that needs to stop the run.

    A validate guard normally only flags or denies a single call and lets the
    loop continue (deny-and-continue); this exists so a derived validate guard
    that *does* need to abort the whole run has a typed error to raise, distinct
    from other guards' aborts.
    """
