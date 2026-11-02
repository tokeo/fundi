"""
The ```confirm``` guard package: the confirm guard type and its error.

```TokeoAiConfirmGuard``` (in ```base```) is the empty type to derive from;
```TokeoAiConfirmGuardError``` (in ```exc```) is the typed error a confirm guard
raises to abort. Both are re-exported here, so the short path
```tokeo.core.ai.guards.confirm``` reaches each.

The ready-to-use implementation is the ```confirm``` guard
(```TokeoAiChannelConfirmGuard```): it asks the channels on the cement hook
```tokeo_ai_confirm``` and records every decision.
"""

from tokeo.core.ai.guards.confirm.exc import TokeoAiConfirmGuardError, TokeoAiConfirmChanged
from tokeo.core.ai.guards.confirm.base import TokeoAiConfirmGuard
from tokeo.core.ai.guards.confirm.channel import (
    CONFIRM_HOOK,
    CONFIRM_CHANGES,
    TokeoAiConfirmRequest,
    TokeoAiConfirmAnswer,
    TokeoAiChannelConfirmGuard,
)

__all__ = [
    'TokeoAiConfirmGuardError',
    'TokeoAiConfirmChanged',
    'TokeoAiConfirmGuard',
    'CONFIRM_HOOK',
    'CONFIRM_CHANGES',
    'TokeoAiConfirmRequest',
    'TokeoAiConfirmAnswer',
    'TokeoAiChannelConfirmGuard',
]
