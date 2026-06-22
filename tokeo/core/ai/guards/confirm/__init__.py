"""
The ```confirm``` guard package: the confirm guard type and its error.

```TokeoAiConfirmGuard``` (in ```base```) is the empty type to derive from;
```TokeoAiConfirmGuardError``` (in ```exc```) is the typed error a confirm guard
raises to abort. Both are re-exported here, so the short path
```tokeo.core.ai.guards.confirm``` reaches each.

No ready-to-use implementation ships here yet: the confirm machinery (the input
channel, the answer levels, the remembering of choices) is not built. The type
and its error are available now so a guard can already be typed as a confirm and
derived from in a project.
"""

from tokeo.core.ai.guards.confirm.exc import TokeoAiConfirmGuardError
from tokeo.core.ai.guards.confirm.base import TokeoAiConfirmGuard

__all__ = [
    'TokeoAiConfirmGuardError',
    'TokeoAiConfirmGuard',
]
