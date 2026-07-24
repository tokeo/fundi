"""
Tests for the tool lifecycle contracts (prepare, before, after, teardown).

These pin the CONTRACTS only -- that the four optional hooks exist on the tool,
default to no-ops, carry the decided signatures, and that a tool can override
them. Wiring the hooks into the loop (order, error short-circuit) is a separate
concern and lives with the loop tests.
"""

import inspect

from tokeo.core.ai import TokeoAiTool, TokeoAiContext
from tokeo.core.ai.data import ToolResult, ToolStates
from tokeo.ext.ai import TokeoAi


class _BareTool(TokeoAiTool):

    def exec(self, **arguments):
        return 'ok'


class _FramedTool(TokeoAiTool):
    # a tool that uses every hook, to prove they can be overridden
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
    # a tool that only defines exec inherits the four hooks; they do nothing
    tool = _BareTool.__new__(_BareTool)
    td = {}

    def noop(message):
        return None

    assert tool.prepare(td, {'a': 1}, noop) is None
    assert tool.before(td, {'a': 1}, noop) is None
    assert tool.after(td, {'a': 1}, ToolResult(value='x'), noop) is None
    assert tool.teardown(td, {'a': 1}, ToolResult(value='x'), noop) is None
    assert td == {}


def test_hook_signatures_match_the_contract():
    def sig(m):
        return str(inspect.signature(getattr(TokeoAiTool, m)))

    assert sig('prepare') == '(self, turndata, arguments, track)'
    assert sig('before') == '(self, turndata, arguments, track)'
    assert sig('after') == '(self, turndata, arguments, result, track)'
    assert sig('teardown') == '(self, turndata, arguments, result, track)'


def test_before_may_reshape_and_return_a_dict():
    tool = _FramedTool.__new__(_FramedTool)
    reshaped = tool.before({}, {'path': 'x'}, lambda m: None)
    assert reshaped == {'path': 'safe/x'}


def test_after_may_reshape_the_result():
    tool = _FramedTool.__new__(_FramedTool)
    out = tool.after({}, {'path': 'safe/x'}, ToolResult(value='ran safe/x'), lambda m: None)
    assert out.value == 'ran safe/x (checked safe/x)'


def test_prepare_and_teardown_write_turndata():
    tool = _FramedTool.__new__(_FramedTool)
    td = {}
    tool.prepare(td, {'path': 'x'}, lambda m: None)
    tool.teardown(td, {'path': 'x'}, ToolResult(value='ok'), lambda m: None)
    assert td == {'prepared': True, 'torn_down': True}


def test_teardown_reads_exception_from_result():
    # on a failed call the result carries the exception; teardown can read it
    tool = _BareTool.__new__(_BareTool)
    failed = ToolResult(value=None, state=ToolStates(exception='ValueError: nope'))
    # the default teardown is a no-op, but the contract is that result is always
    # a ToolResult and the exception lives in result.state.exception
    assert tool.teardown({}, {}, failed, lambda message: None) is None
    assert failed.state.exception == 'ValueError: nope'


# ---- the lifecycle as the loop runs it --------------------------------------


class _Sandbox:
    # mirrors a real sandbox: a tool's own throw comes back as a result, only
    # a machinery failure rises out of exec
    def exec(self, tool, arguments):
        try:
            return tool.exec(**arguments)
        except Exception as err:
            return ToolResult(value=None, state=ToolStates(exception=f'{type(err).__name__}: {err}'))


class _LoggingTool(TokeoAiTool):
    # records which hooks ran, and can be told which one should raise
    def __init__(self, fail=None):
        self.fail = fail
        self.ran = []

    def _step(self, name):
        self.ran.append(name)
        if self.fail == name:
            raise ValueError(f'boom-{name}')

    def prepare(self, turndata, arguments, track):
        track('prepared')
        self._step('prepare')

    def before(self, turndata, arguments, track):
        self._step('before')
        return {**arguments, 'path': 'safe/' + arguments['path']}

    def exec(self, path):
        self._step('exec')
        return ToolResult(value=f'ran {path}')

    def after(self, turndata, arguments, result, track):
        self._step('after')

    def teardown(self, turndata, arguments, result, track):
        self._step('teardown')


def _run(tool, ctx=None):
    handler = TokeoAi.__new__(TokeoAi)
    return handler._exec_lifecycle(tool, _Sandbox(), {'path': 'x'}, ctx or TokeoAiContext(messages=[]))


def test_happy_path_runs_all_hooks_and_before_reaches_exec():
    tool = _LoggingTool()
    result = _run(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'after', 'teardown']
    # the arguments before reshaped are the ones exec got
    assert result.value == 'ran safe/x'


def test_prepare_raising_skips_everything_including_teardown():
    tool = _LoggingTool(fail='prepare')
    result = _run(tool)
    assert tool.ran == ['prepare']
    assert 'boom-prepare' in result.state.exception


def test_before_raising_short_circuits_to_teardown():
    tool = _LoggingTool(fail='before')
    result = _run(tool)
    assert tool.ran == ['prepare', 'before', 'teardown']
    assert 'boom-before' in result.state.exception


def test_exec_raising_still_runs_teardown():
    tool = _LoggingTool(fail='exec')
    result = _run(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'teardown']
    assert 'boom-exec' in result.state.exception


def test_after_raising_becomes_the_result():
    tool = _LoggingTool(fail='after')
    result = _run(tool)
    assert tool.ran == ['prepare', 'before', 'exec', 'after', 'teardown']
    assert 'boom-after' in result.state.exception


def test_teardown_raising_is_noted_and_keeps_the_result():
    ctx = TokeoAiContext(messages=[])
    tool = _LoggingTool(fail='teardown')
    result = _run(tool, ctx)
    # the settled result survives a failing teardown
    assert result.value == 'ran safe/x'
    assert result.state.exception is None
    notes = [step.object.content for step in ctx.trace if hasattr(step.object, 'content')]
    assert any('teardown failed' in note for note in notes)


def test_track_binds_origin_and_stage():
    ctx = TokeoAiContext(messages=[])
    tool = _LoggingTool()
    _run(tool, ctx)
    step = next(s for s in ctx.trace if getattr(s.object, 'content', None) == 'prepared')
    assert step.origin is tool
    assert step.stage == 'prepare'
