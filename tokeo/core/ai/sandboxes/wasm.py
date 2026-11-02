"""
The wasm sandbox: real isolation against untrusted code, not just cleanup.

It runs a tool call inside a WebAssembly guest under Wasmtime. The guest is a
user-supplied CPython-WASI build (the ```runtime```/```stdlib``` options). Per
call the host writes the task, a small entry script and copies of the
stdlib-only ```GUEST_MODULES``` into a fresh scratch dir mounted read-write at
```/io```. The guest has no network (the syscalls do not exist in its world),
sees no host file outside the explicit ```mounts```, and runs under a hard
memory cap (Wasmtime store limits, the same on every platform) and an epoch
timeout. This is deny-by-default: a tool that needs a path or the network must
be granted it explicitly, or run in a different sandbox.

### Notes

: The guest delivers only the value, as data in ```/io/value.json```. The host
    tells everything else itself: the exit code, a trap, the timeout, and the
    stdout and stderr it captures outside ```/io```. A guest cannot forge the
    run states of its result.

: On the untrusted path the guest runs the snippet directly and sees nothing of
    tokeo; on the trusted path it rebuilds the tool with ```app=None``` from the
    mounted code.

: Full documentation (when to use it, the two python-exec tools, installing a
    WASI Python build into ```./wasm```, every option, the file bridge, the two
    trust models, the WASI stdlib shim, and troubleshooting) lives in
    ```WASM.md``` next to this module.
"""

import os
import json
import shutil
import tempfile
import importlib.util

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError, ToolResult, ToolValue, ToolStates
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env

#: The stdlib-only modules the guest gets as copies in ```/io/_guest```, per run.
GUEST_MODULES = (
    'tokeo.core.utils.json',
    'tokeo.core.utils.date',
    'tokeo.core.utils.date_compat',
    'tokeo.core.ai.pysnippet',
)

#: The most bytes the host reads of the value, stdout and stderr of a run.
REPLY_LIMIT = 1024 * 1024

# the exit code of a guest entry whose own machinery failed
_EXIT_MACHINERY = 3

# the start of both guest entries: a failure before the tool code runs is the
# machinery's, so it exits with _EXIT_MACHINERY
_GUEST_SETUP = r"""
import sys
import json
import traceback

try:
    from _guest.json import json_dump, TokeoJsonUnknownNoneEncoder

    with open('/io/task.json') as f:
        task = json.load(f)
    arguments = task.get('arguments') or {}
"""

# the end of both guest entries: encode the value like the host encoder does
# and write it as data; no file means no value
_GUEST_DELIVER = r"""
except BaseException:
    traceback.print_exc()
    sys.exit(3)


class Encoder(TokeoJsonUnknownNoneEncoder):

    def __init__(self):
        super().__init__()
        self.substituted = False

    def encode(self, obj):
        result = super().encode(obj)
        if result is None:
            self.substituted = True
            return type(obj).__name__
        return result


def deliver(value):
    if value is None:
        return
    encoder = Encoder()
    as_json = json_dump(value, encoder=encoder)
    with open('/io/value.json', 'w') as f:
        json.dump(dict(as_str=str(value), as_json=as_json, incomplete=encoder.substituted), f)
"""

# the untrusted entry: runs the code argument, rebuilds no tool from tokeo
_GUEST_ENTRY_EXEC_PYSNIPPET = (
    _GUEST_SETUP
    + '    from _guest.pysnippet import run_snippet\n'
    + _GUEST_DELIVER
    + "\ndeliver(run_snippet(arguments.get('code') or '', {}))\n"
)

# the trusted entry: rebuilds the tool from the mounted code and calls it; a
# ToolResult hands over its value, its states are the host's to tell
_GUEST_ENTRY_EXEC_TOOL = (
    _GUEST_SETUP
    + r"""    import importlib
    from tokeo.core.ai.data import ToolResult

    tool = getattr(importlib.import_module(task['module']), task['cls'])(None)
    tool._setup(None, None, {'options': task.get('options') or {}})
"""
    + _GUEST_DELIVER
    + r"""
result = tool.exec(**arguments)
if isinstance(result, ToolResult):
    if result.value is not None:
        with open('/io/value.json', 'w') as f:
            json.dump(dict(as_str=result.value.as_str, as_json=result.value.as_json, incomplete=result.state.incomplete), f)
else:
    deliver(result)
"""
)


class TokeoAiWasmSandbox(TokeoAiSandbox):
    """
    Run a tool call inside a Wasmtime WebAssembly guest.

    Deny-by-default isolation: no network at all, only the explicitly mounted
    host paths are visible, a hard memory cap and an epoch timeout. The guest
    is a user-supplied CPython-WASI build named by the ```runtime```/```stdlib```
    options; the tool runs across a file bridge in a private scratch dir.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Positional arguments for the parent initializer

    ### Keyword Args

    - **kw**: Keyword arguments for the parent initializer

    """

    class Meta:
        """The wasm mechanism's own settings (its option keys)."""

        # runtime: python.wasm, stdlib: its stdlib (read-only at /lib), mounts:
        # {guest_path: host_path} read-only, cwd: host dir read-write at /work,
        # env: scrubbed, ${NAME} expands, timeout: seconds (epoch), memory_mb:
        # hard cap, shim_wasi_stdlib: a multiprocessing stand-in on the trusted
        # path, where a rebuilt framework imports it at load
        config_defaults = dict(
            runtime=None,
            stdlib=None,
            mounts=None,
            cwd=None,
            env=None,
            timeout=None,
            memory_mb=None,
            shim_wasi_stdlib=True,
        )

    def __init__(self, app, *args, **kw):
        super(TokeoAiWasmSandbox, self).__init__(app, *args, **kw)
        # proxied config, filled in _setup (each is used at more than one place)
        self._runtime = None
        self._stdlib = None

    def _setup(self, app, config_name=None, config=None):
        """
        Set up the wasm sandbox after instantiation.

        Proxies the runtime and stdlib paths into instance fields: each is read
        at more than one place (exec validates them, _run_guest mounts/loads
        them), so they are resolved once here instead of per use.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the sandbox is declared under
        - **config** (dict, optional): The raw declaration

        """
        # super first: builds the view the _config below reads
        super(TokeoAiWasmSandbox, self)._setup(app, config_name, config)
        # proxied: runtime and stdlib are each used at more than one place
        self._runtime = self._config('runtime')
        self._stdlib = self._config('stdlib')

    def exec(self, tool, arguments):
        """
        Run the tool in a wasm guest and return its result.

        The guest delivers only the value; the host builds the run states from
        what it observed: the exit code, the captured stdout and stderr, and on
        a raising tool the last line of stderr as ```exception```.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool; the trusted guest
            rebuilds it by the import path of its class and its
            ```_tokeo_parent_instance_options```
        - **arguments** (dict): The parsed, JSON-able call arguments

        ### Returns

        - **ToolResult**: The value from the guest, the states from the host

        ### Raises

        - **TokeoAiError**: On a missing runtime, a missing guest module, a
            timeout, a crash, a failed guest machinery, or a broken value

        """
        # wasmtime is the optional extra tokeo-fundi[wasm], imported on use
        try:
            import wasmtime
        except ImportError:
            raise TokeoAiError("the wasm sandbox needs the wasmtime package: pip install 'tokeo-fundi[wasm]'")
        if not self._runtime or not self._stdlib:
            raise TokeoAiError('the wasm sandbox needs a runtime (python.wasm) and a stdlib path; see the docs')

        dotted = _importable_path(type(tool), 'tool')
        # an untrusted tool runs as a snippet; nothing of tokeo enters the guest
        trusted = not getattr(tool, 'wasm_exec_pysnippet', False)
        task = dict(arguments=arguments or {})
        if trusted:
            module, _, cls = dotted.rpartition('.')
            task.update(module=module, cls=cls, options=getattr(tool, '_tokeo_parent_instance_options', {}) or {})
        # /io is the guest's only read-write surface; the captures live apart
        # from it, so the guest cannot write its own stdout or stderr
        with tempfile.TemporaryDirectory() as io_dir, tempfile.TemporaryDirectory() as cap_dir:
            with open(os.path.join(io_dir, 'task.json'), 'w') as f:
                json.dump(task, f)
            with open(os.path.join(io_dir, 'entry.py'), 'w') as f:
                f.write(_GUEST_ENTRY_EXEC_TOOL if trusted else _GUEST_ENTRY_EXEC_PYSNIPPET)
            guest_dir = os.path.join(io_dir, '_guest')
            os.makedirs(guest_dir)
            open(os.path.join(guest_dir, '__init__.py'), 'w').close()
            for name in GUEST_MODULES:
                try:
                    spec = importlib.util.find_spec(name)
                except ImportError:
                    spec = None
                if spec is None or not spec.origin or not os.path.isfile(spec.origin):
                    raise TokeoAiError(f'the wasm guest module {name!r} has no source file')
                shutil.copyfile(spec.origin, os.path.join(guest_dir, name.rpartition('.')[2] + '.py'))
            shims = trusted and self._config('shim_wasi_stdlib')
            if shims:
                source = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wasi_shims')
                os.makedirs(os.path.join(io_dir, '_shims'))
                for name in ('multiprocessing.py',):
                    shutil.copyfile(os.path.join(source, name), os.path.join(io_dir, '_shims', name))
            code = self._run_guest(wasmtime, io_dir, cap_dir, dotted, shims)

            def read(path):
                # at most REPLY_LIMIT bytes, a missing file reads as empty
                if not os.path.exists(path):
                    return ''
                with open(path, 'rb') as f:
                    return f.read(REPLY_LIMIT + 1).decode('utf-8', 'replace')

            stdout = read(os.path.join(cap_dir, 'stdout'))[:REPLY_LIMIT]
            stderr = read(os.path.join(cap_dir, 'stderr'))[:REPLY_LIMIT]
            if code == _EXIT_MACHINERY:
                raise TokeoAiError(f'tool {dotted!r} failed in the wasm sandbox: {stderr.strip()[-400:]}')
            reply = read(os.path.join(io_dir, 'value.json')) if code == 0 else ''
        if len(reply) > REPLY_LIMIT:
            raise TokeoAiError(f'tool {dotted!r} delivered a value over {REPLY_LIMIT} bytes in the wasm sandbox')
        value, incomplete = None, False
        if reply:
            try:
                doc = json.loads(reply)
                as_str, as_json, incomplete = doc['as_str'], doc['as_json'], bool(doc.get('incomplete'))
                value = ToolValue(as_str=str(as_str), as_json=as_json, as_data=json.loads(as_json))
            except (ValueError, TypeError, KeyError) as err:
                raise TokeoAiError(f'tool {dotted!r} delivered no valid value in the wasm sandbox: {err}')
        # a raising tool exits non-zero; its message is the last stderr line
        lines = [line for line in stderr.splitlines() if line.strip()]
        exception = lines[-1].strip() if code != 0 and lines else None
        state = ToolStates(incomplete=incomplete, stdout=stdout or None, stderr=stderr or None, exception=exception)
        return ToolResult(value=value, state=state)

    def _run_guest(self, wasmtime, io_dir, cap_dir, dotted, shims):
        # assemble the wasmtime store with the cap, the timeout, the mounts and
        # the scrubbed env, run the guest entry and return its exit code
        config = wasmtime.Config()
        timeout = self._config('timeout')
        if timeout:
            config.epoch_interruption = True
        engine = wasmtime.Engine(config)
        store = wasmtime.Store(engine)
        memory_mb = self._config('memory_mb')
        if memory_mb:
            # store limits make the guest refuse growth past the bound: a
            # runaway allocation traps, platform-independent unlike rlimit
            store.set_limits(memory_size=memory_mb * 1024 * 1024)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        wasi = wasmtime.WasiConfig()
        wasi.argv = ('python', '/io/entry.py')

        def mount(host_path, guest_path, writable, what):
            # name the path and its role; wasmtime only says "failed to add
            # preopen dir"
            if not os.path.isdir(host_path):
                raise TokeoAiError(f'the wasm {what} path does not exist or is not a directory: {host_path!r}')
            wasi.preopen_dir(host_path, guest_path, fs_mutable=writable)

        mount(self._stdlib, '/lib', False, 'stdlib')
        mount(io_dir, '/io', True, 'io scratch')
        stderr_path = os.path.join(cap_dir, 'stderr')
        wasi.stdout_file = os.path.join(cap_dir, 'stdout')
        wasi.stderr_file = stderr_path
        work = self._config('cwd')
        if work:
            os.makedirs(work, exist_ok=True)
            mount(work, '/work', True, 'cwd scratch')
        # only what the config granted, read-only
        for guest_path, host_path in (self._config('mounts') or {}).items():
            mount(host_path, guest_path, False, f'mount {guest_path!r}')
        # the scrubbed env: the stdlib boots the interpreter, the shims lead on
        # the trusted path; /io, the script's dir, holds _guest
        env = expand_env(self._config('env'))
        parts = (['/io/_shims'] if shims else []) + ['/lib']
        if env.get('PYTHONPATH'):
            parts.append(env['PYTHONPATH'])
        env['PYTHONPATH'] = ':'.join(parts)
        env.setdefault('PYTHONHOME', '/lib')
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # WasiConfig.env takes all pairs at once, per key keeps only the last
        wasi.env = [(key, value) for key, value in env.items()]
        store.set_wasi(wasi)
        module = wasmtime.Module.from_file(engine, self._runtime)
        if timeout:
            # with epoch interruption the deadline starts at 0 and instantiate
            # already runs start code, so set it before
            store.set_epoch_deadline(1)
        instance = linker.instantiate(store, module)
        start = instance.exports(store)['_start']
        if timeout:
            import threading

            ticker = threading.Timer(timeout, engine.increment_epoch)
            ticker.daemon = True
            ticker.start()
        try:
            start(store)
            return 0
        except wasmtime.ExitTrap as exit_trap:
            return getattr(exit_trap, 'code', 0)
        except wasmtime.Trap as trap:
            # newer wasmtime marks the epoch trap by code, older ones by text
            trap_code = getattr(wasmtime, 'TrapCode', None)
            code_hit = trap_code is not None and getattr(trap, 'trap_code', None) == trap_code.INTERRUPT
            text_hit = any(word in str(trap).lower() for word in ('epoch', 'interrupt'))
            if timeout and (code_hit or text_hit):
                raise TokeoAiError(f'tool {dotted!r} timed out after {timeout}s in the wasm sandbox')
            detail = ''
            if os.path.exists(stderr_path):
                with open(stderr_path, errors='replace') as f:
                    detail = f.read().strip()[-400:]
            raise TokeoAiError(f'tool {dotted!r} crashed in the wasm sandbox: {detail or str(trap).splitlines()[0]}')
        finally:
            if timeout:
                ticker.cancel()
