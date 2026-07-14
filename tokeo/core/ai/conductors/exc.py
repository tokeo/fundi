"""
Exceptions for conductors.

A leaf module: it imports only ```TokeoAiError``` from the ai package's own leaf
```exc``` module, so any conductor module can import ```TokeoAiConductorError```
at the top level without a circular import. Each conductor package adds its own
typed subclass in its own ```exc``` module, one level deep.
"""

from tokeo.core.ai.exc import TokeoAiError


class TokeoAiConductorError(TokeoAiError):
    """
    Base for errors raised by a conductor.

    A conductor steers the run; it raises this (or a typed subclass) to stop the
    run hard at its stage. Catch ```TokeoAiConductorError``` to handle any
    conductor abort; catch a typed subclass to handle one kind.
    """
