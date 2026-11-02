"""
Tests for the guard role's own contract: the soft/hard denial (tokeo core).

The shared governor mechanic (stage reflection, per-stage ordering) is tested in
```test_governor.py``` on the bare base. Here the focus is what makes a guard a
guard rather than any other governor, the deny decision: a soft denial
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
    """append_file on the denylist gets a soft DENY: 'not permitted by policy'."""
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'deny': ['append_file']}})
        invocation = Invocation(id='t1', name='append_file', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.DENY
        assert 'not permitted by policy' in (invocation.reason or '')


def test_tool_policy_allows_a_tool_not_denied():
    """calc, not on the denylist and with no allowlist set, stays ALLOW."""
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'deny': ['append_file']}})
        invocation = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.ALLOW


def test_tool_policy_allowlist_restricts_to_its_members():
    """With allow ['calc'] calc stays ALLOW, time gets DENY; deny beats allow."""
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'allow': ['calc']}})
        permitted = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=permitted)
        assert permitted.decision == Invocation.ALLOW
        blocked = Invocation(id='t2', name='time', arguments={})
        guard.on_call(ctx=None, invocation=blocked)
        assert blocked.decision == Invocation.DENY


def _files_config():
    """An ai config with the group filesystem of read_file and append_file."""
    return {'ai': {'tools': {'filesystem': ['read_file', 'append_file']}}}


def test_tool_policy_deny_resolves_a_group_to_its_members():
    """deny [filesystem] denies append_file, a member of the group."""
    with AiTest(config_defaults=_files_config()) as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'deny': ['filesystem']}})
        invocation = Invocation(id='t1', name='append_file', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.DENY


def test_tool_policy_allow_resolves_a_group_to_its_members():
    """allow [filesystem] keeps read_file and denies calc."""
    with AiTest(config_defaults=_files_config()) as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'allow': ['filesystem']}})
        permitted = Invocation(id='t1', name='read_file', arguments={})
        guard.on_call(ctx=None, invocation=permitted)
        assert permitted.decision == Invocation.ALLOW
        blocked = Invocation(id='t2', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=blocked)
        assert blocked.decision == Invocation.DENY


def test_tool_policy_single_name_is_a_list_of_one():
    """allow 'rewrite_file' denies write_file, no match as part of the text."""
    with AiTest() as app:
        guard = TokeoAiToolPolicyGuard(app)
        guard._setup(app, 'policy', {'options': {'allow': 'rewrite_file'}})
        invocation = Invocation(id='t1', name='write_file', arguments={})
        guard.on_call(ctx=None, invocation=invocation)
        assert invocation.decision == Invocation.DENY


def test_deny_policy_softly_denies_at_the_tool_stages():
    """The deny guard sets DENY at on_call and on_return, no raise, loop goes on."""
    with AiTest() as app:
        guard = TokeoAiDenyPolicyGuard(app)
        on_call = Invocation(id='t1', name='calc', arguments={})
        guard.on_call(ctx=None, invocation=on_call)
        assert on_call.decision == Invocation.DENY
        on_return = Invocation(id='t2', name='calc', arguments={})
        guard.on_return(ctx=None, invocation=on_return)
        assert on_return.decision == Invocation.DENY


def test_deny_policy_raises_at_the_non_tool_stages_for_now():
    """
    The deny guard raises at on_begin, on_prompt, on_answer and on_close.

    A soft denial is not defined at the non-tool stages yet, so this is a
    temporary hard stop (the TODO placeholder).
    """
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
    """The abort guard raises at all six stages, so the run ends wherever it is."""
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
