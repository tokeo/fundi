"""
The subprocess sandbox: crash and resource isolation, not a jail.

It runs a tool call in a fresh interpreter through the generic runner
(```python -m tokeo.core.ai.sandboxes.runner```), feeding the job as JSON on
stdin and reading the ```ToolResult``` as JSON on stdout. The child gets its
own interpreter in a process group of its own, a wall-clock timeout (then
SIGKILL), the end of every process it started when the call ends, an enforced
memory limit
(RLIMIT_AS, with RLIMIT_DATA as fallback; current macos rejects limits below
the already-mapped virtual size (gigabytes at interpreter start), so a
realistic limit errors the call there instead of silently running unlimited),
a working directory, and a scrubbed environment. A crashing or run-away tool
is contained from the parent, but this is not a jail against hostile code:
it cannot stop the tool from reading files or reaching the network. Real path
or network isolation needs the docker or the wasm sandbox.

### Notes

: The tool is rebuilt in the child from the import path of its class and its
    ```options``` (carried on the instance by the handler), with ```app=None```;
    a child has no live parent app, and the uniformity rule means a tool that
    needs an app builds it itself. The full ```ToolResult``` (its value views
    and run states) crosses the boundary as JSON; only a sandbox-machinery
    failure (timeout, a runner crash, a non-JSON reply) is raised as a
    ```TokeoAiError```.
"""

import os
import sys
import json
import signal
import tempfile
import subprocess

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError
from tokeo.core.ai.sandbox import SANDBOX_MAX_OUTPUT_MB
from tokeo.core.ai.sandboxes._common import (
    _get_importable_path,
    expand_env,
    result_from_reply,
    output_bytes,
    read_reply,
    read_diagnostics,
    check_output,
)


class TokeoAiSubprocessSandbox(TokeoAiSandbox):
    """
    Run a tool call in a fresh interpreter via the generic runner.

    Enforced limits: ```timeout``` (wall-clock, then SIGKILL), ```max_memory_mb```
    (the child's address-space rlimit) and ```max_output_mb``` (the captured
    output is cut, a larger reply is an error). ```cwd``` steers relative
    paths (advisory). ```env``` is scrubbed and ```${NAME}```-expanded. Path,
    network and disk limits are intentionally absent; they cannot be promised
    here.
    """

    class Meta:
        """Subprocess sandbox meta-data."""

        # the configurable defaults; the item's options overlay them, read at
        # runtime via _config
        config_defaults = dict(
            # wall-clock seconds, then the process group is killed; None = no limit
            timeout=None,
            # MB of memory via rlimit (RLIMIT_AS, else RLIMIT_DATA); a refused
            # limit errors the call (macos rejects small limits); None = no limit
            max_memory_mb=None,
            # working dir, created on demand; steers relative paths, no wall
            cwd=None,
            # the only variables of the run, ${NAME} expands; empty by default
            env=None,
            # MB of stdout and stderr kept, and the largest reply read
            max_output_mb=SANDBOX_MAX_OUTPUT_MB,
        )

    def exec(self, tool, arguments):
        """
        Run the tool in a runner subprocess and return its result.

        The full ```ToolResult``` is rebuilt from the runner's JSON, including a
        tool that raised; the child catches the tool's own throw and reports it
        in ```state.error```, so it returns as a result, not as a raise.
        Only a sandbox-machinery failure (timeout, a runner crash, a non-JSON or
        ```error``` reply) is raised here for the loop to catch.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool; the child rebuild
            imports by the canonical path of its class (registry aliases
            work too) and reuses its ```_tokeo_parent_instance_options```
        - **arguments** (dict): The parsed, JSON-able call arguments

        ### Returns

        - **ToolResult**: The tool's result, rebuilt from the runner's JSON

        ### Raises

        - **TokeoAiError**: On timeout, a non-JSON reply, or a runner error

        """
        # WHY canonical path: the child imports by module path. deriving it
        # from the loaded class (not the config string) lets a registry
        # alias cross the boundary too; the parent already resolved it
        class_path = _get_importable_path(type(tool), 'tool')
        timeout = self._config('timeout')
        max_memory_mb = self._config('max_memory_mb')
        max_output_mb = self._config('max_output_mb')
        job = json.dumps(
            dict(
                tool=class_path,
                arguments=arguments or {},
                options=getattr(tool, '_tokeo_parent_instance_options', {}) or {},
                limits=dict(max_memory_mb=max_memory_mb),
                max_output_mb=max_output_mb,
            )
        )
        # a fresh interpreter running the generic runner module; stdin carries
        # the job, stdout the reply, both as a single JSON document
        cmd = [sys.executable, '-m', 'tokeo.core.ai.sandboxes.runner']
        env = expand_env(self._config('env'))
        # WHY: options.env shapes the TOOL's environment and is scrubbed, but
        # the runner interpreter must still import tokeo and the tool module.
        # carry the parent's import path in PYTHONPATH so the child can boot
        # regardless of what the user listed; this is sandbox mechanics, not
        # the tool's environment. a PYTHONPATH the user set in env keeps the
        # lead; the parent's entries are appended (absolute, so a changed cwd
        # does not break a relative path)
        parent_paths = [os.path.abspath(p) for p in sys.path if p]
        user_paths = [p for p in (env.get('PYTHONPATH') or '').split(os.pathsep) if p]
        env['PYTHONPATH'] = os.pathsep.join(user_paths + parent_paths)
        cwd = self._config('cwd') or None
        if cwd:
            # WHY: cwd is the sandbox scratch dir; create it on demand so a
            # not-yet-existing path is a usable working dir, not a crash
            os.makedirs(cwd, exist_ok=True)
        # WHY a group of its own: a tool may start processes itself; the call
        # ends with all of them, on a timeout and after a normal end alike, so
        # nothing it started outlives it. stdout and stderr go to files, not
        # pipes: a process the tool started may hold them open, the call ends
        # when the runner ends, not when every holder closed them
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
            try:
                # wall-clock is the parent's to enforce, a hung tool cannot wedge us
                proc.communicate(job.encode(), timeout=timeout)
            except subprocess.TimeoutExpired:
                self._end_group(proc)
                proc.wait()
                raise TokeoAiError(f'tool {class_path!r} timed out after {timeout}s in the subprocess sandbox')
            finally:
                self._end_group(proc)
            # read at most max_output_mb, so a reply without end cannot fill memory
            out.seek(0)
            err.seek(0)
            stdout = read_reply(out, output_bytes(max_output_mb), f'{max_output_mb:g} MB', class_path, 'subprocess')
            stderr = read_diagnostics(err, output_bytes(max_output_mb))
        result = result_from_reply(stdout, stderr, class_path, 'subprocess')
        return check_output(result, output_bytes(max_output_mb), f'{max_output_mb:g} MB', class_path, 'subprocess')

    def _end_group(self, proc):
        # kill every process of the call's group; the group may be gone already
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
