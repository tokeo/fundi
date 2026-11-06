"""
Tests for the ai extension around a tool call.

Covers the four lifecycle contracts (prepare, before, after, teardown) and how
the loop runs them around the sandboxed exec: the order, the reshaping of
arguments and result, and what happens when any of them raises. Lives here
because it drives the handler in ```tokeo.ext.ai```.
"""

import inspect
import pytest

from tokeo.core.ai import TokeoAiTool, TokeoAiContext, TokeoAiError
from tokeo.core.ai.data import ToolResult, ToolStates
from tokeo.ext.ai import TokeoAi


class _BareTool(TokeoAiTool):
    """A tool that defines only exec and inherits the four hooks."""

    def exec(self, **arguments):
        return 'ok'


class _FramedTool(TokeoAiTool):
    """A tool that uses every hook, to prove they can be overridden."""

    def prepare(self, turndata, arguments, track):
        turndata['prepared'] = True

    def before(self, turndata, arguments, track):
        return {**arguments, 'path': 'safe/' + arguments['path']}

    def exec(self, path):
        return f'ran {path}'

    def after(self, turndata, arguments, result, track):
        return ToolResult(value=f'{result.value} (checked {arguments["path"]})')

    def teardown(self, turndata, arguments, result, track):
        turndata['torn_down'] = True


def test_hooks_default_to_noops():
    """The four inherited hooks return None and leave turndata as {}."""
    tool = _BareTool.__new__(_BareTool)
    td = {}

    def track_nothing(message):
        return None

    assert tool.prepare(td, {'a': 1}, track_nothing) is None
    assert tool.before(td, {'a': 1}, track_nothing) is None
    assert tool.after(td, {'a': 1}, ToolResult(value='x'), track_nothing) is None
    assert tool.teardown(td, {'a': 1}, ToolResult(value='x'), track_nothing) is None
    assert td == {}


def test_hook_signatures_match_the_contract():
    """prepare, before: (turndata, arguments, track); after, teardown add result."""

    def get_signature(m):
        return str(inspect.signature(getattr(TokeoAiTool, m)))

    assert get_signature('prepare') == '(self, turndata, arguments, track)'
    assert get_signature('before') == '(self, turndata, arguments, track)'
    assert get_signature('after') == '(self, turndata, arguments, result, track)'
    assert get_signature('teardown') == '(self, turndata, arguments, result, track)'


def test_before_may_reshape_and_return_a_dict():
    """before returns reshaped arguments: path 'x' comes back as 'safe/x'."""
    tool = _FramedTool.__new__(_FramedTool)
    reshaped = tool.before({}, {'path': 'x'}, lambda m: None)
    assert reshaped == {'path': 'safe/x'}


def test_after_may_reshape_the_result():
    """after turns 'ran safe/x' into a new result 'ran safe/x (checked safe/x)'."""
    tool = _FramedTool.__new__(_FramedTool)
    out = tool.after({}, {'path': 'safe/x'}, ToolResult(value='ran safe/x'), lambda m: None)
    assert out.value == 'ran safe/x (checked safe/x)'


def test_prepare_and_teardown_write_turndata():
    """prepare and teardown write into turndata: prepared and torn_down end True."""
    tool = _FramedTool.__new__(_FramedTool)
    td = {}
    tool.prepare(td, {'path': 'x'}, lambda m: None)
    tool.teardown(td, {'path': 'x'}, ToolResult(value='ok'), lambda m: None)
    assert td == {'prepared': True, 'torn_down': True}


def test_teardown_reads_exception_from_result():
    """On a failed call teardown gets 'ValueError: nope' in result.state."""
    tool = _BareTool.__new__(_BareTool)
    failed = ToolResult(value=None, state=ToolStates(exception='ValueError: nope'))
    # the default teardown is a no-op, but the contract is that result is always
    # a ToolResult and the exception lives in result.state.exception
    assert tool.teardown({}, {}, failed, lambda message: None) is None
    assert failed.state.exception == 'ValueError: nope'


# ---- the lifecycle as the loop runs it --------------------------------------


class _Sandbox:
    """Mirrors a real sandbox: a tool's throw is a result, only machinery rises."""

    def exec(self, tool, arguments):
        try:
            return tool.exec(**arguments)
        except Exception as err:
            return ToolResult(value=None, state=ToolStates(exception=f'{type(err).__name__}: {err}'))


class _LoggingTool(TokeoAiTool):
    """Records which hooks ran, and can be told which one should raise."""

    def __init__(self, fail=None):
        self.fail = fail
        self.ran = []

    def _log_step(self, name):
        self.ran.append(name)
        if self.fail == name:
            raise ValueError(f'boom-{name}')

    def prepare(self, turndata, arguments, track):
        track('prepared')
        self._log_step('prepare')

    def before(self, turndata, arguments, track):
        self._log_step('before')
        return {**arguments, 'path': 'safe/' + arguments['path']}

    def exec(self, path):
        self._log_step('exec')
        return ToolResult(value=f'ran {path}')

    def after(self, turndata, arguments, result, track):
        self._log_step('after')

    def teardown(self, turndata, arguments, result, track):
        self._log_step('teardown')


def _run_tool(tool, ctx=None):
    """Run the tool through _exec_lifecycle with path 'x' behind a _Sandbox."""
    handler = TokeoAi.__new__(TokeoAi)
    return handler._exec_lifecycle(tool, _Sandbox(), {'path': 'x'}, ctx or TokeoAiContext(messages=[]))


def test_happy_path_runs_all_hooks_and_before_reaches_exec():
    """All five steps run in order and exec gets the reshaped path: 'ran safe/x'."""
    tool = _LoggingTool()
    result = _run_tool(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'after', 'teardown']
    # the arguments before reshaped are the ones exec got
    assert result.value == 'ran safe/x'


def test_prepare_raising_skips_everything_including_teardown():
    """A raising prepare runs nothing else, not even teardown; its error returns."""
    tool = _LoggingTool(fail='prepare')
    result = _run_tool(tool)
    assert tool.ran == ['prepare']
    assert 'boom-prepare' in result.state.exception


def test_before_raising_short_circuits_to_teardown():
    """A raising before skips exec and after, teardown runs; its error returns."""
    tool = _LoggingTool(fail='before')
    result = _run_tool(tool)
    assert tool.ran == ['prepare', 'before', 'teardown']
    assert 'boom-before' in result.state.exception


def test_exec_raising_still_runs_teardown():
    """A raising exec skips after, teardown still runs; its error is the result."""
    tool = _LoggingTool(fail='exec')
    result = _run_tool(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'teardown']
    assert 'boom-exec' in result.state.exception


def test_after_raising_becomes_the_result():
    """A raising after still gets teardown, and its error replaces the result."""
    tool = _LoggingTool(fail='after')
    result = _run_tool(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'after', 'teardown']
    assert 'boom-after' in result.state.exception


def test_teardown_raising_is_noted_and_keeps_the_result():
    """A raising teardown keeps 'ran safe/x' and is traced as 'teardown failed'."""
    ctx = TokeoAiContext(messages=[])
    tool = _LoggingTool(fail='teardown')
    result = _run_tool(tool, ctx)
    # the settled result survives a failing teardown
    assert result.value == 'ran safe/x'
    assert result.state.exception is None
    notes = [step.object.content for step in ctx.trace if hasattr(step.object, 'content')]
    assert any('teardown failed' in note for note in notes)


def test_track_binds_origin_and_stage():
    """A note tracked in prepare has the tool as origin and stage 'prepare'."""
    ctx = TokeoAiContext(messages=[])
    tool = _LoggingTool()
    _run_tool(tool, ctx)
    step = next(s for s in ctx.trace if getattr(s.object, 'content', None) == 'prepared')
    assert step.origin is tool
    assert step.stage == 'prepare'


class _BrokenSandbox:
    """A sandbox whose machinery fails, like a timeout of the subprocess sandbox."""

    def exec(self, tool, arguments):
        raise TokeoAiError('timed out after 1s')


class _SeeingTool(TokeoAiTool):
    """Records the result its teardown gets."""

    seen = None

    def exec(self, path):
        return ToolResult(value='never')

    def teardown(self, turndata, arguments, result, track):
        self.seen = result


def test_teardown_gets_a_tool_result_when_the_sandbox_fails():
    """A failing sandbox rises, teardown still gets a ToolResult with the error."""
    tool = _SeeingTool.__new__(_SeeingTool)
    handler = TokeoAi.__new__(TokeoAi)
    with pytest.raises(TokeoAiError, match='timed out'):
        handler._exec_lifecycle(tool, _BrokenSandbox(), {'path': 'x'}, TokeoAiContext(messages=[]))
    assert isinstance(tool.seen, ToolResult)
    assert tool.seen.value is None
    assert tool.seen.state.exception == 'TokeoAiError: timed out after 1s'
