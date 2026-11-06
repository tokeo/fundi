"""
A policy guard that denies unconditionally, wherever it runs.

```TokeoAiDenyPolicyGuard``` refuses to let the run continue past it, with no
check at all: a debugging tool you drop into a guard list to see the denial
take effect at a given position.

At the tool stages (```on_call```/```on_return```) "deny" has a defined,
*soft* meaning: set ```invocation.decision = DENY```; the call is skipped and
the loop continues. At the other stages (```on_begin```/```on_prompt```/
```on_answer```/```on_close```) it raises ```TokeoAiPolicyGuardError``` and stops
the run hard. A soft denial at those stages is planned; until then the hard
stop stays, so the guard shows the denial at any position.
"""

from tokeo.core.ai import Invocation
from tokeo.core.ai.guards.policy.base import TokeoAiPolicyGuard
from tokeo.core.ai.guards.policy.exc import TokeoAiPolicyGuardError


class TokeoAiDenyPolicyGuard(TokeoAiPolicyGuard):
    """
    Denies unconditionally at whatever stage it runs (a debugging guard).

    No configuration, no check: it always denies. At the tool stages the denial
    is the established soft one (```decision = DENY```; the loop continues). At
    the non-tool stages it raises a ```TokeoAiPolicyGuardError```, a hard stop,
    until a soft denial there is built.

    """

    class Meta:
        """Deny policy guard meta-data."""

        # no configurable options; empty per the config_defaults rule
        config_defaults = {}

    def on_call(self, ctx, invocation):
        """Softly deny the tool call (the loop continues)."""
        invocation.decision = Invocation.DENY
        invocation.reason = 'the deny policy guard denies every call'

    def on_return(self, ctx, invocation):
        """Softly deny at return as well (the loop continues)."""
        invocation.decision = Invocation.DENY
        invocation.reason = 'the deny policy guard denies every call'

    def on_begin(self, ctx):
        """Deny at begin. TODO: soft denial here is undefined; hard stop for now."""
        # TODO: no soft-denial concept for this stage yet; raising is a
        # placeholder while the abort criterion is developed
        raise TokeoAiPolicyGuardError('denied unconditionally at on_begin (no soft denial yet)')

    def on_prompt(self, ctx):
        """Deny at prompt. TODO: soft denial undefined here; hard stop for now."""
        # TODO: no soft-denial concept for this stage yet; raising is a
        # placeholder while the abort criterion is developed
        raise TokeoAiPolicyGuardError('denied unconditionally at on_prompt (no soft denial yet)')

    def on_answer(self, ctx, result):
        """Deny at answer. TODO: soft denial undefined here; hard stop for now."""
        # TODO: no soft-denial concept for this stage yet; raising is a
        # placeholder while the abort criterion is developed
        raise TokeoAiPolicyGuardError('denied unconditionally at on_answer (no soft denial yet)')

    def on_close(self, ctx, result):
        """Deny at close. TODO: soft denial here is undefined; hard stop for now."""
        # TODO: no soft-denial concept for this stage yet; raising is a
        # placeholder while the abort criterion is developed
        raise TokeoAiPolicyGuardError('denied unconditionally at on_close (no soft denial yet)')
