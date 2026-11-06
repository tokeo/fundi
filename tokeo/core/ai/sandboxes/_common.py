"""
Helpers shared by the sandbox implementations.

The subprocess, docker and wasm sandboxes rebuild a tool in a fresh
interpreter, build a scrubbed environment for it, and the first two read the
reply of the generic runner, so these helpers live here rather than in either
sandbox; the sandboxes are siblings and should not import from each other.
"""

import io
import os
import sys
import json

from tokeo.core.ai import TokeoAiError, ToolResult, ToolValue, ToolStates


def _get_importable_path(cls, what):
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


def result_from_reply(stdout, stderr, class_path, sandbox_type):
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
    - **class_path** (str): The tool's import path, for the error message
    - **sandbox_type** (str): The sandbox, e.g. ```subprocess```, for the message

    ### Returns

    - **ToolResult**: The tool's result

    ### Raises

    - **TokeoAiError**: On no valid JSON or an ```error``` reply, a failure of
        the sandbox machinery (a bad job, a limit, an unimportable tool)

    """
    try:
        reply = json.loads(stdout or '')
    except json.JSONDecodeError:
        error = (stderr or '').strip()[:200] or 'no output'
        raise TokeoAiError(f'tool {class_path!r} produced no valid result in the {sandbox_type} sandbox: {error}')
    if 'error' in reply:
        raise TokeoAiError(f'tool {class_path!r} failed in the {sandbox_type} sandbox: {reply["error"]}')
    value_dict = reply.get('value')
    value = None
    if value_dict is not None:
        as_json = value_dict.get('as_json', '')
        value = ToolValue(as_str=value_dict.get('as_str', ''), as_json=as_json, as_data=json.loads(as_json) if as_json else None)
    state_dict = reply.get('state') or {}
    state = ToolStates(
        incomplete=state_dict.get('incomplete', False),
        stdout=state_dict.get('stdout'),
        stderr=state_dict.get('stderr'),
        exception=state_dict.get('exception'),
    )
    return ToolResult(value=value, state=state)


def output_bytes(output_mb):
    """
    Turn the option ```output_mb``` into bytes.

    ### Args

    - **output_mb** (int|float): The MB of the option

    ### Returns

    - **int**: The bytes

    """
    return int(output_mb * 1024 * 1024)


class OutputBuffer(io.StringIO):
    """
    A text buffer for the captured stdout or stderr of a tool, with a limit.

    It keeps the first ```max_bytes``` (UTF-8) of what is written and drops the
    rest, so a tool that prints without end cannot fill the memory. A cut
    output ends with a mark that says where it was cut. Every sandbox captures
    a tool's output through it.

    ### Args

    - **max_bytes** (int): The bytes to keep
    - **label** (str): How the limit is named in the mark, e.g. ```1 MB```

    """

    def __init__(self, max_bytes, label):
        super(OutputBuffer, self).__init__()
        #: (bool) Whether output was dropped
        self.cut = False
        self._bytes_left = max_bytes
        self._label = label

    def write(self, text):
        """
        Keep the text while there is room, drop the rest.

        ### Args

        - **text** (str): The text written

        ### Returns

        - **int**: The length of the text, as if it was all written

        """
        if self._bytes_left > 0 and text:
            data = text.encode('utf-8', 'replace')
            if len(data) > self._bytes_left:
                data = data[: self._bytes_left]
                self.cut = True
            self._bytes_left -= len(data)
            super(OutputBuffer, self).write(data.decode('utf-8', 'ignore'))
        elif text:
            self.cut = True
        return len(text)

    def getvalue(self):
        """
        The kept output, with the mark when output was dropped.

        ### Returns

        - **str**: The output

        """
        value = super(OutputBuffer, self).getvalue()
        return f'{value}\n[... cut after {self._label}]' if self.cut else value


def reply_bytes(max_bytes):
    """
    The bytes the host reads at most of a raw reply, for an output limit.

    A reply frames up to four texts of ```max_bytes``` each (the two views of
    the value, stdout and stderr), JSON escaping can double them, and the frame
    itself is small: eight times the limit and 64 KiB.

    ### Args

    - **max_bytes** (int): The output limit in bytes

    ### Returns

    - **int**: The bytes of a raw reply

    """
    return 8 * max_bytes + 64 * 1024


def read_reply(file, max_bytes, label, class_path, sandbox_type):
    """
    Read the raw reply of a tool run from a file, at most ```reply_bytes```.

    The host reads only so much, so a reply that grows without end cannot fill
    its memory. A larger reply is an error, a cut reply is no value.

    ### Args

    - **file**: The file in binary mode, at its start
    - **max_bytes** (int): The output limit in bytes
    - **label** (str): How the limit is named in the message, e.g. ```1 MB```
    - **class_path** (str): The tool's import path, for the message
    - **sandbox_type** (str): The sandbox, e.g. ```subprocess```, for the message

    ### Returns

    - **str**: The reply

    ### Raises

    - **TokeoAiError**: On a reply over ```reply_bytes(max_bytes)```

    """
    max_reply_bytes = reply_bytes(max_bytes)
    data = file.read(max_reply_bytes + 1)
    if len(data) > max_reply_bytes:
        raise TokeoAiError(f'tool {class_path!r} delivered more than {label} in the {sandbox_type} sandbox')
    return data.decode('utf-8', 'replace')


def check_output(result, max_bytes, label, class_path, sandbox_type):
    """
    Hold a tool result to the output limit, the same in every sandbox.

    A value view (```as_str``` or ```as_json```) over ```max_bytes``` is an
    error, the value is the result and a cut value would mislead. A captured
    stdout or stderr over it is cut and marked, it is only a hint.

    ### Args

    - **result** (ToolResult): The result of the tool run
    - **max_bytes** (int): The output limit in bytes
    - **label** (str): How the limit is named, e.g. ```1 MB```
    - **class_path** (str): The tool's import path, for the message
    - **sandbox_type** (str): The sandbox, e.g. ```in_process```, for the message

    ### Returns

    - **ToolResult**: The same result, its captured output cut where needed

    ### Raises

    - **TokeoAiError**: On a value view over ```max_bytes```

    """
    value = result.value
    if value is not None:
        for view in (value.as_str, value.as_json):
            if len((view or '').encode('utf-8', 'replace')) > max_bytes:
                raise TokeoAiError(f'tool {class_path!r} delivered more than {label} in the {sandbox_type} sandbox')
    for name in ('stdout', 'stderr'):
        text = getattr(result.state, name)
        if text is not None and len(text.encode('utf-8', 'replace')) > max_bytes:
            buffer = OutputBuffer(max_bytes, label)
            buffer.write(text)
            setattr(result.state, name, buffer.getvalue())
    return result


def read_diagnostics(file, max_bytes):
    """
    Read the stderr of a sandbox mechanism for a message, at most ```max_bytes```.

    ### Args

    - **file**: The file in binary mode, at its start
    - **max_bytes** (int): The bytes to read at most

    ### Returns

    - **str**: The text, cut at ```max_bytes```

    """
    return file.read(max_bytes).decode('utf-8', 'replace')
