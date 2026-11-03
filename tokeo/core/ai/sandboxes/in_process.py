"""
The in-process sandbox: zero isolation.

It runs a tool call directly in the application process, the same place the
loop ran tools before the sandbox seam existed. It isolates nothing, so it
names only trusted, quick tools in its ```tools```; a tool no sandbox of the
chain names is denied.
"""

import io
import sys
import threading
import contextvars

from tokeo.core.ai import TokeoAiSandbox, ToolResult, ToolStates
from tokeo.core.ai.tool import create_tool_result

# the buffers of the call running in this thread or task, (stdout, stderr), or
# None outside a call
_call_buffers = contextvars.ContextVar('tokeo_ai_in_process_buffers', default=None)
_install_lock = threading.Lock()


class _CallStream:
    # stands in for sys.stdout or sys.stderr once: a write of a call running
    # in process goes into the buffer of that call, every other write into the
    # stream it stands in for. the call is told apart by its context, so calls
    # in parallel threads or asyncio tasks never mix their output

    def __init__(self, stream, index):
        self._stream = stream
        self._index = index

    def _target(self):
        buffers = _call_buffers.get()
        return buffers[self._index] if buffers is not None else self._stream

    def write(self, text):
        return self._target().write(text)

    def flush(self):
        return self._target().flush()

    def __getattr__(self, name):
        return getattr(self._target(), name)


class TokeoAiInProcessSandbox(TokeoAiSandbox):
    """
    Run a tool call in the application process, with no isolation.

    The honest baseline: full access to the running app, no overhead, no
    containment. It takes no options; any key under ```options``` is a lint
    error.
    """

    def exec(self, tool, arguments):
        """
        Call the tool directly and return a ToolResult.

        This is the innermost layer around the tool, so a value or an exception
        here is the tool's own, not the sandbox machinery's. The tool's outcome
        maps to a ToolResult as follows:

        - a finished ToolResult (the tool called ```create_tool_result```) is
            passed through unchanged
        - a plain value is wrapped into a ToolResult
        - a bare ```None``` (an explicit ```return None``` or no return) carries
            no value, so ```value``` stays ```None```; a tool that means the
            json null as its result returns ```create_tool_result(None)```
        - a raised exception is caught and recorded as ```type: message``` in
            ```state.exception```, with no value

        The tool's stdout and stderr are captured onto ```state.stdout``` and
        ```state.stderr``` (only where the tool did not set them itself), so the
        run states match what a process-boundary sandbox reports. Each call
        keeps its own output, also when calls run in parallel threads or asyncio
        tasks; a thread the tool starts itself may write to the real streams.

        Only the tool call is guarded: an exception from the sandbox machinery
        (not from ```tool.exec```) is not caught here and reaches the loop.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool to run
        - **arguments** (dict): The parsed call arguments

        ### Returns

        - **ToolResult**: The tool's finished result, the wrapped value, an
            empty-value result for ```None```, or a result carrying the caught
            exception in its state

        """
        # capture the tool's own stdout/stderr while it runs, so a print() is
        # recorded on the result rather than only hitting the app console; the
        # same run states a process-boundary sandbox reports, for consistency.
        # the buffers belong to this call only, also when calls run in parallel,
        # and hold output a tool emitted before raising
        self._stand_in_streams()
        out_buf, err_buf = io.StringIO(), io.StringIO()
        token = _call_buffers.set((out_buf, err_buf))
        try:
            # the raw call: in process the real object is right here, with no
            # transport between the tool and this frame
            output = tool.exec(**(arguments or {}))
        except Exception as err:
            # a throw from tool.exec is the tool's own, so it becomes a result
            # carrying the exception rather than propagating to the loop
            output = ToolResult(value=None, state=ToolStates(exception=f'{type(err).__name__}: {err}'))
        finally:
            _call_buffers.reset(token)
        # a bare None carries no value; a finished ToolResult passes through; any
        # other value is wrapped, so the loop always reads a ToolResult
        if output is None:
            output = ToolResult(value=None)
        elif not isinstance(output, ToolResult):
            output = create_tool_result(output)
        # fold the captured streams into the run states, but only where the tool
        # left them unset; a stdout/stderr the tool set on purpose is its own
        # note and wins; an empty buffer stays None
        if output.state.stdout is None and out_buf.getvalue():
            output.state.stdout = out_buf.getvalue()
        if output.state.stderr is None and err_buf.getvalue():
            output.state.stderr = err_buf.getvalue()
        return output

    def _stand_in_streams(self):
        # put the stand-ins in place of sys.stdout and sys.stderr, once; again
        # only when other code replaced them since (e.g. a capture of a test)
        with _install_lock:
            if not isinstance(sys.stdout, _CallStream):
                sys.stdout = _CallStream(sys.stdout, 0)
            if not isinstance(sys.stderr, _CallStream):
                sys.stderr = _CallStream(sys.stderr, 1)
