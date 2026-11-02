"""
The subprocess sandbox: fault and resource isolation, not a jail.

It runs a tool call in a fresh interpreter through the generic runner
(```python -m tokeo.core.ai.sandboxes.runner```), feeding the job as JSON on
stdin and reading the ```ToolResult``` as JSON on stdout. The child gets its
own interpreter in a process group of its own, a wall-clock timeout (then
SIGKILL), the end of every process it started when the call ends, an enforced
memory cap
(RLIMIT_AS, with RLIMIT_DATA as fallback; current macos rejects caps below
the already-mapped virtual size (gigabytes at interpreter start), so a
realistic cap errors the call there instead of silently running uncapped),
a working directory, and a scrubbed environment. A crashing or run-away tool
is contained from the parent, but this is not a jail against hostile code:
it cannot stop the tool from reading files or reaching the network. Real path
or network isolation needs a container, VM, or WASM backend the user supplies.

### Notes

: The tool is rebuilt in the child from its dotted ```type``` and ```options```
    (carried on the instance by the handler), with ```app=None```; a child has
    no live parent app, and the uniformity rule means a tool that needs an app
    builds it itself. The full ```ToolResult``` (its value views and run states)
    crosses the boundary as JSON; only a sandbox-machinery failure (timeout, a
    runner crash, a non-JSON reply) is raised as a ```TokeoAiError```.
"""

import os
import sys
import json
import signal
import tempfile
import subprocess

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env, result_from_reply


class TokeoAiSubprocessSandbox(TokeoAiSandbox):
    """
    Run a tool call in a fresh interpreter via the generic runner.

    Enforced caps: ```timeout``` (wall-clock, then SIGKILL) and ```memory_mb```
    (the child's address-space rlimit). ```cwd``` steers relative paths
    (advisory). ```env``` is scrubbed and ```${NAME}```-expanded. Path and network
    caps are intentionally absent; they cannot be promised here.
    """

    class Meta:
        """The subprocess mechanism's own settings (its option keys)."""

        # the configurable defaults, as one dict; the item's options overlay
        # this, read at runtime via _config. timeout: wall-clock seconds before
        # the run is killed (None = unbounded). memory_mb: memory cap in MB via
        # rlimit (RLIMIT_AS, fallback RLIMIT_DATA); a refused cap errors the
        # call (current macos rejects caps below the mapped size), no sham caps,
        # None = unbounded. cwd: scratch working directory (steers relative
        # writes, created on demand), advisory only; absolute paths are not
        # blocked. env: environment for the run, scrubbed/empty by default, only
        # the listed keys are set, ${NAME} expands against out -> host env -> ''
        config_defaults = dict(
            timeout=None,
            memory_mb=None,
            cwd=None,
            env=None,
        )

    def exec(self, tool, arguments):
        """
        Run the tool in a runner subprocess and return its result.

        The full ```ToolResult``` is rebuilt from the runner's JSON, including a
        tool that raised; the child catches the tool's own throw and reports it
        in ```state.exception```, so it returns as a result, not as a raise.
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
        dotted = _importable_path(type(tool), 'tool')
        timeout = self._config('timeout')
        memory_mb = self._config('memory_mb')
        job = json.dumps(
            dict(
                tool=dotted,
                arguments=arguments or {},
                options=getattr(tool, '_tokeo_parent_instance_options', {}) or {},
                caps=dict(memory_mb=memory_mb),
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
                raise TokeoAiError(f'tool {dotted!r} timed out after {timeout}s in the subprocess sandbox')
            finally:
                self._end_group(proc)
            out.seek(0)
            err.seek(0)
            proc = subprocess.CompletedProcess(cmd, proc.returncode, out.read().decode(), err.read().decode())
        return result_from_reply(proc.stdout, proc.stderr, dotted, 'subprocess')

    def _end_group(self, proc):
        # kill every process of the call's group; the group may be gone already
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
