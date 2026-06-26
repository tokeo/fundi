"""
The ```python_untrusted_exec``` tool: run model-generated Python in isolation.

This is the CodeAct pattern for code you must NOT trust. The snippet runs by the
exec-pysnippet guest path that imports nothing from tokeo except the pact
contract -- the wasm guest sees its standard library, the contract, and the code
itself, never the framework. That keeps the isolation total: the model-generated
code cannot read tokeo's own modules.

### Security

: !!! DANGER !!! This tool executes arbitrary, model-generated code. It MUST
    only be configured to run inside the ```wasm``` sandbox (no network, only
    the stdlib, hard memory cap) or a hardened, throwaway ```docker```
    container. Running it ```in_process``` or in the plain ```subprocess```
    sandbox gives generated code full host access -- file reads, network,
    process spawn -- which a prompt-injection can turn into data exfiltration
    or worse. The sandbox is not optional for this tool; it is the only thing
    that makes it safe.

### Notes

: ```wasm_exec_pysnippet = True``` tells the wasm sandbox to run the ```code```
    argument via run_snippet in the guest, WITHOUT rebuilding this tool there --
    so only the pact contract is mounted and the untrusted code stays walled off
    from the framework. The snippet delivers its answer by ending on an expression
    (the jupyter form) or by a ```return```; the same delivery runs in process
    (for tests or a trusted agent that deliberately chose no sandbox), so the tool
    is self-contained either way.
"""

from tokeo.core.ai import TokeoAiTool
from tokeo.pact.ai.pysnippet import run_snippet


class TokeoAiPythonUntrustedExecTool(TokeoAiTool):
    """
    Execute UNTRUSTED model-generated Python in isolation, returning its value.

    DANGER: runs arbitrary code -- only ever behind the wasm sandbox or a
    hardened, disposable docker container. The wasm path runs the code via
    run_snippet in the guest with only the pact contract mounted, so the
    framework stays invisible to it.
    """

    # the wasm sandbox runs the code argument via run_snippet in the guest
    # instead of rebuilding this tool there: only the pact contract is mounted,
    # total isolation for untrusted code
    wasm_exec_pysnippet = True

    class Meta:
        """Tool meta-data sent to the model."""

        description = (
            'Execute a short Python snippet to compute an answer. '
            'Deliver the value as the last line (an expression) or with a `return`. '
            'No network, no file access, only the Python standard library.'
        )

        parameters = {
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': 'Python source to run; end on the value or `return` it.',
                },
            },
            'required': ['code'],
        }

        # no configurable settings of its own; empty per the config_defaults rule
        config_defaults = {}

    def exec(self, **arguments):
        """
        Run the snippet and return the value it delivered.

        The tool hands back the raw value (or ```None``` when the snippet
        delivered none); the sandbox layer wraps it into a ```ToolResult```.

        ### Args

        - **code** (str): The Python source; it delivers by a last expression
            or a ```return```

        ### Returns

        - **object | None**: The value the snippet delivered, or ```None```

        """
        return run_snippet(arguments.get('code') or '')
