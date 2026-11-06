"""
Tests for what the ai extension makes of a tool result.

Driven through ```app.ai._exec_in_sandbox```: a raising tool becoming
```state.exception``` (tool error) while machinery failures surface separately,
the captured stdout, and a structured result keeping its views across the wall.
The ToolResult model itself is tested in ```tests/core/ai/test_toolresult.py```.
"""

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import ToolResult, TokeoAiError

# the class paths of the importable test tools the subprocess worker loads
RAISE = 'tests.ext.ai.lib.ai_tests_helper:RaiseTool'
DATA = 'tests.ext.ai.lib.ai_tests_helper:DataTool'
NOTHING = 'tests.ext.ai.lib.ai_tests_helper:NothingTool'


class ToolResultTest(TokeoTest):
    """A test app with the ai extension and the extensions it needs."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def make_ai_config():
    """An ai config with the tools boom, data and void behind either wall."""
    # a self-contained ai config with the three tools these tests need, each
    # reachable in both the in_process catch-all and the subprocess sandbox so
    # the same tool can be driven behind either wall
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='mock', agent=None),
        tools={
            'boom': dict(type=RAISE),
            'data': dict(type=DATA),
            'void': dict(type=NOTHING),
        },
        sandboxes={
            'allow': dict(type='in_process', tools=['boom', 'data', 'void']),
            'jailed': dict(type='subprocess', tools=['boom', 'data', 'void'], options=dict(timeout=5)),
        },
        agents={
            'plain': dict(type='fundi', options=dict(governors=[], tools=['boom', 'data', 'void'], sandboxes=['allow'])),
            'jail': dict(type='fundi', options=dict(governors=[], tools=['boom', 'data', 'void'], sandboxes=['jailed'])),
        },
        profiles={'mock': dict(type='mock', agent='plain')},
    )
    return cfg


# ---- create_tool_result: the termination states and the three views ---------


def test_in_process_catches_a_raising_tool_into_state_exception():
    """In process a raise lands in state.exception (tool error A), value None."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('plain')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert isinstance(out, ToolResult)
        assert out.value is None
        assert out.state.exception is not None
        assert 'tool failed on purpose' in out.state.exception


def test_subprocess_catches_a_raising_tool_into_state_exception():
    """The subprocess child catches the raise, the parent gets state.exception."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('jail')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert out.value is None
        assert out.state.exception is not None
        assert 'tool failed on purpose' in out.state.exception


def test_tool_error_a_and_machinery_error_b_are_separate():
    """
    A raising tool rides in state.exception (A), a machinery failure is raised (B).

    In process, boom returns a result with state.exception set. A subprocess
    timeout (slow sleeping 3s against a 1s timeout) raises TokeoAiError
    'timed out' and propagates, so the loop, not the result, carries it. The
    loop's shaping of both into 'error: ...' content is in the Spiral tests.
    """
    cfg = make_ai_config()
    with ToolResultTest(config_defaults=cfg) as app:
        # A: a raising tool returns a result with state.exception set
        agent = app.ai._get_agent('plain')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert out.state.exception is not None
    # B: a subprocess timeout is a machinery failure: raised, not in state
    cfg['ai']['sandboxes']['jailed']['options']['timeout'] = 1
    cfg['ai']['tools']['slow'] = dict(type='tests.ext.ai.lib.ai_tests_helper:SleepTool')
    cfg['ai']['sandboxes']['jailed']['tools'].append('slow')
    cfg['ai']['agents']['jail']['options']['tools'].append('slow')
    with ToolResultTest(config_defaults=cfg) as app:
        agent = app.ai._get_agent('jail')
        with pytest.raises(TokeoAiError, match='timed out'):
            app.ai._exec_in_sandbox('slow', dict(seconds=3), agent, None, None, None, None)


# ---- state fields: stdout the sandbox captures, no-result value -------------


def test_in_process_captures_tool_stdout_into_state():
    """In process printed 'side output' lands in state.stdout, not the console."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('plain')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.state.stdout is not None
        assert 'side output' in out.state.stdout


def test_subprocess_captures_tool_stdout_into_state():
    """In a subprocess the child's printed 'side output' reaches state.stdout."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('jail')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.state.stdout is not None
        assert 'side output' in out.state.stdout


def test_a_structured_tool_result_keeps_its_views_through_the_sandbox():
    """In process a returned dict keeps as_data {'answer': 42, 'label': 'ok'}."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('plain')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.value is not None
        assert out.value.as_data == dict(answer=42, label='ok')


def test_a_tool_returning_nothing_yields_no_value():
    """A tool returning nothing gives value None, no exception: not a failure."""
    with ToolResultTest(config_defaults=make_ai_config()) as app:
        agent = app.ai._get_agent('plain')
        out = app.ai._exec_in_sandbox('void', {}, agent, None, None, None, None)
        assert out.value is None
        assert out.state.exception is None
