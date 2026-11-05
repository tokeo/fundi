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


class TokeoAiRunDeclinedError(TokeoAiError):
    """
    Raised when a governor declines the whole run.

    A governor raises it at any stage; ```chat``` ends the run in order and
    raises it on to the caller, with the run so far in ```result```: the trace,
    the counters and the confirmations, but no answer, since a declined answer
    must not be used by mistake (it stays on the trace). To refuse a single
    call at the tool stages, a guard denies it instead and the run goes on; a
    confirm raises this there only when its retries ran out.

    ### Args

    - **stage** (str): The stage the run was declined at
    - **reason** (str): Why it was declined
    - **by** (str|None): Who decided (```human```, ```auto```, ```default```)
    - **result** (TokeoAiResult|None): The run so far, set by ```chat```

    """

    def __init__(self, stage, reason, by=None, result=None):
        super().__init__(f'run declined at {stage}: {reason}')
        #: (str) The stage the run was declined at
        self.stage = stage
        #: (str) Why it was declined
        self.reason = reason
        #: (str|None) Who decided: human, auto or default
        self.by = by
        #: (TokeoAiResult|None) The run so far: trace, loopdata, confirmations
        self.result = result
