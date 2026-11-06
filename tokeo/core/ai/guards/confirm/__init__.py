"""
The ```confirm``` guard package: the confirm guard type and its error.

```TokeoAiConfirmGuard``` (in ```base```) is the empty type to derive from;
```TokeoAiConfirmGuardError``` (in ```exc```) is the typed error a confirm guard
raises to abort. Both are re-exported here, so the short path
```tokeo.core.ai.guards.confirm``` reaches each.

The ready-to-use implementation is the ```confirm``` guard
(```TokeoAiChannelConfirmGuard```): it asks the channels on the cement hook
```tokeo_ai_confirm``` to confirm the decision the guards reached, and records
every answer.
"""

from tokeo.core.ai.guards.confirm.exc import TokeoAiConfirmGuardError, TokeoAiConfirmRetry
from tokeo.core.ai.guards.confirm.base import TokeoAiConfirmGuard
from tokeo.core.ai.guards.confirm.channel import (
    GOVERNOR_CONFIRM_CHANNEL_HOOK,
    GOVERNOR_CONFIRM_STAGE_MAX_RETRIES,
    TokeoAiConfirmRequest,
    TokeoAiConfirmAnswer,
    TokeoAiChannelConfirmGuard,
)

__all__ = [
    'TokeoAiConfirmGuardError',
    'TokeoAiConfirmRetry',
    'TokeoAiConfirmGuard',
    'GOVERNOR_CONFIRM_CHANNEL_HOOK',
    'GOVERNOR_CONFIRM_STAGE_MAX_RETRIES',
    'TokeoAiConfirmRequest',
    'TokeoAiConfirmAnswer',
    'TokeoAiChannelConfirmGuard',
]
