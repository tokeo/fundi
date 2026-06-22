"""
A policy guard that aborts the run hard, wherever it runs.

```TokeoAiAbortPolicyGuard``` raises ```TokeoAiPolicyGuardError``` at every
stage -- the same hard, unambiguous "the run ends here" at any position. A
debugging tool you drop into a guard list to stop the run reliably at a chosen
point, with no soft/hard ambiguity: it never continues.
"""

from tokeo.core.ai.guards.policy.base import TokeoAiPolicyGuard
from tokeo.core.ai.guards.policy.exc import TokeoAiPolicyGuardError


class TokeoAiAbortPolicyGuard(TokeoAiPolicyGuard):
    """
    Aborts the run with an exception at whatever stage it runs.

    No configuration, no check: every stage raises ```TokeoAiPolicyGuardError```,
    so the run stops hard wherever this guard sits. Unlike the deny policy guard
    (soft at the tool stages), this is always a hard stop -- the unambiguous
    debugging abort.

    """

    class Meta:
        """Abort policy guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}

    def on_begin(self, ctx):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_begin by the abort policy guard')

    def on_prompt(self, ctx):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_prompt by the abort policy guard')

    def on_answer(self, ctx, result):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_answer by the abort policy guard')

    def on_call(self, ctx, invocation):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_call by the abort policy guard')

    def on_return(self, ctx, invocation):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_return by the abort policy guard')

    def on_close(self, ctx, result):
        """Abort the run."""
        raise TokeoAiPolicyGuardError('aborted at on_close by the abort policy guard')
