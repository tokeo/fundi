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
