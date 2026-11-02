"""
Helpers shared by the sandbox implementations.

The subprocess, docker and wasm sandboxes rebuild a tool in a fresh
interpreter, build a scrubbed environment for it, and the first two read the
reply of the generic runner, so these helpers live here rather than in either
sandbox; the sandboxes are siblings and should not import from each other.
"""

import os
import sys
import json

from tokeo.core.ai import TokeoAiError, ToolResult, ToolValue, ToolStates


def _importable_path(cls, what):
    # the canonical import path of a loaded class; a sandbox that rebuilds the
    # tool in another interpreter needs it reachable by module path there
    module = cls.__module__
    if module == '__main__':
        # WHY: under ```python -m pkg.mod``` the defining module RUNS as
        # __main__ but stays importable under its real name; PEP 451 keeps
        # that name on the spec. only the name is taken; re-importing in
        # the parent would execute the module twice
        spec = getattr(sys.modules.get('__main__'), '__spec__', None)
        if spec is not None and spec.name:
            module = spec.name
    if module == '__main__' or '.' in cls.__qualname__:
        raise TokeoAiError(
            f'cannot rebuild the {what} in another interpreter: '
            f'{cls.__qualname__!r} is not importable by module path '
            '(defined in a script __main__ or not at module top level)'
        )
    return f'{module}.{cls.__qualname__}'


def expand_env(spec):
    """
    Build a sandbox environment from a spec, expanding ```${NAME}```.

    The result is scrubbed: only the keys listed in ```spec``` are present (the
    host environment is a resolution source, not the sandbox's environment). A
    ```${NAME}``` reference resolves against the keys already built in this pass,
    then the host ```os.environ```, then the empty string: shell-like, with no
    error on an unknown name. A bare ```$``` is literal.

    ### Args

    - **spec** (dict|None): The ```options.env``` mapping, in definition order

    ### Returns

    - **dict**: The expanded, scrubbed environment for the sandboxed run

    """
    out = {}
    for key, value in (spec or {}).items():
        text = '' if value is None else str(value)
        result, i = [], 0
        while i < len(text):
            # only "${NAME}" is special; a bare "$" stays literal
            if text[i] == '$' and i + 1 < len(text) and text[i + 1] == '{':
                end = text.find('}', i + 2)
                if end != -1:
                    name = text[i + 2 : end]  # noqa E203
                    # already-built keys win, then the host env, then ''
                    result.append(out.get(name, os.environ.get(name, '')))
                    i = end + 1
                    continue
            result.append(text[i])
            i += 1
        out[key] = ''.join(result)
    return out


def result_from_reply(stdout, stderr, dotted, where):
    """
    Build the ```ToolResult``` from the JSON reply of the generic runner.

    The runner writes one JSON document to stdout. A null value stays
    ```None``` (the tool delivered nothing), otherwise the two string views
    cross and ```as_data``` is rebuilt from ```as_json```; the run states come
    as recorded. A tool that raised is a normal reply with
    ```state.exception```.

    ### Args

    - **stdout** (str): What the runner wrote to stdout
    - **stderr** (str): What it wrote to stderr, for the error message
    - **dotted** (str): The tool's import path, for the error message
    - **where** (str): The sandbox, e.g. ```subprocess```, for the message

    ### Returns

    - **ToolResult**: The tool's result

    ### Raises

    - **TokeoAiError**: On no valid JSON or an ```error``` reply, a failure of
        the sandbox machinery (a bad job, a cap, an unimportable tool)

    """
    try:
        reply = json.loads(stdout or '')
    except json.JSONDecodeError:
        detail = (stderr or '').strip()[:200] or 'no output'
        raise TokeoAiError(f'tool {dotted!r} produced no valid result in the {where} sandbox: {detail}')
    if 'error' in reply:
        raise TokeoAiError(f'tool {dotted!r} failed in the {where} sandbox: {reply["error"]}')
    value_doc = reply.get('value')
    value = None
    if value_doc is not None:
        as_json = value_doc.get('as_json', '')
        value = ToolValue(as_str=value_doc.get('as_str', ''), as_json=as_json, as_data=json.loads(as_json) if as_json else None)
    state_doc = reply.get('state') or {}
    state = ToolStates(
        incomplete=state_doc.get('incomplete', False),
        stdout=state_doc.get('stdout'),
        stderr=state_doc.get('stderr'),
        exception=state_doc.get('exception'),
    )
    return ToolResult(value=value, state=state)
