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
    Raised by a governor that declines the run at a stage without a tool call.

    The loop catches it and ends the run in order: the result carries the
    status ```declined``` with the stage, the reason and who decided, instead of
    an error. At the tool stages a call is denied instead, the run goes on.

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
