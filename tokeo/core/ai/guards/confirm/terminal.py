# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
The terminal channel for the confirm guard.

```ai ask``` and ```ai chat``` register it on the hook ```tokeo_ai_confirm``` when
they run in a terminal, so a person answers a confirm there. A project
registers it the same way for a command of its own:

```python
from tokeo.core.ai.guards.confirm import GOVERNOR_CONFIRM_CHANNEL_HOOK
from tokeo.core.ai.guards.confirm.terminal import terminal_channel

app.hook.register(GOVERNOR_CONFIRM_CHANNEL_HOOK, terminal_channel(input, print))
```
"""

import json
import getpass

from tokeo.core.ai import Invocation
from tokeo.core.ai.guards.confirm.channel import TokeoAiConfirmAnswer


def terminal_channel(read, write):
    """
    Build a confirm channel that asks a person in the terminal.

    It shows the stage, the guard, what is to be confirmed, the decision the
    guards reached and the notes so far, then reads an answer by its key and a
    note. A ```no_retry``` needs a note. The answer is a person's
    (```by='human'```) with the login name.

    ### Args

    - **read** (callable): ```read(prompt) -> str```, e.g. ```input```
    - **write** (callable): ```write(text)```, e.g. ```print```

    ### Returns

    - **callable**: The channel ```(app, request) -> TokeoAiConfirmAnswer```

    """
    keys = dict(
        y=TokeoAiConfirmAnswer.YES,
        r=TokeoAiConfirmAnswer.YES_RUN,
        n=TokeoAiConfirmAnswer.NO,
        t=TokeoAiConfirmAnswer.NO_RETRY,
    )

    def ask_on_terminal(app, request):
        subject = request.subject
        if isinstance(subject, Invocation):
            shown = f'{subject.name} {json.dumps(subject.arguments, default=str)}'
        elif isinstance(subject, list):
            shown = (subject[-1] if subject else {}).get('content')
        else:
            shown = getattr(subject, 'text', subject)
        write(f'confirm {request.stage} ({request.guard}): {shown}')
        write(f'  decision: {request.decision}' + (f' ({request.reason})' if request.reason else ''))
        for note in request.notes:
            write(f'  note: {note.get("note")}')
        answer = None
        while answer not in request.answer_values:
            answer = keys.get(read('  [y]es, yes_[r]un, [n]o, no_re[t]ry? ').strip().lower()[:1])
        note = read('  note (empty for none): ').strip() or None
        while answer == TokeoAiConfirmAnswer.NO_RETRY and not note:
            note = read('  note (required for no_retry): ').strip() or None
        try:
            who = getpass.getuser()
        except Exception:
            who = None
        return TokeoAiConfirmAnswer(answer, by=TokeoAiConfirmAnswer.BY_HUMAN, who=who, note=note)

    return ask_on_terminal
