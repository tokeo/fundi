"""
Tests for the sandbox helpers that need no app.

Environment scrubbing, the runner's memory limit fallbacks, and the shared
```_setup``` contract of the ai classes, all driven directly. Everything that
goes through the agent chain lives in ```tests/ext/ai/test_ai.py```.
"""

import pytest
from tokeo.core.ai import TokeoAiError, TokeoAiTool
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.ai.sandboxes._common import expand_env


class GreetTool(TokeoAiTool):
    """Greet with a configurable prefix; proves a tool reads its own options."""

    class Meta:
        description = 'greet the given name'
        parameters = {
            'type': 'object',
            'properties': {'name': {'type': 'string'}},
            'required': ['name'],
        }
        # the tool's outward surface: what a project may set under ```options```
        config_defaults = dict(
            prefix='hello',
        )

    def exec(self, **arguments):
        return create_tool_result(f"{self._config('prefix')} {arguments.get('name', '')}")


def test_expand_env_scrubs_and_expands(monkeypatch):
    """${NAME} takes built keys, then the host env, else ''; a bare $ stays."""
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


# ---- linter coverage of the new section and fields --------------------------


def test_runner_memory_limit_falls_back_per_platform(monkeypatch):
    """With RLIMIT_AS refused (macos-style) a 64 MB limit goes to RLIMIT_DATA."""
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
    runner._set_limits({'max_memory_mb': 64})
    assert res_mod.RLIMIT_DATA in attempts


def test_runner_memory_limit_soft_only_when_hard_is_refused(monkeypatch):
    """A refused hard limit turns the pinned pair (max, max) into soft-only."""
    import resource as res_mod
    from tokeo.core.ai.sandboxes import runner

    calls = []

    def fake_getrlimit(res):
        return (res_mod.RLIM_INFINITY, res_mod.RLIM_INFINITY)

    def fake_setrlimit(res, lim):
        calls.append(lim)
        # the platform refuses any change to the HARD limit (macos-style);
        # the soft-only form is accepted, and the soft limit is the one
        # the kernel enforces, so this is real enforcement
        if lim[1] != res_mod.RLIM_INFINITY:
            raise ValueError('current limit exceeds maximum limit')

    monkeypatch.setattr(res_mod, 'getrlimit', fake_getrlimit)
    monkeypatch.setattr(res_mod, 'setrlimit', fake_setrlimit)
    runner._set_limits({'max_memory_mb': 64})
    memory_bytes = 64 * 1024 * 1024
    # first the pinned pair, then the accepted soft-only fallback
    assert calls == [(memory_bytes, memory_bytes), (memory_bytes, res_mod.RLIM_INFINITY)]


def test_runner_memory_limit_refuses_a_sham_limit(monkeypatch):
    """A 64 MB limit that no mechanism accepts raises 'not enforceable'."""
    import resource as res_mod
    from tokeo.core.ai.sandboxes import runner

    def fake_setrlimit(res, lim):
        # every mechanism is rejected, like a platform that supports none
        raise ValueError('current limit exceeds maximum limit')

    monkeypatch.setattr(res_mod, 'setrlimit', fake_setrlimit)
    # a configured limit that cannot be kept must error, not silently skip
    with pytest.raises(RuntimeError, match='not enforceable'):
        runner._set_limits({'max_memory_mb': 64})


def test_setup_reads_the_item_once_and_is_idempotent():
    """
    A second _setup replaces the first: prefix 'eins' becomes 'zwei'.

    The item is read once at setup (the extension rule: read the
    config once, then work off the attributes); nothing stale survives.
    """
    tool = GreetTool(None)
    tool._setup(None, 'greeter', dict(options=dict(prefix='eins')))
    assert tool._config('prefix') == 'eins'
    tool._setup(None, 'greeter', dict(options=dict(prefix='zwei')))
    assert tool._config('prefix') == 'zwei'


def test_an_object_without_setup_is_not_initialized():
    """
    Without _setup the first _config read raises a named TokeoAiError.

    Construction alone is not a lifecycle, setup is the initialization, so
    nothing runs on guessed options. config_name has no setter; only
    _setup can hand one in.
    """
    tool = GreetTool(None)
    assert tool.config_name == 'tests.core.ai.test_fundi:GreetTool'
    with pytest.raises(TokeoAiError, match='GreetTool: config options were not set by setup'):
        tool._config('prefix')
    with pytest.raises(AttributeError):
        tool.config_name = 'poked'


def test_a_setup_without_a_config_yields_the_class_defaults():
    """
    _setup without an item yields the class default prefix 'hello'.

    This is the path of a governor at the point of use and of a unit test.
    The view is a deep copy, so a write into it never bleeds into the
    class-level config_defaults.
    """
    tool = GreetTool(None)
    tool._setup(None)
    assert tool.config_name == 'tests.core.ai.test_fundi:GreetTool'
    assert tool._config('prefix') == 'hello'
    tool._config_options['prefix'] = 'poked'
    assert GreetTool.Meta.config_defaults == {'prefix': 'hello'}
