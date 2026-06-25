"""
Generic, tool-agnostic sandbox job runner.

A subprocess sandbox (and later a docker one) runs a tool call in a fresh
interpreter through this module: it is started as ```python -m
tokeo.core.ai.sandboxes.runner```, reads a single JSON job from stdin, applies the
resource caps BEFORE importing the tool, runs the tool, and writes the
```ToolResult``` back as JSON on stdout. It never reaches back into the parent;
the only contract across the boundary is JSON in and JSON out.

The job has the shape::

    {
      "tool": "dotted.path.To.ToolClass",
      "arguments": { ... },          # the parsed call arguments
      "options": { ... },            # the tool item's options (Meta overrides)
      "caps": { "memory_mb": 256 }   # caps to enforce in this process
    }

The reply is the JSON-encoded ```ToolResult``` on success::

    {
      "value": { "as_str": "...", "as_json": "..." } | null,
      "state": { "incomplete": bool, "stdout": ..., "stderr": ...,
                 "exception": "type: message" | null }
    }

Only the two string views of a value cross; ```as_data``` is left out, since a
raw object (a datetime, a set) need not be JSON-able. The parent rebuilds
```as_data``` from ```as_json```, so a value's structured form survives the
boundary as its encoded JSON shape.

A tool that raised is a normal reply too, carrying its message in
```state.exception``` with a null ```value``` -- the tool throwing is the tool's
own outcome, not a sandbox failure. Only a failure of the sandbox machinery
itself (a bad job, an unenforceable cap, an unimportable tool) becomes a
```{"error": "..."}``` reply with a non-zero exit, a short message and never a
traceback, so nothing leaks across the boundary.

### Notes

: The worker builds the tool with ```app=None```. A tool that needs an app
    builds it in its own ```__init__``` (the uniformity rule); the live parent
    app is not available in a child process and must not be relied on.
"""

import io
import sys
import json
import importlib
import resource
import contextlib


def _set_caps(caps):
    # apply what a child process can truly enforce on itself BEFORE the tool
    # is imported, so even import-time work is bounded. the wall-clock timeout
    # is the parent's job (it kills the child), not something the child sets
    memory_mb = (caps or {}).get('memory_mb')
    if not memory_mb:
        return
    limit = int(memory_mb) * 1024 * 1024
    # WHY two mechanisms and retries: linux sets rlimits as asked and
    # enforces them on future allocations. current macos (xnu) delegates
    # RLIMIT_AS/RLIMIT_DATA to mach vm and rejects any limit BELOW the
    # process's already-mapped virtual size with EINVAL (python surfaces
    # that as "current limit exceeds maximum limit"); on apple silicon a
    # fresh interpreter maps gigabytes (dyld shared cache, malloc zones),
    # so realistic caps are refused there. a configured cap is enforced
    # through whatever the platform accepts -- or the call errors below,
    # never a sham setting
    for name in ('RLIMIT_AS', 'RLIMIT_DATA'):
        res = getattr(resource, name, None)
        if res is None:
            continue
        try:
            _, hard = resource.getrlimit(res)
        except (ValueError, OSError):
            continue
        pinned = limit if hard == resource.RLIM_INFINITY else min(limit, hard)
        # WHY two attempts: enforcement happens at the SOFT limit (the kernel
        # fails allocations there); the hard limit is only the ceiling a
        # process may raise its own soft limit back to. pinning both hardens
        # against self-raising, but macos often refuses any change to the
        # hard limit (EINVAL) -- so fall back to soft-only, which is still
        # full enforcement, just not hardened (and we promise no jail)
        for soft_hard in ((pinned, pinned), (pinned, hard)):
            try:
                resource.setrlimit(res, soft_hard)
                return
            except (ValueError, OSError):
                continue
    # WHY error out: a configured cap is a promise. if no mechanism on this
    # platform can keep it, a sham setting that silently runs uncapped would
    # lie to the config -- fail the call with a clear reason instead
    raise RuntimeError('memory cap (memory_mb) is not enforceable on this platform')


def _load_tool(dotted, options):
    # resolve "module.path.Class" to the class exactly as the handler does
    # (rpartition on the last dot), then build it with no app (a child has
    # none) and the item's options as the cement Meta overrides
    module_path, _, attr = dotted.rpartition('.')
    cls = getattr(importlib.import_module(module_path), attr)
    tool = cls(None, **(options or {}))
    tool._setup(None)
    return tool


def _encode_result(output):
    # reduce a ToolResult to the JSON-able reply that crosses the boundary; the
    # same shape the parent rebuilds a ToolResult from. a value crosses as its
    # two string views, or null when the tool delivered nothing -- as_data is
    # deliberately NOT sent: a raw object (a datetime, a set) need not be
    # JSON-able, so the parent reconstructs as_data from as_json instead
    value = output.value
    if value is None:
        value_doc = None
    else:
        value_doc = dict(as_str=value.as_str, as_json=value.as_json)
    state = output.state
    state_doc = dict(
        incomplete=state.incomplete,
        stdout=state.stdout,
        stderr=state.stderr,
        exception=state.exception,
    )
    return dict(value=value_doc, state=state_doc)


def main():
    """
    Read one job from stdin, run the tool, write the result to stdout.

    A failure of the sandbox machinery (a bad job, an unenforceable cap, an
    unimportable tool) becomes a clean ```{"error": ...}``` reply with a
    non-zero exit. A tool that itself raises is not a machinery failure: it is
    caught around its own call and returned as a normal ```ToolResult``` reply
    carrying the message in ```state.exception```.
    """
    # imports from inside, so an import failure here is still a machinery error
    # and crosses as {"error": ...}, not as a tool result
    from tokeo.core.ai.data import ToolResult, ToolStates
    from tokeo.core.ai.tool import create_tool_result

    try:
        job = json.loads(sys.stdin.read() or '{}')
        _set_caps(job.get('caps'))
        tool = _load_tool(job['tool'], job.get('options'))
    except Exception as err:
        # machinery failure (bad job, unenforceable cap, unimportable tool):
        # a short message crosses back as an error, never a traceback
        sys.stdout.write(json.dumps(dict(error=f'{type(err).__name__}: {err}')))
        return 1
    # capture the tool's own stdout/stderr while it runs: a print() must not
    # reach the real stdout, where it would corrupt the single JSON reply the
    # parent reads. the buffers also hold output a tool emitted before raising
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            output = tool.exec(**(job.get('arguments') or {}))
    except Exception as err:
        # the tool itself raised: its own outcome, returned as a result with no
        # value and the message in state.exception (the same split as in process)
        output = ToolResult(value=None, state=ToolStates(exception=f'{type(err).__name__}: {err}'))
    # a finished ToolResult passes through; a bare None carries no value; any
    # other value is wrapped -- so the reply is always a ToolResult
    if output is None:
        output = ToolResult(value=None)
    elif not isinstance(output, ToolResult):
        output = create_tool_result(output)
    # fold the captured streams into the run states, but only where the tool
    # left them unset -- a stdout/stderr the tool set on purpose is its own note
    # and wins; an empty buffer stays None
    if output.state.stdout is None and out_buf.getvalue():
        output.state.stdout = out_buf.getvalue()
    if output.state.stderr is None and err_buf.getvalue():
        output.state.stderr = err_buf.getvalue()
    sys.stdout.write(json.dumps(_encode_result(output)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
