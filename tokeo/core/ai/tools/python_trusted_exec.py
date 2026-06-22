"""
The ```python_trusted_exec``` tool: run Python with the target app available.

This is the CodeAct pattern for code you DO trust (your own snippets, a vetted
agent). Unlike the untrusted variant, this tool is rebuilt inside the wasm
guest the normal way, so the guest needs the target app (tokeo or your spiral
project) mounted and importable -- the snippet can then use framework helpers.

### Security

: This tool still runs code, but it is meant for TRUSTED input. Because the
    wasm guest must mount the app to import it, the guest can read that mounted
    code -- acceptable for trusted snippets, NOT for model-generated untrusted
    code. For untrusted code use ```python_untrusted_exec```, which runs in the
    guest without any tokeo mount.

### Notes

: This tool does not set ```wasm_direct_exec```, so the wasm sandbox rebuilds
    it in the guest from its dotted path -- which requires the app on the
    guest's import path (mount it read-only, e.g. ```/app```, and add ```/app```
    to ```env.PYTHONPATH```). The snippet contract is the same: assign the
    answer to ```result```.
"""

from tokeo.core.ai import TokeoAiTool, ToolResult


class TokeoAiPythonTrustedExecTool(TokeoAiTool):
    """
    Execute TRUSTED Python with the target app importable, returning ```result```.

    Rebuilt inside the wasm guest the normal way, so the guest must have the
    app (tokeo/your project) mounted and on PYTHONPATH. For untrusted code use
    python_untrusted_exec instead.
    """

    class Meta:
        """Tool meta-data sent to the model."""

        description = (
            'Execute a short Python snippet with the application available. ' 'Assign the final value to a variable named `result`.'
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
        code = arguments.get('code') or ''
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
