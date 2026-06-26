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
    guest with only the pact contract mounted.

### Notes

: This tool does not set ```wasm_exec_pysnippet```, so the wasm sandbox rebuilds
    it in the guest from its dotted path -- which requires the app on the
    guest's import path (mount it read-only, e.g. ```/app```, and add ```/app```
    to ```env.PYTHONPATH```). The snippet delivers the same way: end on an
    expression (the jupyter form) or use a ```return```.
"""

from tokeo.core.ai import TokeoAiTool
from tokeo.pact.ai.pysnippet import run_snippet


class TokeoAiPythonTrustedExecTool(TokeoAiTool):
    """
    Execute TRUSTED Python with the target app importable, returning its value.

    Rebuilt inside the wasm guest the normal way, so the guest must have the
    app (tokeo/your project) mounted and on PYTHONPATH. For untrusted code use
    python_untrusted_exec instead.
    """

    class Meta:
        """Tool meta-data sent to the model."""

        description = (
            'Execute a short Python snippet with the application available. '
            'Deliver the value as the last line (an expression) or with a `return`.'
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
