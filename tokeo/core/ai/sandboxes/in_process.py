"""
The in-process sandbox: zero isolation, the lean default.

It runs a tool call directly in the application process -- exactly what the
loop did before the sandbox seam existed, so wrapping a call in this sandbox
changes nothing observable. With ```tools: _all``` placed last in an agent's
sandbox chain it is the opt-in catch-all that lets the remaining tools run in
process; its absence from a chain is the deny-by-default.
"""

from tokeo.core.ai import TokeoAiSandbox


class TokeoAiInProcessSandbox(TokeoAiSandbox):
    """
    Run a tool call in the application process, with no isolation.

    The honest baseline: full access to the running app, no overhead, no
    containment. Caps in ```options``` are meaningless here and are ignored.
    """

    def exec(self, tool, arguments):
        """
        Call the tool directly and return its result.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool to run
        - **arguments** (dict): The parsed call arguments

        ### Returns

        - **ToolResult | str**: Whatever the tool returns, unchanged

        """
        # WHY 1:1: this is the literal pre-sandbox behaviour, kept identical so
        # the seam is a pure refactor for the default path
        return tool.exec(**(arguments or {}))
