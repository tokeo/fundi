"""
Exceptions for the ai subsystem.

A leaf module: it imports only ```tokeo.core.exc.TokeoError``` and nothing from
the ai package, so any ai module can import ```TokeoAiError``` from here at the
top level without a circular import. The package facade
(```tokeo.core.ai```) re-exports ```TokeoAiError```, so the short path
```from tokeo.core.ai import TokeoAiError``` keeps working.
"""

from tokeo.core.exc import TokeoError


class TokeoAiError(TokeoError):
    """Raised when an ai profile, provider, or resource cannot be resolved."""


class TokeoAiRunDeclined(TokeoAiError):
    """
    Raised by a governor that declines the whole run.

    The loop catches it at any stage and ends the run in order: the result
    carries the status ```declined``` with the stage, the reason and who
    decided, instead of an error. To refuse a single call at the tool stages, a
    guard denies it instead and the run goes on; a confirm raises this there
    only when its retries ran out.

    ### Args

    - **stage** (str): The stage the run was declined at
    - **reason** (str): Why it was declined
    - **by** (str|None): Who decided (```human```, ```auto```, ```default```)

    """

    def __init__(self, stage, reason, by=None):
        super().__init__(f'run declined at {stage}: {reason}')
        self.stage = stage
        self.reason = reason
        self.by = by
