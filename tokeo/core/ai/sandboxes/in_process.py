"""
The in-process sandbox: zero isolation, the lean default.

It runs a tool call directly in the application process -- the same place the
loop ran tools before the sandbox seam existed. With ```tools: _all``` placed
last in an agent's sandbox chain it is the opt-in catch-all that lets the
remaining tools run in process; its absence from a chain is the deny-by-default.
"""

import io
import contextlib

from tokeo.core.ai import TokeoAiSandbox, ToolResult, ToolStates
from tokeo.core.ai.tool import create_tool_result


class TokeoAiInProcessSandbox(TokeoAiSandbox):
    """
    Run a tool call in the application process, with no isolation.

    The honest baseline: full access to the running app, no overhead, no
    containment. Caps in ```options``` are meaningless here and are ignored.
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
        run states match what a process-boundary sandbox reports.

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
        # recorded on the result rather than only hitting the app console -- the
        # same run states a process-boundary sandbox reports, for consistency.
        # the buffers also hold output a tool emitted before raising
        out_buf, err_buf = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                # the raw call: in process the real object is right here, with
                # no transport between the tool and this frame
                output = tool.exec(**(arguments or {}))
        except Exception as err:
            # a throw from tool.exec is the tool's own, so it becomes a result
            # carrying the exception rather than propagating to the loop
            output = ToolResult(value=None, state=ToolStates(exception=f'{type(err).__name__}: {err}'))
        # a bare None carries no value; a finished ToolResult passes through; any
        # other value is wrapped -- so the loop always reads a ToolResult
        if output is None:
            output = ToolResult(value=None)
        elif not isinstance(output, ToolResult):
            output = create_tool_result(output)
        # fold the captured streams into the run states, but only where the tool
        # left them unset -- a stdout/stderr the tool set on purpose is its own
        # note and wins; an empty buffer stays None
        if output.state.stdout is None and out_buf.getvalue():
            output.state.stdout = out_buf.getvalue()
        if output.state.stderr is None and err_buf.getvalue():
            output.state.stderr = err_buf.getvalue()
        return output
