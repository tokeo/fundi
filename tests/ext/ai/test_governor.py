"""
Tests for how the ai extension resolves and runs governors.

Driven through ```app.ai._governors_by_stage``` and the deny path: the flat
governor list turning into per-stage lists in order, and the actor being stamped
into a reason the governor left unnamed.
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
    """Test app with the ai extension and the extensions it builds on."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]
        config_defaults = {'ai': {'license_acknowledged': False}}


# small governors that participate in chosen stages by overriding their methods;
# the bodies are no-ops; only which stages they have matters for ordering. they
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
    """Return _governors_by_stage for a fixed flat list of governor objects."""
    # drive _governors_by_stage with a fixed flat governor list, so the test
    # targets the ordering logic (flat list -> per-stage lists), not agent
    # resolution. _governors_by_stage consumes resolved GovernorConfigEntry items
    # (config_name + the stages it runs at) and looks each one up via _governor;
    # here each governor object is its own config_name, its stages are the class
    # stages it implements
    from tokeo.core.ai.config.governors import GovernorConfigEntry

    entries, objs = [], {}
    for index, governor in enumerate(governors):
        config_name = f'g{index}'
        objs[config_name] = governor
        stages = frozenset(stage for stage in GOVERNOR_STAGES if governor.has_stage(stage))
        entries.append(GovernorConfigEntry(config_name, stages, 'agent'))
    app.ai._resolve_governors = lambda agent_obj: entries
    app.ai._governor = lambda config_name: objs[config_name]
    return app.ai._governors_by_stage(agent_obj=object())


def test_each_stage_gets_its_own_ordered_list():
    """A governor is listed only at the stages it has, in flat-list order."""
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
    """Two on_call governors keep the flat list's order, reversed when it is."""
    with AiTest() as app:
        first = OnCallGovernor(app)
        second = OnCallGovernor(app)
        by_stage = _by_stage(app, [first, second])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [first, second]
        # reversing the flat list reverses the stage order
        by_stage = _by_stage(app, [second, first])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [second, first]


def test_a_governor_with_several_stages_appears_in_each():
    """A governor with on_call and on_return is in both stage lists."""
    with AiTest() as app:
        both = CallAndReturnGovernor(app)
        only_call = OnCallGovernor(app)
        by_stage = _by_stage(app, [only_call, both])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [only_call, both]
        assert by_stage[GOVERNOR_STAGE_ON_RETURN] == [both]


def test_stage_lists_are_independent_views_of_the_flat_order():
    """Each stage filters on its own: pc is 2nd at on_call, alone at on_prompt."""
    with AiTest() as app:
        pc = PromptAndCallGovernor(app)
        c = OnCallGovernor(app)
        # flat order [c, pc]: at on_call, c is before pc; at on_prompt, pc alone
        by_stage = _by_stage(app, [c, pc])
        assert by_stage[GOVERNOR_STAGE_ON_CALL] == [c, pc]
        assert by_stage[GOVERNOR_STAGE_ON_PROMPT] == [pc]


def test_no_governors_gives_empty_lists_for_every_stage():
    """No governors give an empty list per stage, so the loop runs ungoverned."""
    with AiTest() as app:
        by_stage = _by_stage(app, [])
        assert all(by_stage[stage] == [] for stage in GOVERNOR_STAGES)


# ---- deny stamps: trace and feedback name who decided ----


class DenyingSilently(TokeoAiTransformer):

    def on_call(self, ctx, invocation):
        # denies without naming a reason: the loop stamps the actor
        invocation.decision = Invocation.DENY


class DenyingOnReturn(TokeoAiConductor):

    def on_return(self, ctx, invocation):
        invocation.decision = Invocation.DENY
        invocation.reason = 'result rejected'


def test_call_deny_without_reason_is_stamped_with_the_actor():
    """A transformer's silent deny holds; reason names transformer 'shredder'."""
    # roles are characters, the implementation decides; never 'a guard'
    with AiTest() as app:
        governor = DenyingSilently(app)
        governor._setup(app, 'shredder')
        ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
        call = ToolCall(id='t1', name='calc', arguments={'expr': '1+1'})
        invocation, content = app.ai._exec_governed(call, [governor], [], ctx, None, None)
        assert invocation.decision == Invocation.DENY
        assert invocation.reason == "blocked by transformer 'shredder'"
        assert content == "denied: blocked by transformer 'shredder'"
        assert invocation.result is None  # the tool never ran


def test_return_deny_keeps_a_named_reason_untouched():
    """An on_return deny's own reason 'result rejected' is fed back unstamped."""
    with AiTest() as app:
        governor = DenyingOnReturn(app)
        governor._setup(app, 'rejector')
        ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
        call = ToolCall(id='t2', name='missing_tool', arguments={})
        invocation, content = app.ai._exec_governed(call, [], [governor], ctx, None, None)
        assert invocation.decision == Invocation.DENY
        assert invocation.reason == 'result rejected'
        assert content == 'denied: result rejected'


def test_governor_config_reads_a_key_honouring_the_stage():
    """An on_<stage> option wins at that stage, options elsewhere, then fallback."""
    with AiTest() as app:
        governor = DenyingSilently(app)
        governor._setup(
            app,
            'truncate',
            {
                'options': {'limit': 10, 'marker': '...'},
                'on_close': {'options': {'limit': 99}},
            },
        )
        assert governor._config('limit') == 10
        assert governor._config('limit', stage='on_close') == 99
        assert governor._config('marker', stage='on_close') == '...'
        assert governor._config('limit', stage='on_return') == 10
        assert governor._config('missing', fallback='x') == 'x'


def test_governor_label_reads_class_character_and_config_name():
    """The label is role + config name, or the class path for one never set up."""
    # the role comes from the class (isinstance); the class path is the
    # same form a point-of-use declaration writes
    with AiTest() as app:
        governor = DenyingSilently(app)
        governor._setup(app, 'truncate')
        assert app.ai._governor_label(governor) == "transformer 'truncate'"
        stray = DenyingOnReturn(app)
        # computed, not hard-coded: the module of a class defined inside a
        # test file depends on how pytest imported it; the rule under test is
        # the FORM (module:Class), not one specific path
        path = f'{type(stray).__module__}:{type(stray).__name__}'
        assert app.ai._governor_label(stray) == f'conductor {path!r}'
