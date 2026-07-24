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


# dotted paths to the importable test tools the subprocess worker loads
RAISE = 'tests.core.ai.tools.RaiseTool'
DATA = 'tests.core.ai.tools.DataTool'
NOTHING = 'tests.core.ai.tools.NothingTool'


class ToolResultTest(TokeoTest):

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
    # a self-contained ai config with the three tools these tests need, each
    # reachable in both the in_process catch-all and the subprocess sandbox so
    # the same tool can be driven behind either wall
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='mock', agent=None),
        tools={
            'boom': dict(type=RAISE),
            'data': dict(type=DATA),
            'void': dict(type=NOTHING),
        },
        sandboxes={
            'allow': dict(type='in_process', tools='_all'),
            'jailed': dict(type='subprocess', tools=['boom', 'data', 'void'], options=dict(timeout=5)),
        },
        agents={
            'plain': dict(type='fundi', options=dict(sandboxes=['allow'])),
            'jail': dict(type='fundi', options=dict(sandboxes=['jailed'])),
        },
        profiles={'mock': dict(type='mock', agent='plain')},
    )
    return cfg


# --------------------------------------------------------------------------------------
# create_tool_result: the termination states and the three views
# --------------------------------------------------------------------------------------


def test_in_process_catches_a_raising_tool_into_state_exception():
    # a tool that raises does not propagate out of the in_process sandbox: it is
    # caught and recorded in state.exception (tool-error A), with value left None
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert isinstance(out, ToolResult)
        assert out.value is None
        assert out.state.exception is not None
        assert 'tool failed on purpose' in out.state.exception


def test_subprocess_catches_a_raising_tool_into_state_exception():
    # the same contract behind the subprocess wall: the child catches the raise
    # and the parent rebuilds a ToolResult carrying state.exception, value None
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('jail')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert out.value is None
        assert out.state.exception is not None
        assert 'tool failed on purpose' in out.state.exception


def test_tool_error_a_and_machinery_error_b_are_separate():
    # the A/B split at the sandbox seam: a tool that raises is caught and rides
    # in state.exception (A), the sandbox still returns a result; a machinery
    # failure (here a subprocess timeout) is raised as TokeoAiError (B) and
    # propagates, so the loop -- not the result -- carries it. the loop's
    # content shaping of these into 'error: ...' is exercised by the Spiral tests
    cfg = ai_config()
    with ToolResultTest(config_defaults=cfg) as app:
        # A: a raising tool returns a result with state.exception set
        agent = app.ai._agent('plain')
        out = app.ai._exec_in_sandbox('boom', {}, agent, None, None, None, None)
        assert out.state.exception is not None
    # B: a subprocess timeout is a machinery failure -- raised, not in state
    cfg['ai']['sandboxes']['jailed']['options']['timeout'] = 1
    cfg['ai']['tools']['slow'] = dict(type='tests.core.ai.tools.SleepTool')
    cfg['ai']['sandboxes']['jailed']['tools'].append('slow')
    with ToolResultTest(config_defaults=cfg) as app:
        agent = app.ai._agent('jail')
        with pytest.raises(TokeoAiError, match='timed out'):
            app.ai._exec_in_sandbox('slow', dict(seconds=3), agent, None, None, None, None)


# --------------------------------------------------------------------------------------
# state fields: stdout the sandbox captures, no-result value
# --------------------------------------------------------------------------------------


def test_in_process_captures_tool_stdout_into_state():
    # what a tool prints is captured by the sandbox and folded into state.stdout
    # rather than leaking to the app console
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.state.stdout is not None
        assert 'side output' in out.state.stdout


def test_subprocess_captures_tool_stdout_into_state():
    # the same capture behind the subprocess wall: the child collects stdout and
    # the parent carries it in state.stdout
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('jail')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.state.stdout is not None
        assert 'side output' in out.state.stdout


def test_a_structured_tool_result_keeps_its_views_through_the_sandbox():
    # a tool returning a dict comes back with the structured view intact in
    # process (as_data is the dict), so a guard or the trace sees the structure
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        out = app.ai._exec_in_sandbox('data', {}, agent, None, None, None, None)
        assert out.value is not None
        assert out.value.as_data == dict(answer=42, label='ok')


def test_a_tool_returning_nothing_yields_no_value():
    # a tool that returns nothing comes back as the no-result state: value None,
    # no exception -- "nothing to deliver", not "it failed"
    with ToolResultTest(config_defaults=ai_config()) as app:
        agent = app.ai._agent('plain')
        out = app.ai._exec_in_sandbox('void', {}, agent, None, None, None, None)
        assert out.value is None
        assert out.state.exception is None
