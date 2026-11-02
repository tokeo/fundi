"""
Exceptions for confirm guards.

A leaf module importing only the base ```TokeoAiGuardError```, so a confirm
guard can raise ```TokeoAiConfirmGuardError``` at the top level without a
circular import.
"""

from tokeo.core.ai.guards.exc import TokeoAiGuardError


class TokeoAiConfirmGuardError(TokeoAiGuardError):
    """
    Raised by a confirm guard that needs to stop the run.

    A confirm guard normally pauses for a human decision and then lets the run
    continue with that answer; this exists so a derived confirm guard that
    *does* need to abort (e.g. the human declined and the run must not proceed,
    or no input channel is available to ask on) has a typed error to raise,
    distinct from other guards' aborts.
    """


class TokeoAiConfirmRetry(Exception):
    """
    Raised by a confirm guard on the answer no_retry.

    Not an error: the loop catches it and runs the stage again, so its guards
    judge anew with the note in ```ctx.notes```.

    ### Args

    - **subject**: The work object of the stage, or ```None``` for the
        messages

    """

    def __init__(self, subject=None):
        super().__init__('retried in a confirm')
        self.subject = subject
