"""
The ```truncate``` guard package: the truncate guard type and its error.

```TokeoAiTruncateGuard``` (in ```base```) is the empty type to derive from;
```TokeoAiTruncateGuardError``` (in ```exc```) is the typed error a truncate
guard raises to abort. Both are re-exported here, so the short path
```tokeo.core.ai.guards.truncate``` reaches each.

Unlike the other guard packages, no ready-to-use implementation ships here: a
generated project carries an editable example in its own
```core/ai/guards/truncate.py```, derived from ```TokeoAiTruncateGuard```.
"""

from tokeo.core.ai.guards.truncate.exc import TokeoAiTruncateGuardError
from tokeo.core.ai.guards.truncate.base import TokeoAiTruncateGuard

__all__ = [
    'TokeoAiTruncateGuardError',
    'TokeoAiTruncateGuard',
]
