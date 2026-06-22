"""
Tests for the fundi agent and the sandbox layer (tokeo core).

Covers the sandbox seam end to end through ```app.ai```: the ```in_process``` and
```subprocess``` sandboxes, the per-agent selection rule (```tools```/```_all```,
```except```, the ordered chain, the hard ```deny```, deny-by-default), the
```set_sandbox``` override, the env expansion helper, and the linter's coverage
of the new ```ai.sandboxes``` section and agent fields. The full LLM loop is
exercised by the Spiral tests; here the focus is the mechanics in isolation.
"""

import os
import sys
import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError, ToolResult, TokeoAiFundiAgent, TokeoAiAgent
from tokeo.core.ai.sandboxes._common import expand_env
from tokeo.core.ai.sandboxes.in_process import TokeoAiInProcessSandbox
from tokeo.core.ai.sandboxes.subprocess import TokeoAiSubprocessSandbox
from tokeo.core.ai.linter import TokeoAiLinter


# dotted paths to the importable test tools the worker loads in a child
ECHO = 'tests.core.ai.tools.EchoTool'
CWD = 'tests.core.ai.tools.CwdTool'
ENV = 'tests.core.ai.tools.EnvTool'

# the jailed options every platform can enforce; the memory cap is added on
# linux only: macos cannot bind RLIMIT_AS/DATA below the already mapped
# address space and the runner refuses sham caps by design
JAILED_OPTIONS = dict(timeout=5, cwd='tmp/sbx')
if sys.platform == 'linux':
    JAILED_OPTIONS['memory_mb'] = 256
SLEEP = 'tests.core.ai.tools.SleepTool'


class FundiTest(TokeoTest):

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
    # drive just the seam: resolve the agent and execute one tool call through
    # its sandbox chain, returning the model-facing text
    agent = app.ai._agent(agent_name)
    out = app.ai._exec_in_sandbox(tool_name, arguments, agent)
    return out.text if isinstance(out, ToolResult) else str(out)


# --------------------------------------------------------------------------------------
# the agent classes
# --------------------------------------------------------------------------------------


def test_fundi_agent_is_the_concrete_default():
    # the standard agent is fundi, a concrete subclass of the declarative base
    assert issubclass(TokeoAiFundiAgent, TokeoAiAgent)
    with FundiTest(config_defaults=ai_config()) as app:
        assert app.ai.resolve('agent', 'fundi') is TokeoAiFundiAgent
        # the old short name is gone
        with pytest.raises(TokeoAiError):
            app.ai.resolve('agent', 'default')


# --------------------------------------------------------------------------------------
# in_process sandbox (behaviour 1:1)
# --------------------------------------------------------------------------------------


def test_in_process_runs_directly():
    with FundiTest(config_defaults=ai_config()) as app:
        assert run(app, 'echo', 'plain', text='hi') == 'hi'


# --------------------------------------------------------------------------------------
# subprocess sandbox: real execution, cwd, env, timeout
# --------------------------------------------------------------------------------------


def test_subprocess_runs_in_a_child(tmp):
    with FundiTest(config_defaults=ai_config()) as app:
        # echo is in jailed.tools (subprocess) under the mixed agent
        assert run(app, 'echo', 'mixed', text='from-child') == 'from-child'


def test_subprocess_cwd_takes_effect():
    with FundiTest(config_defaults=ai_config()) as app:
        # the jailed sandbox sets cwd to tmp/sbx; the tool reports it back
        out = run(app, 'cwd', 'mixed')
        assert out.endswith('tmp/sbx')


def test_subprocess_env_is_scrubbed():
    with FundiTest(config_defaults=ai_config()) as app:
        # jailed lists no env, so the child sees a scrubbed environment; a
        # common host var like HOME must come back unset
        assert run(app, 'env', 'mixed', name='HOME') == '<unset>'


def test_subprocess_timeout_is_enforced():
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
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('mixed')
        # echo is in jailed.tools (first in the chain) -> subprocess
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiSubprocessSandbox)
        # env is in jailed.tools too
        assert isinstance(app.ai._sandbox_for('env', agent), TokeoAiSubprocessSandbox)


def test_except_skips_a_sandbox_and_chain_walks_on():
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('walking')
        # partial lists the bundle (echo, cwd) but excepts cwd; so echo runs
        # in partial (subprocess) while cwd falls through to allow (in process)
        assert isinstance(app.ai._sandbox_for('echo', agent), TokeoAiSubprocessSandbox)
        assert isinstance(app.ai._sandbox_for('cwd', agent), TokeoAiInProcessSandbox)


def test_all_keyword_contains_every_tool():
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        # the allow sandbox has tools: _all, so any tool resolves to it
        assert isinstance(app.ai._sandbox_for('sleep', agent), TokeoAiInProcessSandbox)


def test_hard_deny_forbids_before_lookup():
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError, match='is denied'):
            run(app, 'echo', 'banning', text='x')


def test_empty_chain_denies_by_default():
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError, match='no sandbox'):
            run(app, 'echo', 'empty', text='x')


def test_profile_deny_subtracts_from_the_agent_set():
    with FundiTest(config_defaults=ai_config()) as app:
        # the echoer agent carries echo + cwd; the narrow profile denies cwd,
        # so the active set is just echo (agent.tools minus profile.deny)
        name, profile = app.ai._resolve(profile='narrow')
        agent = app.ai._agent('echoer')
        active = app.ai._tools_minus_deny(agent, profile)
        assert 'echo' in active and 'cwd' not in active


def test_profile_deny_is_enforced_at_exec():
    with FundiTest(config_defaults=ai_config()) as app:
        # even if a model calls a profile-denied tool, the seam refuses it as
        # the defence line behind the trimmed specs
        name, profile = app.ai._resolve(profile='narrow')
        agent = app.ai._agent('echoer')
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('cwd', {}, agent, profile)


def test_call_deny_narrows_further():
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('echoer')
        # echoer carries echo + cwd; a call-level deny of echo leaves only cwd
        active = app.ai._tools_minus_deny(agent, None, ['echo'])
        assert active == ['cwd']


def test_call_deny_is_enforced_at_exec():
    with FundiTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('echoer')
        # a call can only narrow: a call-denied tool is refused at the seam
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('echo', {'text': 'x'}, agent, None, ['echo'])


def test_strict_agent_denies_unlisted_tool():
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
    with FundiTest(config_defaults=ai_config()) as app:
        with pytest.raises(TokeoAiError):
            app.ai.set_sandbox('does_not_exist')


# --------------------------------------------------------------------------------------
# env expansion helper
# --------------------------------------------------------------------------------------


def test_expand_env_scrubs_and_expands(monkeypatch):
    monkeypatch.setenv('FUNDI_HOST_VAR', 'host-value')
    out = expand_env(
        {
            'A': 'literal',
            'B': '${FUNDI_HOST_VAR}/sub',
            'C': '${A}-${B}',
            'D': '${MISSING_VAR}x',
            'E': 'price is $5',
        }
    )
    assert out == {
        'A': 'literal',
        'B': 'host-value/sub',
        'C': 'literal-host-value/sub',
        'D': 'x',
        'E': 'price is $5',
    }
    # only the listed keys are present (scrubbed)
    assert set(out) == {'A', 'B', 'C', 'D', 'E'}


# --------------------------------------------------------------------------------------
# linter coverage of the new section and fields
# --------------------------------------------------------------------------------------


def test_linter_accepts_a_sound_sandbox_config():
    with FundiTest(config_defaults=ai_config()) as app:
        issues = TokeoAiLinter(app).lint()
        errors = [i for i in issues if i.level == 'error']
        assert errors == [], errors


def test_linter_flags_unknown_sandbox_in_chain():
    cfg = ai_config()
    cfg['ai']['agents']['plain']['options']['sandboxes'] = ['ghost']
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('ghost' in i.message and i.level == 'error' for i in issues)


def test_linter_flags_missing_sandbox_tools():
    cfg = ai_config()
    del cfg['ai']['sandboxes']['jailed']['tools']
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('required' in i.message for i in issues)


def test_linter_flags_unknown_subprocess_option():
    cfg = ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['net'] = False
    with FundiTest(config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('net' in i.message for i in issues)


def test_runner_memory_cap_falls_back_per_platform(monkeypatch):
    import resource as res_mod
    from tokeo.core.ai.sandboxes import runner

    attempts = []

    def fake_getrlimit(res):
        return (res_mod.RLIM_INFINITY, res_mod.RLIM_INFINITY)

    def fake_setrlimit(res, lim):
        attempts.append(res)
        # RLIMIT_AS is rejected entirely, the way macos does
        if res == res_mod.RLIMIT_AS:
            raise ValueError('current limit exceeds maximum limit')

    monkeypatch.setattr(res_mod, 'getrlimit', fake_getrlimit)
    monkeypatch.setattr(res_mod, 'setrlimit', fake_setrlimit)
    # must fall through to the next mechanism instead of crashing the call
    runner._set_caps({'memory_mb': 64})
    assert res_mod.RLIMIT_DATA in attempts


def test_runner_memory_cap_soft_only_when_hard_is_refused(monkeypatch):
    import resource as res_mod
    from tokeo.core.ai.sandboxes import runner

    calls = []

    def fake_getrlimit(res):
        return (res_mod.RLIM_INFINITY, res_mod.RLIM_INFINITY)

    def fake_setrlimit(res, lim):
        calls.append(lim)
        # the platform refuses any change to the HARD limit (macos-style);
        # the soft-only form is accepted -- and the soft limit is the one
        # the kernel enforces, so this is real enforcement
        if lim[1] != res_mod.RLIM_INFINITY:
            raise ValueError('current limit exceeds maximum limit')

    monkeypatch.setattr(res_mod, 'getrlimit', fake_getrlimit)
    monkeypatch.setattr(res_mod, 'setrlimit', fake_setrlimit)
    runner._set_caps({'memory_mb': 64})
    cap = 64 * 1024 * 1024
    # first the pinned pair, then the accepted soft-only fallback
    assert calls == [(cap, cap), (cap, res_mod.RLIM_INFINITY)]


def test_runner_memory_cap_refuses_a_sham_setting(monkeypatch):
    import resource as res_mod
    from tokeo.core.ai.sandboxes import runner

    def fake_setrlimit(res, lim):
        # every mechanism is rejected, like a platform that supports none
        raise ValueError('current limit exceeds maximum limit')

    monkeypatch.setattr(res_mod, 'setrlimit', fake_setrlimit)
    # a configured cap that cannot be kept must error, not silently skip
    with pytest.raises(RuntimeError, match='not enforceable'):
        runner._set_caps({'memory_mb': 64})


def test_subprocess_resolves_registry_shortname_tools():
    # the import path crosses the boundary as the canonical path of the
    # LOADED class, so a registry shortname in the config simply works
    from tests.core.ai.tools import EchoTool

    cfg = ai_config()
    cfg['ai']['tools']['shorty'] = dict(type='echo_short')
    cfg['ai']['sandboxes']['jailed']['tools'].append('shorty')
    with FundiTest(config_defaults=cfg) as app:
        app.ai.register('tool', 'echo_short', EchoTool)
        assert run(app, 'shorty', 'mixed', text='hi') == 'hi'


def test_subprocess_refuses_classes_a_child_cannot_import():
    # a nested class has no top-level module path the child could import;
    # the sandbox refuses early with the reason (a script's __main__ case
    # fails the same guard; ```python -m``` resolves via the module spec)
    from tests.core.ai.tools import EchoTool

    Ghost = type('GhostTool', (EchoTool,), {'__qualname__': 'Outer.GhostTool'})
    with FundiTest(config_defaults=ai_config()) as app:
        tool = Ghost(None)
        sandbox = app.ai._sandbox('jailed')
        with pytest.raises(TokeoAiError, match='not importable by module path'):
            sandbox.exec(tool, {'text': 'hi'})


def test_subprocess_keeps_a_user_pythonpath_in_the_lead():
    # env tool reports a variable; a PYTHONPATH listed in options.env must
    # survive the parent-path injection (user first, parent appended)
    cfg = ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['env'] = {'PYTHONPATH': '/user/extra'}
    with FundiTest(config_defaults=cfg) as app:
        out = run(app, 'env', 'mixed', name='PYTHONPATH')
        assert out.startswith('/user/extra' + os.pathsep)
