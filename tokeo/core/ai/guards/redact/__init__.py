"""
The ```redact``` guard package: the redact guard type and its implementations.

```TokeoAiRedactGuard``` (in ```base```) is the empty type to derive from;
```TokeoAiRegexRedactGuard``` (in ```regex```) is the ready-to-use implementation
that masks secret-looking spans by regex at the tool stages;
```TokeoAiRedactGuardError``` (in ```exc```) is the typed error a redact guard
raises to abort. All are re-exported here, so the short path
```tokeo.core.ai.guards.redact``` reaches each.
"""

from tokeo.core.ai.guards.redact.exc import TokeoAiRedactGuardError
from tokeo.core.ai.guards.redact.base import TokeoAiRedactGuard
from tokeo.core.ai.guards.redact.regex import TokeoAiRegexRedactGuard

__all__ = [
    'TokeoAiRedactGuardError',
    'TokeoAiRedactGuard',
    'TokeoAiRegexRedactGuard',
]
