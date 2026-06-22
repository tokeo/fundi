"""
The ```python_untrusted_exec``` tool: run model-generated Python in isolation.

This is the CodeAct pattern for code you must NOT trust. The snippet runs by a
direct guest path that imports nothing from tokeo -- the wasm guest sees only
its standard library and the code itself, never the framework. That keeps the
isolation total: the model-generated code cannot even read tokeo's own modules.

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

: ```wasm_direct_exec = True``` tells the wasm sandbox to run the ```code```
    argument directly in the guest, WITHOUT rebuilding this tool there -- so no
    tokeo mount is needed and the untrusted code stays walled off from the
    framework. The contract with the generated code: assign the model-facing
    answer to a variable named ```result```; it is coerced to text. The same
    ```exec``` body also runs in process (for tests or a trusted agent that
    deliberately chose no sandbox), so the tool is self-contained either way.
"""

from tokeo.core.ai import TokeoAiTool, ToolResult


class TokeoAiPythonUntrustedExecTool(TokeoAiTool):
    """
    Execute UNTRUSTED model-generated Python in isolation, returning ```result```.

    DANGER: runs arbitrary code -- only ever behind the wasm sandbox or a
    hardened, disposable docker container. The wasm path runs the code directly
    in the guest with no tokeo import, so the framework stays invisible to it.
    """

    # the wasm sandbox runs the code argument directly in the guest instead of
    # rebuilding this tool there: no tokeo mount, total isolation for untrusted
    # code
    wasm_direct_exec = True

    class Meta:
        """Tool meta-data sent to the model."""

        description = (
            'Execute a short Python snippet to compute an answer. '
            'Assign the final value to a variable named `result`. '
            'No network, no file access, only the Python standard library.'
        )

        parameters = {
            'type': 'object',
            'properties': {
                'code': {
                    'type': 'string',
                    'description': 'Python source to run; set `result` to the answer.',
                },
            },
            'required': ['code'],
        }

        # no configurable settings of its own; empty per the config_defaults rule
        config_defaults = {}

    def exec(self, **arguments):
        """
        Compile and run the snippet, returning its ```result``` as text.

        ### Args

        - **code** (str): The Python source; it should assign ```result```

        ### Returns

        - **ToolResult**: The string form of the snippet's ```result``` (empty
            when the snippet sets nothing), with the raw value kept in ```data```

        """
        return _run_snippet(arguments.get('code') or '')


def _run_snippet(code):
    # a dedicated namespace: the snippet reads and writes here, the answer is
    # whatever it bound to ```result```. shared by the in-process path and the
    # guest entry (which inlines the same contract)
    namespace = {}
    compiled = compile(code, '<python_exec>', 'exec')
    exec(compiled, namespace)
    result = namespace.get('result')
    text = '' if result is None else str(result)
    return ToolResult(text=text, data=result if _json_able(result) else None)


def _json_able(value):
    import json

    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False
