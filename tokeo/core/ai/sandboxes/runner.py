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

The reply is ```{"text": "...", "data": ...}``` on success, or ```{"error":
"..."}``` on failure -- a short message, never a traceback, so nothing leaks
across the boundary.

### Notes

: The worker builds the tool with ```app=None```. A tool that needs an app
    builds it in its own ```__init__``` (the uniformity rule); the live parent
    app is not available in a child process and must not be relied on.
"""

import sys
import json
import importlib
import resource


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


def main():
    """
    Read one job from stdin, run the tool, write the result to stdout.

    Wraps everything so any failure becomes a clean ```{"error": ...}``` reply
    with a non-zero exit, instead of a traceback crossing the boundary.
    """
    try:
        job = json.loads(sys.stdin.read() or '{}')
        _set_caps(job.get('caps'))
        tool = _load_tool(job['tool'], job.get('options'))
        output = tool.exec(**(job.get('arguments') or {}))
        # a tool may return a ToolResult or a plain string; reduce both to the
        # JSON-able text/data that may cross the boundary
        if hasattr(output, 'text'):
            reply = dict(text=output.text, data=getattr(output, 'data', None))
        else:
            reply = dict(text=str(output), data=None)
        sys.stdout.write(json.dumps(reply))
        return 0
    except Exception as err:
        # only a short message crosses back, never a traceback
        sys.stdout.write(json.dumps(dict(error=f'{type(err).__name__}: {err}')))
        return 1


if __name__ == '__main__':
    sys.exit(main())
