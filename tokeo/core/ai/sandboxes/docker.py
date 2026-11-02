# Copyright (c) 2026 Tom (Thomas) Freudenberg <th.freudenberg@gmail.com>
#
# SPDX-License-Identifier: LicenseRef-Tokeo-Fundi-Source-Available-1.0
#
# This file is part of Tokeo-Fundi and is licensed under the
# Tokeo-Fundi Source-Available License 1.0. See the LICENSE.md file in the
# project root for the full license text. Use is subject to its conditions.

"""
The docker sandbox: a tool call in a hardened, throwaway container.

It runs the generic runner (```python3 -m tokeo.core.ai.sandboxes.runner```) in
a container of the configured ```image```, feeding the job as JSON on stdin
and reading the ```ToolResult``` as JSON on stdout, like the subprocess sandbox.
The image must hold tokeo, fundi and the tools of the app; the image of the app
itself is the simplest choice.

Always set, not configurable: ```--rm```, ```--cap-drop ALL```,
```--security-opt no-new-privileges```, never ```--privileged```, and every
mount read-only. Configurable with safe defaults: no network, a read-only root
file system (with a small ```/tmp```), a memory and a process cap, a timeout,
and a user that is not root.

```yaml
sandboxes:
  docked:
    type: docker
    tools: [mathematics]
    options:
      image: myapp:latest
      memory_mb: 256
      timeout: 20
```

### Notes

: ```reuse: false``` (the default) starts a fresh container for every call.
    ```reuse: true``` starts one container on the first call, with the same
    options, runs each call in it by ```exec```, and removes it when the process
    ends; a timeout removes it too, the next call starts a new one.

: ```command``` names the client, ```docker``` or ```podman``` (rootless).
    Whoever may use the docker service is root on the host in effect: never
    mount the docker socket into a container of the app.
"""

import os
import re
import json
import uuid
import atexit
import threading
import subprocess

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env, result_from_reply


class TokeoAiDockerSandbox(TokeoAiSandbox):
    """
    Run a tool call in a hardened container through the generic runner.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Positional arguments for the parent initializer

    ### Keyword Args

    - **kw**: Keyword arguments for the parent initializer

    """

    class Meta:
        """The docker mechanism's own settings (its option keys)."""

        # image: required. command: docker or podman. python: the interpreter
        # in the image. network, read_only, memory_mb, pids, user: hardening
        # with safe defaults. mounts: {guest_path: host_path}, read-only. env:
        # scrubbed, ${NAME} expands. timeout: seconds. reuse: one container
        # for the process instead of one per call
        config_defaults = dict(
            image=None,
            command='docker',
            python='python3',
            network='none',
            read_only=True,
            memory_mb=512,
            pids=128,
            user='65534:65534',
            mounts=None,
            env=None,
            timeout=30,
            reuse=False,
        )

    def __init__(self, app, *args, **kw):
        super(TokeoAiDockerSandbox, self).__init__(app, *args, **kw)
        # the running container with reuse, and the lock to start it once
        self._container = None
        self._lock = threading.Lock()

    def validate_options(self, options):
        """
        Check the values the linter cannot know.

        ### Args

        - **options** (dict): The item's ```options``` block

        ### Returns

        - **list[str]**: An error for a missing image or a root user

        """
        errors = []
        if not (options or {}).get('image'):
            errors.append('image is required')
        if str((options or {}).get('user', '65534:65534')).split(':')[0] in ('root', '0'):
            errors.append('user must not be root')
        return errors

    def exec(self, tool, arguments):
        """
        Run the tool in a container and return its result.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool; the container rebuilds
            it by the import path of its class and its options
        - **arguments** (dict): The parsed, JSON-able call arguments

        ### Returns

        - **ToolResult**: The tool's result, rebuilt from the runner's JSON

        ### Raises

        - **TokeoAiError**: On a missing image, a timeout, or a failed run

        """
        image = self._config('image')
        if not image:
            raise TokeoAiError(f'the docker sandbox {self.config_name!r} needs an image')
        dotted = _importable_path(type(tool), 'tool')
        job = json.dumps(
            dict(
                tool=dotted,
                arguments=arguments or {},
                options=getattr(tool, '_tokeo_parent_instance_options', {}) or {},
                caps={},
            )
        )
        runner = [self._config('python'), '-m', 'tokeo.core.ai.sandboxes.runner']
        if self._config('reuse'):
            with self._lock:
                if self._container is None:
                    name = self._name()
                    self._call(self._run_args(name, detach=True) + [image, 'sleep', 'infinity'], None, dotted)
                    self._container = name
                    atexit.register(self.close)
                name = self._container
            args = [self._config('command'), 'exec', '-i', name] + runner
        else:
            name = self._name()
            args = self._run_args(name, detach=False) + [image] + runner
        try:
            out, err = self._call(args, job, dotted)
        except TokeoAiError:
            # a timed out or broken container goes, reused or not
            self._kill(name)
            with self._lock:
                if self._container == name:
                    self._container = None
            raise
        return result_from_reply(out, err, dotted, 'docker')

    def close(self):
        """Remove the container of ```reuse```, if one is running."""
        with self._lock:
            if self._container is not None:
                self._kill(self._container)
                self._container = None

    def _name(self):
        # a unique container name, so a timeout can remove exactly this one
        label = re.sub(r'[^a-zA-Z0-9_.-]', '-', self.config_name or 'docker')
        return f'tokeo-ai-{label}-{os.getpid()}-{uuid.uuid4().hex[:8]}'

    def _run_args(self, name, detach):
        # the hardening: fixed flags first, then the configured caps
        args = [self._config('command'), 'run', '--rm', '-d' if detach else '-i', '--name', name]
        args += ['--cap-drop', 'ALL', '--security-opt', 'no-new-privileges']
        args += ['--network', str(self._config('network'))]
        if self._config('read_only'):
            args += ['--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m']
        if self._config('memory_mb'):
            args += ['--memory', f'{self._config("memory_mb")}m']
        if self._config('pids'):
            args += ['--pids-limit', str(self._config('pids'))]
        if self._config('user'):
            args += ['--user', str(self._config('user'))]
        for guest_path, host_path in (self._config('mounts') or {}).items():
            args += ['-v', f'{os.path.abspath(host_path)}:{guest_path}:ro']
        for key, value in expand_env(self._config('env')).items():
            args += ['-e', f'{key}={value}']
        return args

    def _call(self, args, job, dotted):
        # run the client, stdin carries the job; a timeout is a failure
        try:
            proc = subprocess.run(
                args,
                input=job.encode() if job is not None else None,
                capture_output=True,
                timeout=self._config('timeout'),
            )
        except subprocess.TimeoutExpired:
            raise TokeoAiError(f'tool {dotted!r} timed out after {self._config("timeout")}s in the docker sandbox')
        except OSError as err:
            raise TokeoAiError(f'the docker sandbox cannot run {args[0]!r}: {err}')
        out, err = proc.stdout.decode(errors='replace'), proc.stderr.decode(errors='replace')
        if job is None and proc.returncode != 0:
            raise TokeoAiError(f'the docker sandbox cannot start its container: {err.strip()[:200]}')
        return out, err

    def _kill(self, name):
        # remove the container; it may be gone already (--rm)
        try:
            subprocess.run([self._config('command'), 'kill', name], capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
