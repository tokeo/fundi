"""
Tests for the ai extension's sandbox seam.

Driven through ```app.ai._agent``` and ```app.ai._exec_in_sandbox```: which
sandbox a chain picks, how deny narrows the tool set at every level, how a tool
reads its declared options, and what the invocation records about where it ran.
The app-free helpers are tested in ```tests/core/ai/test_fundi.py```.
"""

import os
import sys
import inspect
import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError, TokeoAiFundiAgent, TokeoAiAgent
from tokeo.core.ai import TokeoAiProvider, TokeoAiSandbox, TokeoAiTool, TokeoAiGovernor
from tests.ext.ai.ai_tests_helper import GreetTool
from tokeo.core.ai.sandboxes.in_process import TokeoAiInProcessSandbox
from tokeo.core.ai.sandboxes.subprocess import TokeoAiSubprocessSandbox
from tokeo.core.ai.linter import TokeoAiLinter

# dotted paths to the importable test tools the worker loads in a child
ECHO = 'tests.ext.ai.ai_tests_helper.EchoTool'
CWD = 'tests.ext.ai.ai_tests_helper.CwdTool'
GREET = 'tests.ext.ai.ai_tests_helper.GreetTool'
ENV = 'tests.ext.ai.ai_tests_helper.EnvTool'

# the jailed options every platform can enforce; the memory cap is added on
# linux only: macos cannot bind RLIMIT_AS/DATA below the already mapped
# address space and the runner refuses sham caps by design
JAILED_OPTIONS = dict(timeout=5)
if sys.platform == 'linux':
    JAILED_OPTIONS['memory_mb'] = 256
SLEEP = 'tests.ext.ai.ai_tests_helper.SleepTool'


class FundiTest(TokeoTest):
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


def ai_config():
    """Return the ai config every test starts from, as config_defaults."""
    # a self-contained ai config: tools by dotted path, a group, both core
    # sandboxes (one subprocess "jailed", the in_process catch-all "allow"),
    # and agents that exercise every selection branch
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='mock', agent=None),
        tools={
            'echo': dict(type=ECHO),
            'cwd': dict(type=CWD),
            'env': dict(type=ENV),
            'sleep': dict(type=SLEEP),
            # a tool with options: the declaration overlays its config_defaults
            'greeter': dict(type=GREET, options=dict(prefix='moin')),
            # the same class without options: it runs on its config_defaults
            'plain_greeter': dict(type=GREET),
            'bundle': ['echo', 'cwd'],
        },
        sandboxes={
            'allow': dict(type='in_process', tools='_all'),
            'jailed': dict(
                type='subprocess',
                tools=['echo', 'cwd', 'env', 'sleep'],
                options=dict(JAILED_OPTIONS),
            ),
            # lists the bundle but excepts cwd, so the chain must walk on for it
            'partial': dict(type='subprocess', tools=['bundle'], **{'except': ['cwd']}),
        },
        agents={
            # everything in process
            'plain': dict(type='fundi', options=dict(sandboxes=['allow'])),
            # echo -> jailed (subprocess), the rest falls through to allow
            'mixed': dict(type='fundi', options=dict(sandboxes=['jailed', 'allow'])),
            # no catch-all: a tool no sandbox lists is denied
            'strict': dict(type='fundi', options=dict(sandboxes=['jailed'])),
            # hard deny wins before any sandbox lookup
            'banning': dict(type='fundi', options=dict(deny=['echo'], sandboxes=['allow'])),
            # partial sandbox skips cwd via except, then allow catches it
            'walking': dict(type='fundi', options=dict(sandboxes=['partial', 'allow'])),
            # empty chain -> deny by default
            'empty': dict(type='fundi', options=dict(sandboxes=[])),
            # carries echo + cwd; the narrow profile denies cwd off this set
            'echoer': dict(type='fundi', options=dict(tools=['echo', 'cwd'], sandboxes=['allow'])),
        },
        profiles={
            'mock': dict(type='mock', agent='plain'),
            # shares the plain agent but denies one tool/group of its set
            'narrow': dict(type='mock', agent='echoer', deny=['cwd']),
        },
    )
    return cfg


def run(app, tool_name, agent_name, **arguments):
    """Run one tool call through the agent's sandbox chain, return its text."""
    # drive just the seam: resolve the agent and execute one tool call through
    # its sandbox chain, returning the model-facing text. value is None when the
    # tool returned nothing, so guard the access
    agent = app.ai._agent(agent_name)
    out = app.ai._exec_in_sandbox(tool_name, arguments, agent, None, None, None, None)
    return out.value.as_str if out.value else ''


# --------------------------------------------------------------------------------------
# the agent classes
# --------------------------------------------------------------------------------------


def test_fundi_agent_is_the_concrete_default():
    """'fundi' resolves to TokeoAiFundiAgent, a concrete TokeoAiAgent subclass."""
    assert issubclass(TokeoAiFundiAgent, TokeoAiAgent)
    with FundiTest(config_defaults=ai_config()) as app:
        assert app.ai.resolve('agent', 'fundi') is TokeoAiFundiAgent
        # the old alias is gone
        with pytest.raises(TokeoAiError):
            app.ai.resolve('agent', 'default')


# --------------------------------------------------------------------------------------
# in_process sandbox (behaviour 1:1)
# --------------------------------------------------------------------------------------


def test_in_process_runs_directly():
    """echo under the plain agent runs in process: 'hi' comes back as 'hi'."""
    with FundiTest(config_defaults=ai_config()) as app:
        assert run(app, 'echo', 'plain', text='hi') == 'hi'


# --------------------------------------------------------------------------------------
# subprocess sandbox: real execution, cwd, env, timeout
# --------------------------------------------------------------------------------------


def test_subprocess_runs_in_a_child(tmp):
    """echo under mixed runs in the jailed child, 'from-child' comes back."""
    with FundiTest(config_defaults=ai_config()) as app:
        # echo is in jailed.tools (subprocess) under the mixed agent
        assert run(app, 'echo', 'mixed', text='from-child') == 'from-child'


def test_subprocess_cwd_takes_effect(tmp_path):
    """The child runs in the sandbox's cwd <tmp_path>/sbx, made on demand."""
    cfg = ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['cwd'] = str(tmp_path / 'sbx')
    with FundiTest(config_defaults=cfg) as app:
        out = run(app, 'cwd', 'mixed')
        assert out == str(tmp_path / 'sbx')


def test_subprocess_env_is_scrubbed():
    """jailed lists no env, so host HOME is '<unset>' in the scrubbed child."""
    with FundiTest(config_defaults=ai_config()) as app:
        assert run(app, 'env', 'mixed', name='HOME') == '<unset>'


def test_subprocess_timeout_is_enforced():
    """A 3 s sleep under a 1 s jailed timeout raises TokeoAiError 'timed out'."""
    cfg = ai_config()
    # tighten the jailed timeout and have the tool sleep past it
    cfg['ai']['sandboxes']['jailed']['options']['timeout'] = 1
    with FundiTest(config_defaults=cfg) as app:
        with pytest.raises(TokeoAiError, match='timed out'):
            run(app, 'sleep', 'mixed', seconds=3)


# --------------------------------------------------------------------------------------
# selection: chain order, except, _all, hard deny, deny-by-default
# --------------------------------------------------------------------------------------


def test_chain_picks_first_sandbox_whose_tools_contain():
    """Under mixed, echo and env go to jailed, the first sandbox listing them."""
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('mixed')
        # echo is in jailed.tools (first in the chain) -> subprocess
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiSubprocessSandbox)
        # env is in jailed.tools too
        assert isinstance(app.ai._sandbox_for('env', agent), TokeoAiSubprocessSandbox)


def test_except_skips_a_sandbox_and_chain_walks_on():
    """partial excepts cwd: echo runs in partial, cwd walks on to allow."""
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('walking')
        # partial lists the bundle (echo, cwd) but excepts cwd; so echo runs
        # in partial (subprocess) while cwd falls through to allow (in process)
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiSubprocessSandbox)
        assert isinstance(app.ai._sandbox_for('cwd', agent), TokeoAiInProcessSandbox)


def test_all_keyword_contains_every_tool():
    """A sandbox with tools: _all takes any tool, e.g. sleep under plain."""
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        # the allow sandbox has tools: _all, so any tool resolves to it
        assert isinstance(app.ai._sandbox_for('sleep', agent), TokeoAiInProcessSandbox)


def test_hard_deny_forbids_before_lookup():
    """The agent's deny refuses echo before any sandbox lookup: 'is denied'."""
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError, match='is denied'):
            run(app, 'echo', 'banning', text='x')


def test_empty_chain_denies_by_default():
    """An agent with an empty sandbox chain refuses echo: 'no sandbox'."""
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError, match='no sandbox'):
            run(app, 'echo', 'empty', text='x')


def test_profile_deny_subtracts_from_the_agent_set():
    """Active is agent.tools minus profile.deny: echo + cwd minus cwd is echo."""
    with FundiTest(config_defaults=ai_config()) as app:
        name, profile = app.ai._resolve(profile='narrow')
        agent = app.ai._agent('echoer')
        active = app.ai._tools_minus_deny(agent, profile)
        assert 'echo' in active and 'cwd' not in active


def test_profile_deny_is_enforced_at_exec():
    """A profile-denied cwd is refused at exec, the defence behind trimmed specs."""
    with FundiTest(config_defaults=ai_config()) as app:
        name, profile = app.ai._resolve(profile='narrow')
        agent = app.ai._agent('echoer')
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('cwd', {}, agent, profile, None, None, None)


def test_call_deny_narrows_further():
    """A call-level deny of echo leaves echoer's echo + cwd as just ['cwd']."""
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('echoer')
        active = app.ai._tools_minus_deny(agent, None, ['echo'])
        assert active == ['cwd']


def test_call_deny_is_enforced_at_exec():
    """A call can only narrow: a call-denied echo is refused at the seam."""
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('echoer')
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('echo', {'text': 'x'}, agent, None, ['echo'], None, None)


def test_strict_agent_denies_unlisted_tool():
    """strict runs jailed's echo; an unconfigured tool raises TokeoAiError."""
    with FundiTest(config_defaults=ai_config()) as app:
        # the strict agent has only jailed; jailed lists echo, so echo is ok
        assert run(app, 'echo', 'strict', text='ok') == 'ok'
        # but a tool jailed does not list has no catch-all -> denied. add a
        # tool not in jailed's list by denying via a fresh resolve: 'unknown'
        with pytest.raises(TokeoAiError):
            run(app, 'not_a_tool', 'strict')


# --------------------------------------------------------------------------------------
# set_sandbox override
# --------------------------------------------------------------------------------------


def test_set_sandbox_overrides_the_chain():
    """set_sandbox('jailed') beats plain's chain for echo; None restores it."""
    with FundiTest(config_defaults=ai_config()) as app:
        # force everything into jailed regardless of the agent; echo under the
        # plain agent would be in_process, but the override wins
        app.ai.set_sandbox('jailed')
        agent = app.ai._agent('plain')
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiSubprocessSandbox)
        # clearing restores the per-agent chain
        app.ai.set_sandbox(None)
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiInProcessSandbox)


def test_set_sandbox_rejects_unknown():
    """set_sandbox('does_not_exist') raises TokeoAiError right away."""
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError):
            app.ai.set_sandbox('does_not_exist')


# --------------------------------------------------------------------------------------
# env expansion helper
# --------------------------------------------------------------------------------------


def test_linter_accepts_a_sound_sandbox_config():
    """The linter reports no error for the base ai_config()."""
    with FundiTest(config_defaults=ai_config()) as app:
        issues = TokeoAiLinter(app).lint()
        errors = [i for i in issues if i.level == 'error']
        assert errors == [], errors


def test_linter_flags_unknown_sandbox_in_chain():
    """A chain naming the undeclared sandbox 'ghost' is a linter error."""
    cfg = ai_config()
    cfg['ai']['agents']['plain']['options']['sandboxes'] = ['ghost']
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('ghost' in i.message and i.level == 'error' for i in issues)


def test_linter_accepts_a_tuple_as_list():
    """A chain built in python as tuple ('jailed',) lints clean like a yaml list."""
    cfg = ai_config()
    cfg['ai']['agents']['plain']['options']['sandboxes'] = ('jailed',)
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert [i for i in issues if i.path.endswith('.sandboxes')] == []


def test_linter_flags_missing_sandbox_tools():
    """A sandbox without tools is flagged: tools is required."""
    cfg = ai_config()
    del cfg['ai']['sandboxes']['jailed']['tools']
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('required' in i.message for i in issues)


def test_linter_flags_unknown_subprocess_option():
    """The unknown subprocess option 'net' is flagged by the linter."""
    cfg = ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['net'] = False
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('net' in i.message for i in issues)


def test_linter_warns_when_license_acknowledged_is_missing():
    """Without license_acknowledged the linter warns; set, even to false, none."""
    cfg = ai_config()
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert [(i.path, i.level) for i in issues if 'license_acknowledged' in i.path] == [('ai.license_acknowledged', 'warning')]
    cfg['ai']['license_acknowledged'] = False
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert [i for i in issues if 'license_acknowledged' in i.path] == []


@pytest.mark.parametrize('value', [True, -1, 1.5, '3'])
def test_linter_flags_max_steps_that_is_no_whole_number_of_0_or_more(value):
    """A bool, negative, float or str max_steps is flagged; 0 means unlimited."""
    cfg = ai_config()
    cfg['ai']['agents']['plain']['options']['max_steps'] = value
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any(i.path.endswith('.max_steps') for i in issues)


def test_subprocess_resolves_registry_alias_tools():
    """A registry alias runs in the child: the LOADED class's path crosses over."""
    from tests.ext.ai.ai_tests_helper import EchoTool

    cfg = ai_config()
    cfg['ai']['tools']['shorty'] = dict(type='echo_short')
    cfg['ai']['sandboxes']['jailed']['tools'].append('shorty')
    with FundiTest(config_defaults=cfg) as app:
        app.ai.register('tool', 'echo_short', EchoTool)
        assert run(app, 'shorty', 'mixed', text='hi') == 'hi'


def test_subprocess_refuses_classes_a_child_cannot_import():
    """A nested Outer.GhostTool is refused early, not importable by module path."""
    # a script's __main__ case fails the same guard; ```python -m``` resolves
    # via the module spec
    from tests.ext.ai.ai_tests_helper import EchoTool

    Ghost = type('GhostTool', (EchoTool,), {'__qualname__': 'Outer.GhostTool'})
    with FundiTest(config_defaults=ai_config()) as app:
        tool = Ghost(None)
        sandbox = app.ai._sandbox('jailed')
        with pytest.raises(TokeoAiError, match='not importable by module path'):
            sandbox.exec(tool, {'text': 'hi'})


def test_subprocess_keeps_a_user_pythonpath_in_the_lead():
    """A PYTHONPATH in options.env stays first, the parent's path is appended."""
    cfg = ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['env'] = {'PYTHONPATH': '/user/extra'}
    with FundiTest(config_defaults=cfg) as app:
        out = run(app, 'env', 'mixed', name='PYTHONPATH')
        assert out.startswith('/user/extra' + os.pathsep)


# --------------------------------------------------------------------------------------
# the config surface: every ai class is set up the same way
# --------------------------------------------------------------------------------------


def test_setup_is_one_form_across_the_ai_classes():
    """All ai classes set up via _setup(app, config_name, config), one form."""
    # the handler writes no attributes from outside
    for cls in (TokeoAiProvider, TokeoAiSandbox, TokeoAiAgent, TokeoAiTool, TokeoAiGovernor):
        params = list(inspect.signature(cls._setup).parameters)
        assert params == ['self', 'app', 'config_name', 'config'], cls.__name__


def test_a_tool_reads_its_declared_options_through_config():
    """Option prefix='moin' overlays config_defaults, read lazily by _config."""
    with FundiTest(config_defaults=ai_config()) as app:
        assert run(app, 'greeter', 'plain', name='ada') == 'moin ada'
        tool = app.ai._tool('greeter')
        assert tool.config_name == 'greeter'
        assert tool._config('prefix') == 'moin'


def test_a_tool_without_options_runs_on_its_config_defaults():
    """No options declared: the Meta default 'hello' stands, no None leaks in."""
    with FundiTest(config_defaults=ai_config()) as app:
        assert run(app, 'plain_greeter', 'plain', name='ada') == 'hello ada'
        assert app.ai._tool('plain_greeter')._config('prefix') == 'hello'


def test_two_declarations_of_one_class_stay_separate():
    """One class under two keys: two objects, names, options; defaults unmutated."""
    with FundiTest(config_defaults=ai_config()) as app:
        greeter, plain = app.ai._tool('greeter'), app.ai._tool('plain_greeter')
        assert greeter is not plain
        assert (greeter.config_name, plain.config_name) == ('greeter', 'plain_greeter')
        assert (greeter._config('prefix'), plain._config('prefix')) == ('moin', 'hello')
        assert TokeoAiTool.Meta.config_defaults == {}
        assert GreetTool.Meta.config_defaults == {'prefix': 'hello'}


def test_options_do_not_reach_the_class_meta():
    """An option named description stays an option, the spec keeps the Meta text."""
    cfg = ai_config()
    cfg['ai']['tools']['greeter']['options'] = dict(prefix='moin', description='REWRITTEN')
    with FundiTest(config_defaults=cfg) as app:
        spec = app.ai._tool_specs(['greeter'])[0]['function']
        assert spec['description'] == 'greet the given name'
        assert spec['name'] == 'greeter'
        # it stays reachable as what it is: an option
        assert app.ai._tool('greeter')._config('description') == 'REWRITTEN'


def test_the_sandbox_carries_its_config_name_to_the_invocation():
    """The jailed sandbox knows its key 'jailed', so a call records WHERE it ran."""
    with FundiTest(config_defaults=ai_config()) as app:
        assert app.ai._sandbox('jailed').config_name == 'jailed'
