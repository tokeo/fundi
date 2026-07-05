"""
Tests for the guard role's own contract: the soft/hard denial (tokeo core).

The shared governor mechanic (stage reflection, per-stage ordering) is tested in
```test_governor.py``` on the bare base. Here the focus is what makes a guard a
guard rather than any other governor -- the deny decision: a soft denial
(```decision = DENY``` with a reason) that skips one call while the loop
continues, and the hard stop (a raise) that ends the run. Each policy guard is
tested in isolation (one guard, one invocation or stage call); the full loop is
exercised by the Spiral tests.
"""

from tokeo.main import TokeoTest
from tokeo.core.ai import Invocation
from tokeo.core.ai.guards.policy import (
    TokeoAiToolPolicyGuard,
    TokeoAiDenyPolicyGuard,
    TokeoAiAbortPolicyGuard,
    TokeoAiPolicyGuardError,
)


class AiTest(TokeoTest):

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def test_tool_policy_denies_a_tool_on_the_denylist():
    # the tool policy guard softly denies a call whose tool is on the denylist:
    # decision becomes DENY with a reason, the loop would continue
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._declaration = {'options': {'deny': ['append_file']}}
        invocation = Invocation(id='t1', name='append_file', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.DENY
        assert 'not permitted by policy' in (invocation.reason or '')


def test_tool_policy_allows_a_tool_not_denied():
    # a tool not on the denylist (and within the allowlist if set) is left to
    # run: decision stays ALLOW
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._declaration = {'options': {'deny': ['append_file']}}
        invocation = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.ALLOW


def test_tool_policy_allowlist_restricts_to_its_members():
    # with an allowlist set, a tool not in it is denied (allow wins as a
    # restriction; deny would still win over allow)
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._declaration = {'options': {'allow': ['calc']}}
        permitted = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=permitted)
        assert permitted.decision == Invocation.ALLOW
        blocked = Invocation(id='t2', name='time', arguments={})
        guard.on_call(ctx=None, invocation=blocked)
        assert blocked.decision == Invocation.DENY


def test_deny_policy_softly_denies_at_the_tool_stages():
    # the deny policy guard always denies; at the tool stages the denial is soft
    # (decision = DENY, no exception), so the loop would continue
    with AiTest() as app:
        guard = TokeoAiDenyPolicyGuard(app)
        on_call = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=on_call)
        assert on_call.decision == Invocation.DENY
        on_return = Invocation(id='t2', name='calc', arguments={})
        guard.on_return(ctx=None, invocation=on_return)
        assert on_return.decision == Invocation.DENY


def test_deny_policy_raises_at_the_non_tool_stages_for_now():
    # at the non-tool stages a soft denial is not defined yet, so the deny
    # policy guard raises (a temporary hard stop -- the TODO placeholder)
    with AiTest() as app:
        guard = TokeoAiDenyPolicyGuard(app)
        for call in (
            lambda: guard.on_begin(ctx=None),
            lambda: guard.on_prompt(ctx=None),
            lambda: guard.on_answer(ctx=None, result=None),
            lambda: guard.on_close(ctx=None, result=None),
        ):
            try:
                call()
                assert False, 'expected TokeoAiPolicyGuardError'
            except TokeoAiPolicyGuardError:
                pass


def test_abort_policy_raises_at_every_stage():
    # the abort policy guard is an unconditional hard stop: every stage raises,
    # so the run ends wherever it sits
    with AiTest() as app:
        guard = TokeoAiAbortPolicyGuard(app)
        invocation = Invocation(id='t1', name='calc', arguments={})
        for call in (
            lambda: guard.on_begin(ctx=None),
            lambda: guard.on_prompt(ctx=None),
            lambda: guard.on_answer(ctx=None, result=None),
            lambda: guard.on_call(ctx=None, invocation=invocation),
            lambda: guard.on_return(ctx=None, invocation=invocation),
            lambda: guard.on_close(ctx=None, result=None),
        ):
            try:
                call()
                assert False, 'expected TokeoAiPolicyGuardError'
            except TokeoAiPolicyGuardError:
                pass
