"""
Tests for the governor base and its pipeline mechanics (tokeo core).

The per-stage running order lives on ```TokeoAiGovernor```, the shared base of the
guard, transformer and conductor roles: the stage is the fixed band (a governor
runs at a stage because it overrides that ```on_*``` method); within a stage the
governors run in order. The order is derived from the agent's one flat governor
list: for each stage, the governors that have it, in list order.
```_governors_by_stage``` builds that mapping -- six ordered lists, one per stage
-- and the loop runs each stage's list in order.

The fixtures derive from the bare ```TokeoAiGovernor``` on purpose: this mechanic
is role-independent, so it is proven on the base itself, not on any one role
(a role's own contract, e.g. the guard's deny, is tested with that role). The
full LLM loop is exercised by the Spiral tests; here the focus is the governor
mechanics in isolation.
"""

from tokeo.main import TokeoTest
from tokeo.core.ai.governor import (
    TokeoAiGovernor,
    GOVERNOR_STAGES,
    GOVERNOR_STAGE_ON_CALL,
    GOVERNOR_STAGE_ON_RETURN,
    GOVERNOR_STAGE_ON_PROMPT,
)
from tokeo.core.ai.context import TokeoAiContext
from tokeo.core.ai.transformer import TokeoAiTransformer
from tokeo.core.ai.conductor import TokeoAiConductor
from tokeo.core.ai.data import Invocation, ToolCall


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


# small governors that participate in chosen stages by overriding their methods;
# the bodies are no-ops -- only which stages they have matters for ordering. they
# derive from the bare TokeoAiGovernor: the mechanic under test is role-independent
class OnCallGovernor(TokeoAiGovernor):

    def on_call(self, ctx, invocation):
        pass


class OnReturnGovernor(TokeoAiGovernor):

    def on_return(self, ctx, invocation):
        pass


class CallAndReturnGovernor(TokeoAiGovernor):

    def on_call(self, ctx, invocation):
        pass

    def on_return(self, ctx, invocation):
        pass


class PromptAndCallGovernor(TokeoAiGovernor):

    def on_prompt(self, ctx):
        pass

    def on_call(self, ctx, invocation):
        pass


def _by_stage(app, governors):
    # drive _governors_by_stage with a fixed flat governor list, so the test
    # targets the ordering logic (flat list -> per-stage lists), not agent
    # resolution. _governors_by_stage consumes resolved GovernorConfigEntry items
    # (identity + the stages it runs at) and looks each identity up via _governor;
    # here each governor object is its own identity, its stages are the class
    # stages it implements
    from tokeo.core.ai.config.governors import GovernorConfigEntry

    entries, objs = [], {}
    for index, governor in enumerate(governors):
        identity = f'g{index}'
        objs[identity] = governor
        stages = frozenset(stage for stage in GOVERNOR_STAGES if governor.has_stage(stage))
        entries.append(GovernorConfigEntry(identity, stages, 'agent'))
    app.ai._resolve_governors = lambda agent_obj: entries
    app.ai._governor = lambda identity: objs[identity]
    return app.ai._governors_by_stage(agent_obj=object())


def test_each_stage_gets_its_own_ordered_list():
    # a governor appears only in the lists of the stages it has, in flat-list order
    with AiTest() as app:
        a = OnCallGovernor(app)
        b = OnReturnGovernor(app)
        by_stage = _by_stage(app, [a, b])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [a]
        assert by_stage[GOVERNOR_STAGE_ON_RETURN] == [b]
        # every stage has a list, empty where no governor participates
        assert set(by_stage) == set(GOVERNOR_STAGES)
        assert by_stage[GOVERNOR_STAGE_ON_PROMPT] == []


def test_order_within_a_stage_follows_the_flat_list():
    # two governors of the same stage keep the flat list's order
    with AiTest() as app:
        first = OnCallGovernor(app)
        second = OnCallGovernor(app)
        by_stage = _by_stage(app, [first, second])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [first, second]
        # reversing the flat list reverses the stage order
        by_stage = _by_stage(app, [second, first])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [second, first]


def test_a_governor_with_several_stages_appears_in_each():
    # a governor that overrides two stages is in both stage lists
    with AiTest() as app:
        both = CallAndReturnGovernor(app)
        only_call = OnCallGovernor(app)
        by_stage = _by_stage(app, [only_call, both])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [only_call, both]
        assert by_stage[GOVERNOR_STAGE_ON_RETURN] == [both]


def test_stage_lists_are_independent_views_of_the_flat_order():
    # the same governor can sit at different positions in different stage lists,
    # because each stage filters the flat list on its own
    with AiTest() as app:
        pc = PromptAndCallGovernor(app)
        c = OnCallGovernor(app)
        # flat order [c, pc]: at on_call, c is before pc; at on_prompt, pc alone
        by_stage = _by_stage(app, [c, pc])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [c, pc]
        assert by_stage[GOVERNOR_STAGE_ON_PROMPT] == [pc]


def test_no_governors_gives_empty_lists_for_every_stage():
    # an agent with no governors yields an empty list per stage (the loop then
    # runs exactly as the ungoverned path)
    with AiTest() as app:
        by_stage = _by_stage(app, [])
        assert all(by_stage[stage] == [] for stage in GOVERNOR_STAGES)


# --- deny stamps: trace and feedback name who decided (T-00015/17) ---


class DenyingSilently(TokeoAiTransformer):

    def on_call(self, ctx, invocation):
        # denies without naming a reason: the loop stamps the actor
        invocation.decision = Invocation.DENY


class DenyingOnReturn(TokeoAiConductor):

    def on_return(self, ctx, invocation):
        invocation.decision = Invocation.DENY
        invocation.reason = 'result rejected'


def test_call_deny_without_reason_is_stamped_with_the_actor():
    # the deny is honoured (roles are characters, the implementation
    # decides) and the stamped reason names role + name -- never 'a guard'
    with AiTest() as app:
        governor = DenyingSilently(app)
        governor._setup(app)
        app.ai._governor_objs['shredder'] = governor
        ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
        call = ToolCall(id='t1', name='calc', arguments={'expr': '1+1'})
        invocation, content = app.ai._exec_governed(call, [governor], [], ctx, None, None)
        assert invocation.decision == Invocation.DENY
        assert invocation.reason == "blocked by transformer 'shredder'"
        assert content == "denied: blocked by transformer 'shredder'"
        assert invocation.result is None  # the tool never ran


def test_return_deny_keeps_a_named_reason_untouched():
    # a governor-provided reason IS the text; the stamp only fills silence
    with AiTest() as app:
        governor = DenyingOnReturn(app)
        governor._setup(app)
        app.ai._governor_objs['rejector'] = governor
        ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
        call = ToolCall(id='t2', name='missing_tool', arguments={})
        invocation, content = app.ai._exec_governed(call, [], [governor], ctx, None, None)
        assert invocation.decision == Invocation.DENY
        assert invocation.reason == 'result rejected'
        assert content == 'denied: result rejected'


def test_governor_label_reads_class_character_and_cached_name():
    # the role comes from the class (isinstance), the name from the
    # cache; an uncached object reads as its class name
    with AiTest() as app:
        governor = DenyingSilently(app)
        app.ai._governor_objs['truncate'] = governor
        assert app.ai._governor_label(governor) == "transformer 'truncate'"
        stray = DenyingOnReturn(app)
        assert app.ai._governor_label(stray) == "conductor 'DenyingOnReturn'"
