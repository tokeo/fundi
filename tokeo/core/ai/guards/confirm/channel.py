# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
The ```confirm``` guard: ask a channel before the run goes on.

At each stage it takes part in, the guard builds a ```TokeoAiConfirmRequest```
and asks the channels registered on the cement hook ```tokeo_ai_confirm```. A
channel is a function ```(app, request)``` that returns a
```TokeoAiConfirmAnswer``` or ```None```; the first answer counts. Without one the
option ```default``` decides.

```yaml
ai:
  guards:
    ask_first:
      type: confirm
      options:
        tools: [filesystem]    # only these calls at the tool stages
        auto_allow: [calc]     # allowed by rule, nobody is asked
        default: deny          # no channel answers: no
  agents:
    careful:
      type: fundi
      options:
        governors: [{ask_first: [on_call]}]
```

### Notes

: Who decided is always recorded: ```human``` (a channel answered as a
    person), ```auto``` (a rule or a function of the app, also a model), or
    ```default```. Only a ```human``` answer counts as a human confirmation, and
    only it can lift the no of a guard before; the limits of the config (what the
    agent offers, the deny set, the sandbox chain) no answer lifts.

: Every decision is appended to ```result.loopdata.confirmations``` and tracked
    on the trace, for the app to keep and learn from.
"""

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

#: The cement hook a confirm channel registers on.
CONFIRM_HOOK = 'tokeo_ai_confirm'


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
    - **verdict** (dict|None): The no of a guard before, ```decision``` and
        ```reason```; a human answer may lift it
    - **answers** (tuple): The answers allowed here
    - **userdata**: The caller's ```userdata``` of the run, e.g. who is asked

    """

    stage: str
    guard: str
    subject: object
    verdict: dict | None
    answers: tuple
    userdata: object = None


@dataclass
class TokeoAiConfirmAnswer:
    """
    A channel's answer to a confirm request.

    ### Args

    - **answer** (str): ```NO```, ```ONCE``` or ```RUN``` (the same again in
        this run is allowed without asking)
    - **by** (str): ```HUMAN``` or ```AUTO```, who decided
    - **who** (str|None): The person or rule that decided, for the record
    - **reason** (str|None): Why, for the record and a declined run

    """

    NO = 'no'
    ONCE = 'once'
    RUN = 'run'
    HUMAN = 'human'
    AUTO = 'auto'
    DEFAULT = 'default'

    answer: str
    by: str = HUMAN
    who: str | None = None
    reason: str | None = None


class TokeoAiChannelConfirmGuard(TokeoAiConfirmGuard):
    """
    Ask the ```tokeo_ai_confirm``` channels at every stage it takes part in.

    A no skips the call at ```on_call```, replaces the result at
    ```on_return```, and ends the run with the status ```declined``` at the
    other stages.
    """

    class Meta:
        """The confirm options, overridden per guard by its entry's options."""

        # tools: the calls asked at the tool stages, None = all. default: the
        # answer without a channel, deny or allow. auto_allow, auto_deny: tools
        # decided by rule without asking
        config_defaults = dict(
            tools=None,
            default='deny',
            auto_allow=[],
            auto_deny=[],
        )

        # the options that hold tool and group names, checked by the linter
        tool_name_options = ('tools', 'auto_allow', 'auto_deny')

    def on_begin(self, ctx):
        """Ask before the run starts; a no declines the run."""
        self._decline_on_no(ctx, GOVERNOR_STAGE_ON_BEGIN, ctx.messages)

    def on_prompt(self, ctx):
        """Ask before each model call; a no declines the run."""
        self._decline_on_no(ctx, GOVERNOR_STAGE_ON_PROMPT, ctx.messages)

    def on_answer(self, ctx, result):
        """Ask on each model answer; a no declines the run."""
        self._decline_on_no(ctx, GOVERNOR_STAGE_ON_ANSWER, result)

    def on_close(self, ctx, result):
        """Ask on the final answer; a no declines the run."""
        self._decline_on_no(ctx, GOVERNOR_STAGE_ON_CLOSE, result)

    def on_call(self, ctx, invocation):
        """Ask before a tool call; a no skips it, a human yes lifts a no."""
        self._decide_call(ctx, GOVERNOR_STAGE_ON_CALL, invocation, 'declined by confirmation')

    def on_return(self, ctx, invocation):
        """Ask on a tool result; a no replaces it, a human yes lifts a no."""
        self._decide_call(ctx, GOVERNOR_STAGE_ON_RETURN, invocation, 'denied by confirmation')

    def _decline_on_no(self, ctx, stage, subject):
        # the stages without a tool call: a no ends the run in order
        answer = self._ask(ctx, stage, subject, None, stage)
        if answer.answer == TokeoAiConfirmAnswer.NO:
            raise TokeoAiRunDeclined(stage, answer.reason or 'declined by confirmation', by=answer.by)

    def _decide_call(self, ctx, stage, invocation, no_reason):
        # the tool stages: only the configured tools are asked
        tools = self._config('tools', stage=stage)
        if tools is not None and invocation.name not in self.app.ai.tool_names(tools):
            return
        verdict = None
        if invocation.decision == Invocation.DENY:
            verdict = dict(decision=invocation.decision, reason=invocation.reason)
        answer = self._ask(ctx, stage, invocation, verdict, f'{stage}:{invocation.name}')
        if answer.answer == TokeoAiConfirmAnswer.NO:
            invocation.decision = Invocation.DENY
            invocation.reason = answer.reason or no_reason
        elif verdict is not None and answer.by == TokeoAiConfirmAnswer.HUMAN:
            # a person lifts the no of a guard; the record keeps the verdict
            invocation.decision = Invocation.ALLOW
            invocation.reason = f'allowed by confirmation over: {verdict["reason"]}'

    def _ask(self, ctx, stage, subject, verdict, key):
        # the answer for this request: remembered for the run, by rule, by a
        # channel, or the default; recorded on the result and the trace
        answers = (TokeoAiConfirmAnswer.NO, TokeoAiConfirmAnswer.ONCE, TokeoAiConfirmAnswer.RUN)
        remembered = ctx.turndata.setdefault(f'{self.config_name}:confirm', {})
        answer = remembered.get(key) if verdict is None else None
        recalled = answer is not None
        if answer is None:
            answer = self._rule(stage, subject, verdict)
        if answer is None:
            request = TokeoAiConfirmRequest(stage, self.config_name, subject, verdict, answers, ctx.userdata)
            for result in self.app.hook.run(CONFIRM_HOOK, self.app, request):
                if result is not None:
                    answer = result
                    break
        if answer is None:
            default = self._config('default', stage=stage)
            if default not in ('deny', 'allow'):
                raise TokeoAiError(f'confirm guard {self.config_name!r}: default must be deny or allow, got {default!r}')
            allow = default == 'allow' and verdict is None
            answer = TokeoAiConfirmAnswer(TokeoAiConfirmAnswer.ONCE if allow else TokeoAiConfirmAnswer.NO, by=TokeoAiConfirmAnswer.DEFAULT)
        if answer.answer not in answers:
            raise TokeoAiError(f'confirm guard {self.config_name!r}: answer {answer.answer!r} is not one of {answers}')
        if answer.answer == TokeoAiConfirmAnswer.RUN and verdict is None:
            remembered[key] = answer
        if isinstance(subject, Invocation):
            summary = dict(tool=subject.name, arguments=subject.arguments)
        elif isinstance(subject, list):
            summary = dict(message=(subject[-1] if subject else {}).get('content'))
        else:
            summary = dict(text=getattr(subject, 'text', None))
        record = dict(
            stage=stage,
            guard=self.config_name,
            subject=summary,
            verdict=verdict,
            answer=answer.answer,
            by=answer.by,
            who=answer.who,
            reason=answer.reason,
            recalled=recalled,
            at=datetime.now(timezone.utc).isoformat(),
        )
        ctx.loopdata.confirmations.append(record)
        ctx.track(self, record, stage=stage)
        return answer

    def _rule(self, stage, subject, verdict):
        # the auto rules of the config, at the tool stages only; a rule never
        # lifts the no of a guard
        if not isinstance(subject, Invocation):
            return None
        if subject.name in self.app.ai.tool_names(self._config('auto_deny', stage=stage)):
            return TokeoAiConfirmAnswer(TokeoAiConfirmAnswer.NO, by=TokeoAiConfirmAnswer.AUTO, who='auto_deny')
        if verdict is None and subject.name in self.app.ai.tool_names(self._config('auto_allow', stage=stage)):
            return TokeoAiConfirmAnswer(TokeoAiConfirmAnswer.ONCE, by=TokeoAiConfirmAnswer.AUTO, who='auto_allow')
        return None
