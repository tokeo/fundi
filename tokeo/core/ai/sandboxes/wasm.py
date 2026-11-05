"""
The wasm sandbox: real isolation against untrusted code, not just cleanup.

It runs a tool call inside a WebAssembly guest under Wasmtime. The guest is a
user-supplied CPython-WASI build (the ```runtime```/```stdlib``` options). Per
call the host writes the task, a small entry script and copies of the
stdlib-only ```WASM_GUEST_MODULE_COPIES``` into a fresh scratch dir mounted
read-write at ```/io```. The guest has no network (the syscalls do not exist
in its world), sees no host file outside the explicit ```mounts```, and runs
under a hard memory cap (Wasmtime store limits, the same on every platform)
and an epoch timeout. This is deny-by-default: a tool that needs a path or the
network must be granted it explicitly, or run in a different sandbox.

### Notes

: The guest delivers its value as data in ```/io/value.json```, with the flag
    ```incomplete```. The host observes the exit code, a trap, the timeout and
    the limits itself, and captures stdout and stderr outside ```/io``` (they
    count for ```disk_mb```, and are cut at ```output_mb```); on a failed exit
    the last stderr line becomes ```exception```. What the guest
    writes (the value, ```incomplete```, the text of stdout, stderr and the
    exception) is a hint the host does not trust; it decides only on what it
    observed.

: On the untrusted path the guest runs the snippet directly and sees nothing of
    tokeo; on the trusted path it rebuilds the tool with ```app=None``` from the
    mounted code.

: Full documentation (when to use it, the two python-exec tools, installing a
    WASI Python build into ```./wasm```, the file bridge, the two trust
    models, the WASI stdlib shim, and troubleshooting) lives in ```WASM.md```
    next to this module; the options are specified in section 5.5 of
    ```CONFIG.md```.
"""

import os
import json
import shutil
import hashlib
import tempfile
import threading
import importlib.util

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError, ToolResult, ToolValue, ToolStates
from tokeo.core.ai.sandbox import SANDBOX_OUTPUT_MAX_MB
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env, output_bytes, reply_bytes, check_output
from tokeo.core.utils.validate import is_number_ge

#: The stdlib-only modules the guest gets as copies in ```/io/_guest```, per run.
WASM_GUEST_MODULE_COPIES = (
    'tokeo.core.utils.json',
    'tokeo.core.utils.date',
    'tokeo.core.utils.date_compat',
    'tokeo.core.ai.pysnippet',
)

# the exit code of a guest entry whose own machinery failed
_GUEST_EXIT_MACHINERY = 3

# the start of both guest entries: a failure before the tool code runs is the
# machinery's, so it exits with _GUEST_EXIT_MACHINERY
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

        # the configurable defaults; the item's options overlay them, read at
        # runtime via _config
        config_defaults = dict(
            # required: the CPython-WASI interpreter, python.wasm
            runtime=None,
            # required: its stdlib, mounted read-only at /lib
            stdlib=None,
            # {guest_path: host_path}, mounted read-only
            mounts=None,
            # a host dir mounted read-write at /work, created on demand
            cwd=None,
            # the only variables of the run, ${NAME} expands; empty by default
            env=None,
            # wall-clock seconds, then a timer stops the guest; None = no limit
            timeout=None,
            # MB of memory, a hard cap by the store limits; None = no limit
            memory_mb=None,
            # MB the guest may add to /io, /work and its stdout and stderr
            disk_mb=64,
            # files and dirs the guest may add to /io and /work
            max_files=1000,
            # seconds between two checks of disk_mb and max_files, at least 0.1
            watch_interval=1.0,
            # a dir only the operator can write, for the compiled runtime
            cache=None,
            # a multiprocessing stand-in, only on the trusted path
            shim_wasi_stdlib=True,
            # MB of stdout and stderr kept, and the largest value read
            output_mb=SANDBOX_OUTPUT_MAX_MB,
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

    def validate_options(self, options):
        """
        Check the values the linter cannot know.

        ### Args

        - **options** (dict): The item's ```options``` block

        ### Returns

        - **list[str]**: An error for a watch_interval below 0.1 seconds or an
            output_mb not above 0

        """
        errors = TokeoAiSandbox.validate_options(self, options)
        if not is_number_ge((options or {}).get('watch_interval', 1.0), 0.1):
            errors.append('watch_interval must be a number of seconds, at least 0.1')
        return errors

    def exec(self, tool, arguments):
        """
        Run the tool in a wasm guest and return its result.

        The guest delivers its value and ```incomplete```; the host builds the
        rest from what it observed: the exit code, the captured stdout and
        stderr, and on a failed exit the last line of stderr as
        ```exception```. The guest's part is a hint, not a trusted fact.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool; the trusted guest
            rebuilds it by the import path of its class and its
            ```_tokeo_parent_instance_options```
        - **arguments** (dict): The parsed, JSON-able call arguments

        ### Returns

        - **ToolResult**: The value from the guest, the states from the host

        ### Raises

        - **TokeoAiError**: On a missing runtime, a missing guest module, a
            timeout, a broken disk or file limit, a crash, a failed guest
            machinery, or a broken value

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
            for name in WASM_GUEST_MODULE_COPIES:
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

            output_mb = self._config('output_mb')
            max_bytes = output_bytes(output_mb)

            def read(path, limit):
                # one byte over the limit, cut in bytes before decoding; a
                # missing file reads as empty
                if not os.path.exists(path):
                    return b''
                with open(path, 'rb') as f:
                    return f.read(limit + 1)

            def text(data):
                # the captured output, cut at output_mb and marked like the
                # captures of the other sandboxes
                cut = f'\n[... cut after {output_mb:g} MB]' if len(data) > max_bytes else ''
                return data[:max_bytes].decode('utf-8', 'replace') + cut

            stdout = text(read(os.path.join(cap_dir, 'stdout'), max_bytes))
            stderr = text(read(os.path.join(cap_dir, 'stderr'), max_bytes))
            if code == _GUEST_EXIT_MACHINERY:
                raise TokeoAiError(f'tool {dotted!r} failed in the wasm sandbox: {stderr.strip()[-400:]}')
            # the raw value.json frames two views of the value, read with the
            # cap of a raw reply; the views are held to output_mb below
            data = read(os.path.join(io_dir, 'value.json'), reply_bytes(max_bytes)) if code == 0 else b''
        if len(data) > reply_bytes(max_bytes):
            raise TokeoAiError(f'tool {dotted!r} delivered more than {output_mb:g} MB in the wasm sandbox')
        reply = data.decode('utf-8', 'replace')
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
        return check_output(ToolResult(value=value, state=state), max_bytes, f'{output_mb:g} MB', dotted, 'wasm')

    def _run_guest(self, wasmtime, io_dir, cap_dir, dotted, shims):
        # assemble the wasmtime store with the cap, the mounts and the scrubbed
        # env, run the guest entry under the watcher, return its exit code.
        # epoch interruption is always on: it is the only way to stop a guest
        config = wasmtime.Config()
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
        module = self._module(wasmtime, engine)
        # the deadline is set before instantiate, which already runs start code
        store.set_epoch_deadline(1)
        instance = linker.instantiate(store, module)
        start = instance.exports(store)['_start']
        # the guest's stdout and stderr in cap_dir count too, they fill the
        # disk of the host as well
        roots = [io_dir, cap_dir] + ([work] if work else [])
        disk_mb, max_files, timeout = self._config('disk_mb'), self._config('max_files'), self._config('timeout')
        interval = self._config('watch_interval')

        def usage(stop=None):
            # bytes and entries under the roots, counting stops past stop
            size = count = 0
            for root in roots:
                for folder, dirs, files in os.walk(root):
                    count += len(dirs) + len(files)
                    for name in files:
                        try:
                            size += os.lstat(os.path.join(folder, name)).st_size
                        except OSError:
                            pass
                    if stop and count > stop:
                        return size, count
            return size, count

        def exceeded():
            # the limit the guest broke, or None
            size, count = usage(base_count + max_files if max_files else None)
            if disk_mb and size - base_size > disk_mb * 1024 * 1024:
                return f'wrote more than {disk_mb} MB'
            if max_files and count - base_count > max_files:
                return f'created more than {max_files} files'
            return None

        base_size, base_count = usage()
        reason = []
        done = threading.Event()

        def stop(why):
            # the first broken limit names the stop; the guest ends at its
            # next epoch check
            if not reason:
                reason.append(why)
            engine.increment_epoch()

        def watch():
            # one check of size and files per interval
            while not done.wait(interval):
                broken = exceeded()
                if broken:
                    stop(broken)
                    return

        # the timeout fires once at its time, the limits are checked per interval
        timer = threading.Timer(timeout, stop, (f'timed out after {timeout}s',)) if timeout else None
        watcher = threading.Thread(target=watch, daemon=True) if disk_mb or max_files else None
        for thread in (timer, watcher):
            if thread:
                thread.daemon = True
                thread.start()
        try:
            start(store)
            code = 0
        except wasmtime.ExitTrap as exit_trap:
            code = getattr(exit_trap, 'code', 0)
        except wasmtime.Trap as trap:
            if reason:
                raise TokeoAiError(f'tool {dotted!r} {reason[0]} in the wasm sandbox')
            detail = ''
            if os.path.exists(stderr_path):
                with open(stderr_path, errors='replace') as f:
                    detail = f.read().strip()[-400:]
            raise TokeoAiError(f'tool {dotted!r} crashed in the wasm sandbox: {detail or str(trap).splitlines()[0]}')
        finally:
            done.set()
            if timer:
                timer.cancel()
            if watcher:
                watcher.join()
        # a guest that broke a limit and ended before the next check still failed
        broken = (disk_mb or max_files) and exceeded()
        if broken:
            raise TokeoAiError(f'tool {dotted!r} {broken} in the wasm sandbox')
        return code

    def _module(self, wasmtime, engine):
        # the compiled runtime: from the cache dir when set and fitting, else
        # compiled now (and stored there). a file in the cache is native code
        # the host runs, so the dir must be writable only by the operator
        with open(self._runtime, 'rb') as f:
            wasm = f.read()
        cache = self._config('cache')
        if not cache:
            return wasmtime.Module(engine, wasm)
        path = os.path.join(cache, f'python-{hashlib.sha256(wasm).hexdigest()[:32]}.cwasm')
        if os.path.isfile(path):
            try:
                return wasmtime.Module.deserialize_file(engine, path)
            except wasmtime.WasmtimeError:
                # compiled by another wasmtime or config: compile it anew
                pass
        module = wasmtime.Module(engine, wasm)
        os.makedirs(cache, exist_ok=True)
        part = f'{path}.{os.getpid()}.part'
        with open(part, 'wb') as f:
            f.write(module.serialize())
        os.replace(part, path)
        return module
