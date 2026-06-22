"""
Tests for the guard base and its pipeline mechanics (tokeo core).

Currently the per-stage running order: the stage is the fixed band (a guard runs
at a stage because it overrides that ```on_*``` method); within a stage the
guards run in order. The order is derived from the agent's one flat guard list:
for each stage, the guards that have it, in list order. ```_guards_by_stage```
builds that mapping -- six ordered lists, one per stage -- and the loop runs each
stage's list in order. The full LLM loop is exercised by the Spiral tests; here
the focus is the guard mechanics in isolation.
"""

from tokeo.main import TokeoTest
from tokeo.core.ai import Invocation
from tokeo.core.ai.guards.policy import (
    TokeoAiToolPolicyGuard,
    TokeoAiDenyPolicyGuard,
    TokeoAiAbortPolicyGuard,
    TokeoAiPolicyGuardError,
)
from tokeo.core.ai.guard import (
    TokeoAiGuard,
    GUARD_STAGES,
    GUARD_STAGE_ON_CALL,
    GUARD_STAGE_ON_RETURN,
    GUARD_STAGE_ON_PROMPT,
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


# small guards that participate in chosen stages by overriding their methods;
# the bodies are no-ops -- only which stages they have matters for ordering
class OnCallGuard(TokeoAiGuard):

    def on_call(self, ctx, invocation):
        pass


class OnReturnGuard(TokeoAiGuard):

    def on_return(self, ctx, invocation):
        pass


class CallAndReturnGuard(TokeoAiGuard):

    def on_call(self, ctx, invocation):
        pass

    def on_return(self, ctx, invocation):
        pass


class PromptAndCallGuard(TokeoAiGuard):

    def on_prompt(self, ctx):
        pass

    def on_call(self, ctx, invocation):
        pass


def _by_stage(app, guards):
    # drive _guards_by_stage with a fixed flat guard list, so the test targets
    # the ordering logic (flat list -> per-stage lists), not agent resolution.
    # _guards_by_stage now consumes resolved GuardConfigEntry items (identity + the
    # stages it runs at) and looks each identity up via _guard; here each guard
    # object is its own identity, its stages are the class stages it implements
    from tokeo.core.ai.config.guards import GuardConfigEntry

    entries, objs = [], {}
    for index, guard in enumerate(guards):
        identity = f'g{index}'
        objs[identity] = guard
        stages = frozenset(stage for stage in GUARD_STAGES if guard.has_stage(stage))
        entries.append(GuardConfigEntry(identity, stages, 'agent'))
    app.ai._resolve_guards = lambda agent_obj: entries
    app.ai._guard = lambda identity: objs[identity]
    return app.ai._guards_by_stage(agent_obj=object())


def test_each_stage_gets_its_own_ordered_list():
    # a guard appears only in the lists of the stages it has, in flat-list order
    with AiTest() as app:
        a = OnCallGuard(app)
        b = OnReturnGuard(app)
        by_stage = _by_stage(app, [a, b])
        assert by_stage[GUARD_STAGE_ON_CALL] == [a]
        assert by_stage[GUARD_STAGE_ON_RETURN] == [b]
        # every stage has a list, empty where no guard participates
        assert set(by_stage) == set(GUARD_STAGES)
        assert by_stage[GUARD_STAGE_ON_PROMPT] == []


def test_order_within_a_stage_follows_the_flat_list():
    # two guards of the same stage keep the flat list's order
    with AiTest() as app:
        first = OnCallGuard(app)
        second = OnCallGuard(app)
        by_stage = _by_stage(app, [first, second])
        assert by_stage[GUARD_STAGE_ON_CALL] == [first, second]
        # reversing the flat list reverses the stage order
        by_stage = _by_stage(app, [second, first])
        assert by_stage[GUARD_STAGE_ON_CALL] == [second, first]


def test_a_guard_with_several_stages_appears_in_each():
    # a guard that overrides two stages is in both stage lists
    with AiTest() as app:
        both = CallAndReturnGuard(app)
        only_call = OnCallGuard(app)
        by_stage = _by_stage(app, [only_call, both])
        assert by_stage[GUARD_STAGE_ON_CALL] == [only_call, both]
        assert by_stage[GUARD_STAGE_ON_RETURN] == [both]


def test_stage_lists_are_independent_views_of_the_flat_order():
    # the same guard can sit at different positions in different stage lists,
    # because each stage filters the flat list on its own
    with AiTest() as app:
        pc = PromptAndCallGuard(app)
        c = OnCallGuard(app)
        # flat order [c, pc]: at on_call, c is before pc; at on_prompt, pc alone
        by_stage = _by_stage(app, [c, pc])
        assert by_stage[GUARD_STAGE_ON_CALL] == [c, pc]
        assert by_stage[GUARD_STAGE_ON_PROMPT] == [pc]


def test_no_guards_gives_empty_lists_for_every_stage():
    # an agent with no guards yields an empty list per stage (the loop then runs
    # exactly as the unguarded path)
    with AiTest() as app:
        by_stage = _by_stage(app, [])
        assert all(by_stage[stage] == [] for stage in GUARD_STAGES)


# the policy guard implementations, tested in isolation (one guard, one
# invocation or stage call); the full loop is exercised by the Spiral tests


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
