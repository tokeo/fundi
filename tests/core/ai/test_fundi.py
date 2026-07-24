"""
Tests for the sandbox helpers that need no app.

Environment scrubbing, the runner's memory cap fallbacks, and the shared
```_setup``` contract of the ai classes -- all driven directly. Everything that
goes through the agent chain lives in ```tests/ext/ai/test_ai_fundi.py```.
"""

import pytest
from tokeo.core.ai import TokeoAiError
from tests.core.ai.tools import GreetTool
from tokeo.core.ai.sandboxes._common import expand_env


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


def test_setup_reads_the_declaration_once_and_is_idempotent():
    # the declaration is consumed at setup time (the extension rule: read the
    # config once, then work off the attributes). a second setup with another
    # declaration takes effect -- nothing stale survives it
    tool = GreetTool(None)
    tool._setup(None, 'greeter', dict(options=dict(prefix='eins')))
    assert tool._config('prefix') == 'eins'
    tool._setup(None, 'greeter', dict(options=dict(prefix='zwei')))
    assert tool._config('prefix') == 'zwei'


def test_an_object_without_setup_is_not_initialized():
    # construction alone is not a lifecycle: setup IS the initialization. an
    # object that never saw one fails on the first _config read with a named
    # error instead of running on silently guessed settings; only _setup can
    # hand in a config name (the property has no setter)
    tool = GreetTool(None)
    assert tool.config_name == 'tests.core.ai.tools.GreetTool'
    with pytest.raises(TokeoAiError, match='GreetTool: config options were not set by setup'):
        tool._config('prefix')
    with pytest.raises(AttributeError):
        tool.config_name = 'poked'


def test_a_setup_without_a_config_yields_the_class_defaults():
    # _setup with no declaration (the dotted-governor and unit-test path):
    # the view is the class's config_defaults -- and it is a deep copy, so a
    # write into it can never bleed into the class-level dict
    tool = GreetTool(None)
    tool._setup(None)
    assert tool.config_name == 'tests.core.ai.tools.GreetTool'
    assert tool._config('prefix') == 'hello'
    tool._config_options['prefix'] = 'poked'
    assert GreetTool.Meta.config_defaults == {'prefix': 'hello'}
