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
from tokeo.core.ai.guards.confirm import CONFIRM_HOOK
from tokeo.core.ai.guards.confirm.terminal import terminal_channel

app.hook.register(CONFIRM_HOOK, terminal_channel(input, print))
```
"""

import json
import getpass

from tokeo.core.ai import Invocation
from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL
from tokeo.core.ai.guards.confirm.channel import TokeoAiConfirmAnswer


def terminal_channel(read, write):
    """
    Build a confirm channel that asks a person in the terminal.

    It shows the stage, the guard, what is to be confirmed and the no of a guard
    before, then reads one of the allowed answers by its first letter. A change
    reads the new content: a JSON object of arguments at ```on_call```, a text
    otherwise. The answer is a person's (```by='human'```) with the login name.

    ### Args

    - **read** (callable): ```read(prompt) -> str```, e.g. ```input```
    - **write** (callable): ```write(text)```, e.g. ```print```

    ### Returns

    - **callable**: The channel ```(app, request) -> TokeoAiConfirmAnswer```

    """

    def channel(app, request):
        subject = request.subject
        if isinstance(subject, Invocation):
            shown = f'{subject.name} {json.dumps(subject.arguments, default=str)}'
        elif isinstance(subject, list):
            shown = (subject[-1] if subject else {}).get('content')
        else:
            shown = getattr(subject, 'text', subject)
        write(f'confirm {request.stage} ({request.guard}): {shown}')
        if request.verdict:
            write(f'  a guard said no: {request.verdict.get("reason")}')
        choices = {answer[0]: answer for answer in request.answers}
        prompt = '  ' + ', '.join(f'[{answer[0]}]{answer[1:]}' for answer in request.answers) + '? '
        answer = None
        while answer is None:
            answer = choices.get(read(prompt).strip().lower()[:1])
        content = None
        while answer == TokeoAiConfirmAnswer.CHANGE and content is None:
            text = read('  new content: ')
            if request.stage != GOVERNOR_STAGE_ON_CALL:
                content = text
                continue
            try:
                content = json.loads(text)
            except ValueError:
                content = None
            if not isinstance(content, dict):
                write('  the arguments are a JSON object, e.g. {"text": "moin"}')
                content = None
        try:
            who = getpass.getuser()
        except Exception:
            who = None
        return TokeoAiConfirmAnswer(answer, who=who, content=content)

    return channel
