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
a container of the configured ```image```, with python as the entrypoint in
place of the image's own, feeding the job as JSON on stdin
and reading the ```ToolResult``` as JSON on stdout, like the subprocess sandbox.
The image must hold tokeo, fundi and the tools of the app; the image of the app
itself is the simplest choice.

Always set, not configurable: ```--rm```, ```--cap-drop ALL```,
```--security-opt no-new-privileges```, never ```--privileged```, and every
mount read-only. Configurable with safe defaults: no network, a read-only root
file system with a ```/tmp``` of ```max_disk_mb```, a memory and a process limit, a
timeout, a user that is not root, and ```max_output_mb``` for the output the host
reads.

```yaml
sandboxes:
  docked:
    type: docker
    tools: [mathematics]
    options:
      image: myapp:latest
      max_memory_mb: 256
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
import tempfile
import subprocess

from tokeo.core.utils.validate import is_int_ge
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

# seconds the client may take to remove a container after a timeout
_DOCKER_KILL_TIMEOUT = 30


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
        """Docker sandbox meta-data."""

        # the configurable defaults; the item's options overlay them, read at
        # runtime via _config
        config_defaults = dict(
            # required: an image with tokeo, fundi and the tools of the app
            image=None,
            # the client: docker or podman (rootless)
            command='docker',
            # the interpreter in the image, it replaces the image's entrypoint
            python='python3',
            # a docker network, none = no network at all
            network='none',
            # a read-only root file system with a tmpfs /tmp of max_disk_mb
            read_only=True,
            # MB of the tmpfs /tmp, the only writable place with read_only
            max_disk_mb=64,
            # MB of memory (cgroup), 0 = no limit
            max_memory_mb=512,
            # processes in the container, 0 = no limit
            max_pids=128,
            # the user in the container, never root
            user='65534:65534',
            # {guest_path: host_path}, mounted read-only
            mounts=None,
            # the only variables of the run, ${NAME} expands; empty by default
            env=None,
            # wall-clock seconds, then the container is killed
            timeout=30,
            # true = one container for the process, else one per call
            reuse=False,
            # MB of stdout and stderr kept, and the largest reply read
            max_output_mb=SANDBOX_MAX_OUTPUT_MB,
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

        - **list[str]**: An error for a missing image, a root user, a max_disk_mb
            that is no whole number from 1, or a max_output_mb not above 0

        """
        errors = TokeoAiSandbox.validate_options(self, options)
        if 'max_disk_mb' in (options or {}) and not is_int_ge(options['max_disk_mb'], 1):
            errors.append('max_disk_mb must be a whole number of MB, at least 1')
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
        class_path = _get_importable_path(type(tool), 'tool')
        job = json.dumps(
            dict(
                tool=class_path,
                arguments=arguments or {},
                options=getattr(tool, '_tokeo_parent_instance_options', {}) or {},
                limits={},
                max_output_mb=self._config('max_output_mb'),
            )
        )
        # the image's own entrypoint (often the app) is replaced by python
        python = self._config('python')
        runner = ['-m', 'tokeo.core.ai.sandboxes.runner']
        if self._config('reuse'):
            with self._lock:
                if self._container is None:
                    name = self._build_container_name()
                    idle_cmd = ['--entrypoint', python, image, '-c', 'import signal; signal.pause()']
                    self._run_client(self._build_run_args(name, detach=True) + idle_cmd, None, class_path)
                    self._container = name
                    atexit.register(self.close)
                name = self._container
            args = [self._config('command'), 'exec', '-i', name, python] + runner
        else:
            name = self._build_container_name()
            args = self._build_run_args(name, detach=False) + ['--entrypoint', python, image] + runner
        try:
            out, err = self._run_client(args, job, class_path)
        except TokeoAiError:
            # a timed out or broken container goes, reused or not
            self._kill_container(name)
            with self._lock:
                if self._container == name:
                    self._container = None
            raise
        max_output_mb = self._config('max_output_mb')
        result = result_from_reply(out, err, class_path, 'docker')
        return check_output(result, output_bytes(max_output_mb), f'{max_output_mb:g} MB', class_path, 'docker')

    def close(self):
        """Remove the container of ```reuse```, if one is running."""
        with self._lock:
            if self._container is not None:
                self._kill_container(self._container)
                self._container = None

    def _build_container_name(self):
        # a unique container name, so a timeout can remove exactly this one
        label = re.sub(r'[^a-zA-Z0-9_.-]', '-', self.config_name or 'docker')
        return f'tokeo-ai-{label}-{os.getpid()}-{uuid.uuid4().hex[:8]}'

    def _build_run_args(self, name, detach):
        # the hardening: fixed flags first, then the configured limits
        args = [self._config('command'), 'run', '--rm', '-d' if detach else '-i', '--name', name]
        args += ['--cap-drop', 'ALL', '--security-opt', 'no-new-privileges']
        args += ['--network', str(self._config('network'))]
        if self._config('read_only'):
            args += ['--read-only', '--tmpfs', f'/tmp:rw,noexec,nosuid,size={self._config("max_disk_mb")}m']
        if self._config('max_memory_mb'):
            args += ['--memory', f'{self._config("max_memory_mb")}m']
        if self._config('max_pids'):
            args += ['--pids-limit', str(self._config('max_pids'))]
        if self._config('user'):
            args += ['--user', str(self._config('user'))]
        for guest_path, host_path in (self._config('mounts') or {}).items():
            args += ['-v', f'{os.path.abspath(host_path)}:{guest_path}:ro']
        for key, value in expand_env(self._config('env')).items():
            args += ['-e', f'{key}={value}']
        return args

    def _run_client(self, args, job, class_path):
        # run the client, stdin carries the job; a timeout is a failure. stdout
        # and stderr go to files and are read at most max_output_mb, so a reply
        # without end cannot fill the memory of the host
        max_output_mb = self._config('max_output_mb')
        with tempfile.TemporaryFile() as out_file, tempfile.TemporaryFile() as err_file:
            try:
                proc = subprocess.run(
                    args,
                    input=job.encode() if job is not None else None,
                    stdout=out_file,
                    stderr=err_file,
                    timeout=self._config('timeout'),
                )
            except subprocess.TimeoutExpired:
                raise TokeoAiError(f'tool {class_path!r} timed out after {self._config("timeout")}s in the docker sandbox')
            except OSError as err:
                raise TokeoAiError(f'the docker sandbox cannot run {args[0]!r}: {err}')
            out_file.seek(0)
            err_file.seek(0)
            out = read_reply(out_file, output_bytes(max_output_mb), f'{max_output_mb:g} MB', class_path, 'docker')
            err = read_diagnostics(err_file, output_bytes(max_output_mb))
        if job is None and proc.returncode != 0:
            raise TokeoAiError(f'the docker sandbox cannot start its container: {err.strip()[:200]}')
        return out, err

    def _kill_container(self, name):
        # remove the container; it may be gone already (--rm)
        try:
            kill_cmd = [self._config('command'), 'kill', name]
            subprocess.run(kill_cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=_DOCKER_KILL_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            pass
