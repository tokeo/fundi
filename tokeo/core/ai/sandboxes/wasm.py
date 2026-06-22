"""
The wasm sandbox: real isolation against untrusted code, not just cleanup.

It runs a tool call inside a WebAssembly guest under Wasmtime. The guest is a
user-supplied CPython-WASI build (the ```runtime```/```stdlib``` options); the
host writes the task as JSON into a private scratch dir mounted read-write at
```/io```, a small bundled guest entry script rebuilds the tool and writes the
```ToolResult``` back to ```/io/reply.json```. The guest has NO network (the
syscalls do not exist in its world), sees NO host file outside the explicit
```mounts```, and runs under a hard memory cap (Wasmtime store limits, the same
on every platform) and an epoch timeout. This is deny-by-default: a tool that
needs a path or the network must be granted it explicitly, or run in a
different sandbox.

### Notes

: The tool is rebuilt in the guest from its dotted ```type``` and ```options```
    with ```app=None``` -- a guest has no live parent app, and the uniformity
    rule means a tool that needs an app builds it itself. Only JSON-able
    arguments and the ```ToolResult``` text/data cross the bridge. Unlike the
    subprocess sandbox there is no parent-path injection: nothing is visible
    that the config did not mount, on purpose -- visibility is the whole point
    of choosing wasm.

: Full documentation -- when to use it, the two python-exec tools, installing a
    WASI Python build into ```./wasm```, every option, the file bridge, the two
    trust models, the WASI stdlib shims, and troubleshooting -- lives in
    ```WASM.md``` next to this module.
"""

import os
import json
import tempfile

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError, ToolResult
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env

# the guest entry script: it runs INSIDE the wasm interpreter, reads the task
# from the read-write /io mount, rebuilds the tool with no app, and writes the
# reply. kept as a string so the host can drop it into the scratch dir next to
# the task -- the guest needs no host import path beyond the mounted code
_GUEST_ENTRY = r"""
import json, importlib

with open('/io/task.json') as f:
    task = json.load(f)


def _load(dotted, options):
    module_path, _, attr = dotted.rpartition('.')
    cls = getattr(importlib.import_module(module_path), attr)
    tool = cls(None, **(options or {}))
    tool._setup(None)
    return tool


reply = {}
try:
    tool = _load(task['tool'], task.get('options'))
    result = tool.exec(**(task.get('arguments') or {}))
    if isinstance(result, str):
        reply = dict(text=result, data=None)
    else:
        reply = dict(text=getattr(result, 'text', ''), data=getattr(result, 'data', None))
except Exception as err:
    reply = dict(error='{}: {}'.format(type(err).__name__, err))

with open('/io/reply.json', 'w') as f:
    json.dump(reply, f)
"""

# the direct-exec guest entry for untrusted tools: it runs the code argument
# itself, importing NOTHING from tokeo, so no app mount is needed and the
# untrusted snippet stays walled off from the framework. the result contract
# (assign ```result```) mirrors the untrusted tool's own exec body
_GUEST_ENTRY_DIRECT = r"""
import json

with open('/io/task.json') as f:
    task = json.load(f)

reply = {}
try:
    code = (task.get('arguments') or {}).get('code') or ''
    namespace = {}
    exec(compile(code, '<python_exec>', 'exec'), namespace)
    result = namespace.get('result')
    text = '' if result is None else str(result)
    try:
        json.dumps(result)
        data = result
    except (TypeError, ValueError):
        data = None
    reply = dict(text=text, data=data)
except Exception as err:
    reply = dict(error='{}: {}'.format(type(err).__name__, err))

with open('/io/reply.json', 'w') as f:
    json.dump(reply, f)
"""


class TokeoAiWasmSandbox(TokeoAiSandbox):
    """
    Run a tool call inside a Wasmtime WebAssembly guest.

    Deny-by-default isolation: no network at all, only the explicitly mounted
    host paths are visible, a hard memory cap and an epoch timeout. The guest
    is a user-supplied CPython-WASI build named by the ```runtime```/```stdlib```
    options; the tool runs across a file bridge in a private scratch dir.
    """

    class Meta:
        """The wasm mechanism's own settings (its option keys)."""

        # the configurable defaults, as one dict; the item's options overlay
        # this, read at runtime via _config. runtime: path to the CPython-WASI
        # interpreter (python.wasm), user-supplied, required to run. stdlib:
        # path to the matching WASI python standard library directory, mounted
        # read-only at /lib, required. mounts: guest->host read-only mounts as a
        # dict {guest_path: host_path}, the tool's own code and deps must be
        # granted here, empty means the guest sees no host code (deny-by-default).
        # cwd: scratch working directory on the host, mounted read-write at
        # /work, created on demand, None = a private temp dir. env: environment
        # for the guest, scrubbed/empty by default, only listed keys set, ${NAME}
        # expands against out -> host env -> ''. timeout: wall-clock seconds
        # before the guest is interrupted by epoch (None = unbounded), enforced
        # in-process, no child to kill. memory_mb: hard memory cap in MB via
        # Wasmtime store limits, platform-independent, refused allocations trap
        # the guest, None = unbounded. shim_wasi_stdlib: mount the bundled wasi
        # stdlib shims (multiprocessing/threading) ahead of the real stdlib so a
        # framework that imports those names at load (e.g. cement) can be rebuilt
        # in the guest, on by default; the shims are no-ops that error on real
        # concurrency, correct for the single-threaded guest, only matters on the
        # rebuild (trusted) path
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
        """
        Initialize the wasm sandbox.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiWasmSandbox, self).__init__(app, *args, **kw)
        # proxied config, filled in _setup (each is used at more than one place)
        self._runtime = None
        self._stdlib = None

    def _setup(self, app):
        """
        Set up the wasm sandbox after instantiation.

        Proxies the runtime and stdlib paths into instance fields: each is read
        at more than one place (exec validates them, _run_guest mounts/loads
        them), so they are resolved once here instead of per use.

        ### Args

        - **app**: The Tokeo application instance

        """
        super(TokeoAiWasmSandbox, self)._setup(app)
        # proxied: runtime and stdlib are each used at more than one place
        self._runtime = self._config('runtime')
        self._stdlib = self._config('stdlib')

    def exec(self, tool, arguments):
        """
        Run the tool in a wasm guest and return its result.

        ### Args

        - **tool** (TokeoAiTool): The instantiated tool; the guest rebuild
            imports by the canonical path of its class and reuses its
            ```_tokeo_parent_instance_options```
        - **arguments** (dict): The parsed, JSON-able call arguments

        ### Returns

        - **ToolResult**: The tool's result, rebuilt from the guest's JSON

        ### Raises

        - **TokeoAiError**: On a missing runtime, a timeout, or a guest error

        """
        # WHY lazy import: wasmtime is an opt-in extra (tokeo[wasm]); only this
        # sandbox needs it, so import on use and name the install on absence
        try:
            import wasmtime
        except ImportError:
            raise TokeoAiError('the wasm sandbox needs the wasmtime package -- use feature_ai_wasm')
        runtime = self._runtime
        stdlib = self._stdlib
        if not runtime or not stdlib:
            raise TokeoAiError('the wasm sandbox needs a runtime (python.wasm) and a stdlib path -- see the docs')

        dotted = _importable_path(type(tool), 'tool')
        # an untrusted tool flags itself for direct exec: the guest runs the
        # code argument without rebuilding the tool, so no tokeo mount is
        # needed and the framework stays invisible to the untrusted snippet
        direct = getattr(tool, 'wasm_direct_exec', False)
        task = dict(
            tool=dotted,
            arguments=arguments or {},
            options=getattr(tool, '_tokeo_parent_instance_options', {}) or {},
        )
        # the private io dir carries the task in and the reply out; it is the
        # only read-write surface the guest gets
        with tempfile.TemporaryDirectory() as io_dir:
            with open(os.path.join(io_dir, 'task.json'), 'w') as f:
                json.dump(task, f)
            with open(os.path.join(io_dir, 'entry.py'), 'w') as f:
                f.write(_GUEST_ENTRY_DIRECT if direct else _GUEST_ENTRY)
            self._run_guest(wasmtime, io_dir, dotted)
            reply_path = os.path.join(io_dir, 'reply.json')
            if not os.path.exists(reply_path):
                raise TokeoAiError(f'tool {dotted!r} produced no result in the wasm sandbox')
            with open(reply_path) as f:
                reply = json.load(f)
        if 'error' in reply:
            raise TokeoAiError(f'tool {dotted!r} failed in the wasm sandbox: {reply["error"]}')
        return ToolResult(text=reply.get('text', ''), data=reply.get('data'))

    def _run_guest(self, wasmtime, io_dir, dotted):
        # assemble the wasmtime store with the cap, the timeout, the mounts and
        # the scrubbed env, then run the guest entry under the interpreter
        config = wasmtime.Config()
        timeout = self._config('timeout')
        if timeout:
            config.epoch_interruption = True
        engine = wasmtime.Engine(config)
        store = wasmtime.Store(engine)
        memory_mb = self._config('memory_mb')
        if memory_mb:
            # WHY hard cap: store limits make the kernel of the guest refuse
            # growth past the bound -- a runaway allocation traps, it cannot
            # eat host memory. platform-independent, unlike rlimit
            store.set_limits(memory_size=memory_mb * 1024 * 1024)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()
        wasi = wasmtime.WasiConfig()
        wasi.argv = ('python', '/io/entry.py')

        ro = wasmtime.DirPerms.READ_ONLY
        rw = wasmtime.DirPerms.READ_WRITE
        ro_file = wasmtime.FilePerms.READ_ONLY
        rw_file = wasmtime.FilePerms.READ_WRITE

        def mount(host_path, guest_path, writable, what):
            # a missing host path makes wasmtime raise a bare "failed to add
            # preopen dir"; check first and say WHICH path and role is wrong
            if not os.path.isdir(host_path):
                raise TokeoAiError(f'the wasm {what} path does not exist or is not a directory: {host_path!r}')
            dir_perms = rw if writable else ro
            file_perms = rw_file if writable else ro_file
            wasi.preopen_dir(host_path, guest_path, dir_perms, file_perms)

        # the stdlib is read-only at /lib; the guest interpreter reads it to
        # import the standard library it needs
        mount(self._stdlib, '/lib', False, 'stdlib')
        # the io dir is the only read-write surface
        mount(io_dir, '/io', True, 'io scratch')
        # capture the guest's stderr so a non-zero exit reports WHY (an import
        # error, a fatal interpreter message) instead of a bare wasm backtrace
        stderr_path = os.path.join(io_dir, 'stderr.txt')
        wasi.stderr_file = stderr_path
        work = self._config('cwd')
        if work:
            os.makedirs(work, exist_ok=True)
            mount(work, '/work', True, 'cwd scratch')
        # explicit deny-by-default mounts: only what the config granted, all
        # read-only (the guest gets no write outside /io and /work)
        for guest_path, host_path in (self._config('mounts') or {}).items():
            mount(host_path, guest_path, False, f'mount {guest_path!r}')
        # the bundled wasi stdlib shims, mounted read-only at /lib/shims and put
        # FIRST on PYTHONPATH so they shadow the absent multiprocessing/
        # threading modules for a framework rebuilt in the guest
        shim_path = None
        if self._config('shim_wasi_stdlib'):
            shim_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wasi_shims')
            mount(shim_path, '/lib/shims', False, 'wasi shims')
        # the scrubbed env: only listed keys survive, expanded like elsewhere.
        # PYTHONPATH/PYTHONHOME point the interpreter at the stdlib mounted at
        # /lib so it can boot (find encodings etc); the shims lead, then /lib,
        # then a user PYTHONPATH so mounted tool code is importable too
        env = expand_env(self._config('env'))
        user_pythonpath = env.get('PYTHONPATH')
        parts = (['/lib/shims'] if shim_path else []) + ['/lib']
        if user_pythonpath:
            parts.append(user_pythonpath)
        env['PYTHONPATH'] = ':'.join(parts)
        env.setdefault('PYTHONHOME', '/lib')
        # WHY one assignment: WasiConfig.env takes the full list of pairs at
        # once; assigning per key would keep only the last pair
        wasi.env = [(key, value) for key, value in env.items()]
        store.set_wasi(wasi)
        module = wasmtime.Module.from_file(engine, self._runtime)
        instance = linker.instantiate(store, module)
        start = instance.exports(store)['_start']
        if timeout:
            store.set_epoch_deadline(1)
            import threading

            ticker = threading.Timer(timeout, engine.increment_epoch)
            ticker.daemon = True
            ticker.start()
        try:
            start(store)
        except wasmtime.ExitTrap as exit_trap:
            # the guest interpreter calls exit() at the end: status 0 is a
            # clean, successful run (the reply is already written); a nonzero
            # status is a real failure -- surface the captured stderr
            if getattr(exit_trap, 'code', 0) != 0:
                detail = ''
                if os.path.exists(stderr_path):
                    detail = open(stderr_path).read().strip()[-400:]
                raise TokeoAiError(f'tool {dotted!r} crashed in the wasm sandbox: {detail or exit_trap}')
        except wasmtime.Trap as trap:
            if timeout and 'epoch' in str(trap).lower():
                raise TokeoAiError(f'tool {dotted!r} timed out after {timeout}s in the wasm sandbox')
            # a trap with a written reply is the tool's own error (handled by
            # the caller); a trap without one is a guest-level failure -- the
            # captured stderr says why the interpreter aborted
            if not os.path.exists(os.path.join(io_dir, 'reply.json')):
                detail = ''
                if os.path.exists(stderr_path):
                    detail = open(stderr_path).read().strip()[-400:]
                first = str(trap).splitlines()[0]
                raise TokeoAiError(f'tool {dotted!r} crashed in the wasm sandbox: {detail or first}')
        finally:
            if timeout:
                ticker.cancel()

    def validate_options(self, options):
        """
        Validate the wasm options for the linter.

        Accepts only the keys this sandbox can act on, so a typo or an option
        that belongs to another backend surfaces as a lint error instead of a
        silently ignored setting.

        ### Args

        - **options** (dict): The item's ```options``` block

        ### Returns

        - **list[str] | None**: Error messages, or ```None``` when valid

        """
        allowed = {'runtime', 'stdlib', 'mounts', 'cwd', 'env', 'timeout', 'memory_mb', 'shim_wasi_stdlib'}
        unknown = sorted(set(options or {}) - allowed)
        if unknown:
            return [f'wasm sandbox does not support option {key!r} (allowed: {", ".join(sorted(allowed))})' for key in unknown]
        return None
