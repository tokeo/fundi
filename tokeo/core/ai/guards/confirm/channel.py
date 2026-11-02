# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
The ```confirm``` guard: a channel confirms the state the guards reached.

At each stage it takes part in, the guard builds a ```TokeoAiConfirmRequest```
with the content, the state the guards before it reached (```allow``` or
```deny``` with the reason) and all notes so far, and asks the channels on the
cement hook ```tokeo_ai_confirm```. A channel is a function ```(app, request)```
that returns a ```TokeoAiConfirmAnswer``` or ```None```; the first answer counts.
Without one the option ```default``` decides.

```yaml
ai:
  guards:
    ask_first:
      type: confirm
      options:
        tools: [filesystem]    # only these calls at the tool stages
        default: no            # no channel answers: no
  agents:
    careful:
      type: fundi
      options:
        governors: [spam_check, {ask_first: [on_call]}]
```

### Notes

: The answers: ```yes``` (the state stands), ```yes_run``` (the same, and the
    question 1:1 the same is not asked again in this run), ```no``` (the state
    becomes deny), ```no_retry``` (not like this: the note goes to
    ```ctx.notes``` and the stage runs again, the guards judge anew, then the
    channel is asked again). Every answer may carry a note, ```no_retry```
    must. A confirm turns a yes into a no, never a no into a yes: that only
    happens when a guard judges anew with the notes and the new state is
    confirmed.

: Every answer is recorded in ```result.loopdata.confirmations``` and on the
    trace, a note also in ```ctx.notes```; ```chat(notes=...)``` hands notes of
    earlier runs to the guards of a new one.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from tokeo.core.ai import Invocation, TokeoAiError, TokeoAiRunDeclined
from tokeo.core.ai.governor import (
    GOVERNOR_STAGE_ON_BEGIN,
    GOVERNOR_STAGE_ON_PROMPT,
    GOVERNOR_STAGE_ON_ANSWER,
    GOVERNOR_STAGE_ON_CALL,
    GOVERNOR_STAGE_ON_RETURN,
    GOVERNOR_STAGE_ON_CLOSE,
)
from tokeo.core.ai.guards.confirm.base import TokeoAiConfirmGuard
from tokeo.core.ai.guards.confirm.exc import TokeoAiConfirmRetry

#: The cement hook a confirm channel registers on.
CONFIRM_HOOK = 'tokeo_ai_confirm'

#: How often one stage may be retried before the run is declined.
CONFIRM_RETRIES = 10


@dataclass
class TokeoAiConfirmRequest:
    """
    What a confirm guard asks a channel.

    ### Args

    - **stage** (str): The stage asking, e.g. ```on_call```
    - **guard** (str): The config name of the confirm guard
    - **subject**: What is to be confirmed: the ```Invocation``` at the tool
        stages, the ```ChatResult``` at ```on_answer```/```on_close```, the
        messages at ```on_begin```/```on_prompt```
    - **state** (dict): The state the guards reached, ```decision```
        (```allow``` or ```deny```) and ```reason```
    - **notes** (list): Every note so far, also from earlier runs
    - **answers** (tuple): The answers allowed
    - **userdata**: The caller's ```userdata``` of the run, e.g. who is asked

    """

    stage: str
    guard: str
    subject: object
    state: dict
    notes: list
    answers: tuple
    userdata: object = None


@dataclass
class TokeoAiConfirmAnswer:
    """
    A channel's answer to a confirm request.

    ### Args

    - **answer** (str): ```YES```, ```YES_RUN```, ```NO``` or ```NO_RETRY```
    - **by** (str): ```HUMAN``` or ```AUTO```, who decided; required, so an app
        function never passes for a person
    - **who** (str|None): The person or function that decided
    - **note** (str|None): The note; required with ```NO_RETRY```

    """

    YES = 'yes'
    YES_RUN = 'yes_run'
    NO = 'no'
    NO_RETRY = 'no_retry'
    HUMAN = 'human'
    AUTO = 'auto'
    DEFAULT = 'default'

    answer: str
    by: str
    who: str | None = None
    note: str | None = None


class TokeoAiChannelConfirmGuard(TokeoAiConfirmGuard):
    """
    Ask the ```tokeo_ai_confirm``` channels to confirm the state of a stage.

    A no skips the call at ```on_call```, replaces the result at
    ```on_return```, and ends the run with the status ```declined``` at the
    other stages. A no_retry runs the stage again with the note.
    """

    class Meta:
        """The confirm options, overridden per guard by its entry's options."""

        # tools: the calls asked at the tool stages, None = all. default: the
        # answer when no channel answers, no or yes
        config_defaults = dict(
            tools=None,
            default='no',
        )

        # the options that hold tool and group names, checked by the linter
        tool_name_options = ('tools',)

    def on_begin(self, ctx):
        """Confirm before the run starts; a no declines the run."""
        self._confirm_run(ctx, GOVERNOR_STAGE_ON_BEGIN, ctx.messages)

    def on_prompt(self, ctx):
        """Confirm before each model call; a no declines the run."""
        self._confirm_run(ctx, GOVERNOR_STAGE_ON_PROMPT, ctx.messages)

    def on_answer(self, ctx, result):
        """Confirm each model answer; a no declines the run."""
        self._confirm_run(ctx, GOVERNOR_STAGE_ON_ANSWER, result)

    def on_close(self, ctx, result):
        """Confirm the final answer; a no declines the run."""
        self._confirm_run(ctx, GOVERNOR_STAGE_ON_CLOSE, result)

    def on_call(self, ctx, invocation):
        """Confirm a tool call; a no skips it."""
        self._confirm_call(ctx, GOVERNOR_STAGE_ON_CALL, invocation, 'declined by confirmation')

    def on_return(self, ctx, invocation):
        """Confirm a tool result; a no replaces it."""
        self._confirm_call(ctx, GOVERNOR_STAGE_ON_RETURN, invocation, 'denied by confirmation')

    def _confirm_run(self, ctx, stage, subject):
        # the stages without a tool call: the guards there cannot deny, the
        # state is allow; a no ends the run in order
        state = dict(decision=Invocation.ALLOW, reason=None)
        answer = self._ask(ctx, stage, subject, state, f'{stage}:{ctx.loopdata.steps}')
        if answer.answer == TokeoAiConfirmAnswer.NO_RETRY:
            raise TokeoAiConfirmRetry(None if isinstance(subject, list) else subject)
        if answer.answer == TokeoAiConfirmAnswer.NO:
            raise TokeoAiRunDeclined(stage, answer.note or 'declined by confirmation', by=answer.by)

    def _confirm_call(self, ctx, stage, invocation, no_reason):
        # the tool stages: only the configured tools are asked
        tools = self._config('tools', stage=stage)
        if tools is not None and invocation.name not in self.app.ai.tool_names(tools):
            return
        state = dict(decision=invocation.decision, reason=invocation.reason)
        answer = self._ask(ctx, stage, invocation, state, f'{stage}:{invocation.id}')
        if answer.answer == TokeoAiConfirmAnswer.NO_RETRY:
            # the guards of the stage judge the same call anew, with the notes
            invocation.decision, invocation.reason = Invocation.ALLOW, None
            raise TokeoAiConfirmRetry(invocation)
        if answer.answer == TokeoAiConfirmAnswer.NO and invocation.decision != Invocation.DENY:
            invocation.decision, invocation.reason = Invocation.DENY, answer.note or no_reason

    def _ask(self, ctx, stage, subject, state, retry_key):
        # the answer: recalled for the run when the question is 1:1 the same,
        # else from a channel, else the default; recorded on the result and
        # the trace, a note also in ctx.notes
        memory = ctx.turndata.setdefault(f'{self.config_name}:confirm', dict(run={}, retries={}))
        if isinstance(subject, Invocation):
            summary = dict(tool=subject.name, arguments=subject.arguments)
        elif isinstance(subject, list):
            summary = dict(messages=[dict(role=m.get('role'), content=m.get('content')) for m in subject])
        else:
            summary = dict(text=getattr(subject, 'text', None))
        question = json.dumps([stage, summary, state], sort_keys=True, default=str)
        answers = (TokeoAiConfirmAnswer.YES, TokeoAiConfirmAnswer.YES_RUN, TokeoAiConfirmAnswer.NO, TokeoAiConfirmAnswer.NO_RETRY)
        answer = memory['run'].get(question)
        recalled = answer is not None
        if answer is None:
            request = TokeoAiConfirmRequest(stage, self.config_name, subject, state, list(ctx.notes), answers, ctx.userdata)
            for result in self.app.hook.run(CONFIRM_HOOK, self.app, request):
                if result is not None:
                    answer = result
                    break
        if answer is None:
            default = self._config('default', stage=stage)
            if default not in (TokeoAiConfirmAnswer.NO, TokeoAiConfirmAnswer.YES):
                raise TokeoAiError(f'confirm guard {self.config_name!r}: default must be no or yes, got {default!r}')
            answer = TokeoAiConfirmAnswer(default, by=TokeoAiConfirmAnswer.DEFAULT)
        elif not recalled:
            if answer.answer not in answers:
                raise TokeoAiError(f'confirm guard {self.config_name!r}: answer {answer.answer!r} is not one of {answers}')
            if answer.by not in (TokeoAiConfirmAnswer.HUMAN, TokeoAiConfirmAnswer.AUTO):
                raise TokeoAiError(f'confirm guard {self.config_name!r}: an answer must say by human or auto, got {answer.by!r}')
        retries = memory['retries'].get(retry_key, 0)
        if answer.answer == TokeoAiConfirmAnswer.NO_RETRY:
            if not answer.note:
                raise TokeoAiError(f'confirm guard {self.config_name!r}: no_retry needs a note')
            if retries >= CONFIRM_RETRIES:
                raise TokeoAiRunDeclined(stage, f'retried more than {CONFIRM_RETRIES} times in a confirm', by=answer.by)
            memory['retries'][retry_key] = retries + 1
        if answer.answer == TokeoAiConfirmAnswer.YES_RUN:
            memory['run'][question] = answer
        after = state['decision']
        if answer.answer == TokeoAiConfirmAnswer.NO:
            after = Invocation.DENY
        record = dict(
            stage=stage,
            guard=self.config_name,
            subject=summary,
            state_before=state,
            answer=answer.answer,
            state_after=after,
            note=answer.note,
            by=answer.by,
            who=answer.who,
            retry=retries,
            recalled=recalled,
            at=datetime.now(timezone.utc).isoformat(),
        )
        ctx.loopdata.confirmations.append(record)
        if answer.note:
            ctx.notes.append(record)
        ctx.track(self, record, stage=stage)
        return answer
