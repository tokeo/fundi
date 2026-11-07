"""
The base of the tools that run a Python snippet.

A snippet delivers its answer by its last line or a ```return```; what it
prints goes to stdout and never to the model. A run that printed but delivered
nothing gets a hint instead of an empty answer, so a model that prints its
result learns the rule from the answer, and the round counts as one without a
result for ```max_loops```.
"""

import dataclasses

from tokeo.core.ai import TokeoAiTool, ToolResult


class TokeoAiPythonExecTool(TokeoAiTool):
    """
    A tool that runs a Python snippet, with the hint for a run without result.

    Derive the snippet tools from it; ```after``` turns a run that printed
    output but delivered no value into a result that carries the hint as its
    error, the printed output stays in the state for the trace.
    """

    #: The hint the model gets for a run that printed but delivered nothing.
    NO_RESULT_HINT = 'ran ok, printed output but set no result: put your answer in the last line'

    def after(self, turndata, arguments, result, track):
        """
        Give a run that printed output but delivered no value the hint.

        ### Args

        - **turndata** (TokeoAiTurndata): The run's shared data area
        - **arguments** (dict): The arguments as reshaped by ```before```
        - **result** (ToolResult): The outcome of the run
        - **track** (func): Call it with a message or data to note something
            on the trace

        ### Returns

        - **ToolResult|None**: The result with the hint as error, or
            ```None``` to keep the result as it is

        """
        if result.value is None and result.state.stdout:
            return ToolResult(value=None, state=dataclasses.replace(result.state, error=self.NO_RESULT_HINT))
        return None
