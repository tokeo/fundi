# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
The ```confirm``` guard: a channel confirms the decision the guards reached.

At each stage it takes part in, the guard builds a ```TokeoAiConfirmRequest```
with the content, the decision the guards before it reached (```allow``` or
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

: The answers: ```yes``` (the decision stands), ```yes_run``` (the same, and the
    question 1:1 the same is not asked again in this run), ```no``` (the
    decision becomes deny), ```no_retry``` (not like this: the note goes to
    ```ctx.notes``` and the stage runs again, the guards judge anew, then the
    channel is asked again). Every answer may carry a note, ```no_retry```
    must. A confirm turns a yes into a no, never a no into a yes: that only
    happens when a guard judges anew with the notes and the new decision is
    confirmed.

: Every answer is recorded in ```result.confirmations``` and on the
    trace, a note also in ```ctx.notes```; ```chat(notes=...)``` hands notes of
    earlier runs to the guards of a new one.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tokeo.core.utils.validate import is_int_ge
from tokeo.core.ai import Invocation, TokeoAiRunDeclinedError
from tokeo.core.ai.governor import (
    GOVERNOR_STAGE_ON_BEGIN,
    GOVERNOR_STAGE_ON_PROMPT,
    GOVERNOR_STAGE_ON_ANSWER,
    GOVERNOR_STAGE_ON_CALL,
    GOVERNOR_STAGE_ON_RETURN,
    GOVERNOR_STAGE_ON_CLOSE,
)
from tokeo.core.ai.guards.confirm.base import TokeoAiConfirmGuard
from tokeo.core.ai.guards.confirm.exc import TokeoAiConfirmGuardError, TokeoAiConfirmRetry

#: The cement hook a confirm channel registers on.
GOVERNOR_CONFIRM_CHANNEL_HOOK = 'tokeo_ai_confirm'

#: How often one stage may be retried before the run is declined, the default
#: of the option max_retries.
GOVERNOR_CONFIRM_STAGE_MAX_RETRIES = 5


@dataclass
class TokeoAiConfirmRequest:
    """
    What a confirm guard asks a channel.

    ### Args

    - **subject**: What is to be confirmed: the ```Invocation``` at the tool
        stages, the ```ChatResult``` at ```on_answer```/```on_close```, the
        messages at ```on_begin```/```on_prompt```
    - **decision** (str): The decision the guards reached, ```allow``` or
        ```deny```
    - **reason** (str|None): The reason of the decision
    - **answer_values** (tuple): The values an answer may have
    - **stage** (str): The stage asking, e.g. ```on_call```
    - **guard_name** (str): The config name of the confirm guard
    - **userdata**: The caller's ```userdata``` of the run, e.g. who is asked
    - **notes** (list): Every note so far, also from earlier runs

    """

    #: The work object of the stage
    subject: object
    #: The decision of the guards so far
    decision: str
    #: The reason of the decision
    reason: str | None
    #: The values an answer may have
    answer_values: tuple
    #: The stage that asks
    stage: str
    #: The config name of the asking guard
    guard_name: str
    #: The caller's ```userdata``` of the run
    userdata: object = None
    #: Every note so far, also from earlier runs
    notes: list = field(default_factory=list)


@dataclass
class TokeoAiConfirmAnswer:
    """
    A channel's answer to a confirm request.

    ### Args

    - **value** (str): ```YES```, ```YES_RUN```, ```NO``` or ```NO_RETRY```
    - **by** (str): ```BY_HUMAN``` or ```BY_AUTO```, who decided; required, so
        an app function never passes for a person
    - **who** (str|None): The person or function that decided
    - **note** (str|None): The note; required with ```NO_RETRY```

    """

    #: Yes for this call or stage
    YES = 'yes'
    #: Yes for the rest of the run
    YES_RUN = 'yes_run'
    #: No
    NO = 'no'
    #: No, run the stage again with the note
    NO_RETRY = 'no_retry'
    #: A person decided
    BY_HUMAN = 'human'
    #: A function of the app decided
    BY_AUTO = 'auto'
    #: No channel answered, the default decided
    BY_DEFAULT = 'default'

    #: ```YES```, ```YES_RUN```, ```NO``` or ```NO_RETRY```
    value: str
    #: ```BY_HUMAN``` or ```BY_AUTO```, who decided
    by: str
    #: The person or function that decided
    who: str | None = None
    #: The note; required with ```NO_RETRY```
    note: str | None = None


class TokeoAiChannelConfirmGuard(TokeoAiConfirmGuard):
    """
    Ask the ```tokeo_ai_confirm``` channels to confirm the decision of a stage.

    A no skips the call at ```on_call```, replaces the result at
    ```on_return```, and declines the run at the other stages
    (```TokeoAiRunDeclinedError```). A no_retry runs the stage again with the
    note.
    """

    class Meta:
        """Channel confirm guard meta-data."""

        # the configurable defaults, as one dict; a guard entry's options overlay
        # this, read at runtime via _config
        config_defaults = dict(
            # the calls asked at the tool stages, None = all
            tools=None,
            # the answer when no channel answers, no or yes
            default='no',
            # how often a stage may run again after no_retry before the run
            # is declined
            max_retries=GOVERNOR_CONFIRM_STAGE_MAX_RETRIES,
        )

        # the options that hold tool and group names, checked by the linter
        tool_name_options = ('tools',)

    def lint_options(self, options):
        """
        Check the values the linter cannot know.

        ### Args

        - **options** (dict): The guard's ```options``` block, or the block of
            one stage

        ### Returns

        - **list[str]**: An error for a max_retries below 1

        """
        errors = []
        if 'max_retries' in (options or {}) and not is_int_ge(options['max_retries'], 1):
            errors.append('max_retries must be a whole number, at least 1')
        return errors

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
        self._confirm_call(ctx, GOVERNOR_STAGE_ON_CALL, invocation)

    def on_return(self, ctx, invocation):
        """Confirm a tool result; a no replaces it."""
        self._confirm_call(ctx, GOVERNOR_STAGE_ON_RETURN, invocation)

    def _confirm_run(self, ctx, stage, subject):
        # the stages without a tool call: the guards there cannot deny, the
        # decision is allow; a no ends the run in order
        answer = self._get_answer(ctx, stage, subject, Invocation.ALLOW, None, f'{stage}:{ctx.loopdata.steps}')
        if answer.value == TokeoAiConfirmAnswer.NO_RETRY:
            raise TokeoAiConfirmRetry(None if isinstance(subject, list) else subject)
        if answer.value == TokeoAiConfirmAnswer.NO:
            raise TokeoAiRunDeclinedError(stage, answer.note or 'declined by confirmation', by=answer.by)

    def _confirm_call(self, ctx, stage, invocation):
        # the tool stages: only the configured tools are asked
        tools = self._config('tools', stage=stage)
        if tools is not None and invocation.name not in self.app.ai.resolve_tool_names(tools):
            return
        answer = self._get_answer(ctx, stage, invocation, invocation.decision, invocation.reason, f'{stage}:{invocation.id}')
        if answer.value == TokeoAiConfirmAnswer.NO_RETRY:
            # the guards of the stage judge the same call anew, with the notes
            invocation.decision, invocation.reason = Invocation.ALLOW, None
            raise TokeoAiConfirmRetry(invocation)
        if answer.value == TokeoAiConfirmAnswer.NO and invocation.decision != Invocation.DENY:
            invocation.decision, invocation.reason = Invocation.DENY, answer.note or 'denied by confirmation'

    def _get_answer(self, ctx, stage, subject, decision, reason, retry_key):
        # the answer: recalled for the run when the question is 1:1 the same,
        # else from a channel, else the default; recorded on the result and
        # the trace, a note also in ctx.notes
        confirm_cache = ctx.turndata.setdefault(f'{self.config_name}:confirm', dict(run={}, retries={}))
        if isinstance(subject, Invocation):
            subject_dict = dict(tool=subject.name, arguments=subject.arguments)
        elif isinstance(subject, list):
            subject_dict = dict(messages=[dict(role=m.get('role'), content=m.get('content')) for m in subject])
        else:
            subject_dict = dict(text=getattr(subject, 'text', None))
        cache_key = json.dumps([stage, subject_dict, decision, reason], sort_keys=True, default=str)
        answer_values = (TokeoAiConfirmAnswer.YES, TokeoAiConfirmAnswer.YES_RUN, TokeoAiConfirmAnswer.NO, TokeoAiConfirmAnswer.NO_RETRY)
        answer = confirm_cache['run'].get(cache_key)
        is_recalled = answer is not None
        if answer is None:
            request = TokeoAiConfirmRequest(
                stage=stage,
                guard_name=self.config_name,
                subject=subject,
                decision=decision,
                reason=reason,
                notes=list(ctx.notes),
                answer_values=answer_values,
                userdata=ctx.userdata,
            )
            for result in self.app.hook.run(GOVERNOR_CONFIRM_CHANNEL_HOOK, self.app, request):
                if result is not None:
                    answer = result
                    break
        if answer is None:
            default = self._config('default', stage=stage)
            if default not in (TokeoAiConfirmAnswer.NO, TokeoAiConfirmAnswer.YES):
                raise TokeoAiConfirmGuardError(f'confirm guard {self.config_name!r}: default must be no or yes, got {default!r}')
            answer = TokeoAiConfirmAnswer(default, by=TokeoAiConfirmAnswer.BY_DEFAULT)
        elif not is_recalled:
            if answer.value not in answer_values:
                raise TokeoAiConfirmGuardError(
                    f'confirm guard {self.config_name!r}: answer {answer.value!r} is not one of {answer_values}'
                )
            if answer.by not in (TokeoAiConfirmAnswer.BY_HUMAN, TokeoAiConfirmAnswer.BY_AUTO):
                raise TokeoAiConfirmGuardError(
                    f'confirm guard {self.config_name!r}: an answer must say by human or auto, got {answer.by!r}'
                )
        retries = confirm_cache['retries'].get(retry_key, 0)
        if answer.value == TokeoAiConfirmAnswer.NO_RETRY:
            if not answer.note:
                raise TokeoAiConfirmGuardError(f'confirm guard {self.config_name!r}: no_retry needs a note')
            max_retries = self._config('max_retries', stage=stage)
            if retries >= max_retries:
                raise TokeoAiRunDeclinedError(stage, f'retried more than {max_retries} times in a confirm', by=answer.by)
            confirm_cache['retries'][retry_key] = retries + 1
        if answer.value == TokeoAiConfirmAnswer.YES_RUN:
            confirm_cache['run'][cache_key] = answer
        confirmation = dict(
            stage=stage,
            guard_name=self.config_name,
            subject_dict=subject_dict,
            previous_decision=decision,
            previous_reason=reason,
            answer=answer.value,
            decision=Invocation.DENY if answer.value == TokeoAiConfirmAnswer.NO else decision,
            note=answer.note,
            by=answer.by,
            who=answer.who,
            retries=retries,
            is_recalled=is_recalled,
            at=datetime.now(timezone.utc),
        )
        ctx.confirmations.append(confirmation)
        if answer.note:
            ctx.notes.append(confirmation)
        ctx.track(self, confirmation, stage=stage)
        return answer
