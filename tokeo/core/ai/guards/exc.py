"""
Exceptions for guards.

A leaf module: it imports only ```TokeoAiError``` from the ai package's own leaf
```exc``` module, so any guard module can import ```TokeoAiGuardError``` at the
top level without a circular import. Each guard package adds its own typed
subclass in its own ```exc``` module (e.g. ```audit/exc.py```), one level deep.
"""

from tokeo.core.ai.exc import TokeoAiError


class TokeoAiGuardError(TokeoAiError):
    """
    Base for errors raised by a guard.

    A guard raises this (or a typed subclass) to stop the run hard at its stage,
    as opposed to a soft tool denial (```invocation.decision = DENY```), which
    only skips one call and lets the loop continue. Catch ```TokeoAiGuardError```
    to handle any guard abort; catch a typed subclass to handle one kind.
    """
