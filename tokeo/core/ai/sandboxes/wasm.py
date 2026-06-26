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
    arguments and the result's two string views (```as_str```/```as_json```)
    cross the bridge; the parent rebuilds ```as_data``` from ```as_json```. The
    guest builds the result with the pact contract (```create_tool_result```),
    mounted read-only for both paths since it is dependency-free and cannot
    weaken the isolation. Unlike the subprocess sandbox there is no parent-path
    injection: nothing else is visible that the config did not mount, on purpose
    -- visibility is the whole point of choosing wasm.

: Full documentation -- when to use it, the two python-exec tools, installing a
    WASI Python build into ```./wasm```, every option, the file bridge, the two
    trust models, the WASI stdlib shims, and troubleshooting -- lives in
    ```WASM.md``` next to this module.
"""

import os
import json
import tempfile

from tokeo.core.ai import TokeoAiSandbox, TokeoAiError, ToolResult, ToolValue, ToolStates
from tokeo.core.ai.sandboxes._common import _importable_path, expand_env

# the exec-tool guest entry (the trusted/rebuild path): it runs INSIDE the wasm
# interpreter, reads the task from the read-write /io mount, REBUILDS the tool
# with no app and calls its exec, then writes the reply. kept as a string so the
# host can drop it into the scratch dir next to the task -- the guest needs no
# host import path beyond the mounted code. the pact contract is mounted too, so
# the guest builds a ToolResult the same way the host would (create_tool_result),
# and the reply carries the value/state shape the parent rebuilds from
_GUEST_ENTRY_EXEC_TOOL = r"""
import io
import json
import importlib
import contextlib

from tokeo.pact.ai.data import ToolResult, ToolStates
from tokeo.pact.ai.tool import create_tool_result

with open('/io/task.json') as f:
    task = json.load(f)


def _load(dotted, options):
    module_path, _, attr = dotted.rpartition('.')
    cls = getattr(importlib.import_module(module_path), attr)
    tool = cls(None, **(options or {}))
    tool._setup(None)
    return tool


def _encode(output):
    # the same reply shape the parent rebuilds from: a value crosses as its two
    # string views, or null when nothing was delivered -- as_data is NOT sent,
    # the parent reconstructs it from as_json
    value = output.value
    value_doc = None if value is None else dict(as_str=value.as_str, as_json=value.as_json)
    state = output.state
    state_doc = dict(
        incomplete=state.incomplete,
        stdout=state.stdout,
        stderr=state.stderr,
        exception=state.exception,
    )
    return dict(value=value_doc, state=state_doc)


reply = {}
try:
    tool = _load(task['tool'], task.get('options'))
    # capture the tool's own stdout so a print() lands in state.stdout instead
    # of being lost, the same way the subprocess runner folds it in
    out_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf):
            output = tool.exec(**(task.get('arguments') or {}))
    except Exception as tool_err:
        # the tool itself raised (A): a result with no value and the message in
        # state.exception, NOT a machinery error -- the same split as in process
        output = ToolResult(value=None, state=ToolStates(
            exception='{}: {}'.format(type(tool_err).__name__, tool_err)))
    # a finished ToolResult passes through; a bare None carries no value; any
    # other value is wrapped -- so the result is always a ToolResult
    if output is None:
        output = ToolResult(value=None)
    elif not isinstance(output, ToolResult):
        output = create_tool_result(output)
    # fold the captured stdout into the state, but only where the tool left it
    # unset -- a stdout the tool set on purpose is its own note and wins
    if output.state.stdout is None and out_buf.getvalue():
        output.state.stdout = out_buf.getvalue()
    reply = _encode(output)
except Exception as err:
    # anything outside the tool call -- tool load, encode, json -- is a
    # machinery error (B): an error reply the host turns into a TokeoAiError
    reply = dict(error='{}: {}'.format(type(err).__name__, err))

with open('/io/reply.json', 'w') as f:
    json.dump(reply, f)
"""

# the exec-pysnippet guest entry (the untrusted path): it runs the code
# argument itself via run_snippet, rebuilding NO tool from tokeo, so the
# untrusted snippet stays walled off from the framework. it still imports the
# pact contract (dataclasses, create_tool_result, run_snippet -- no framework,
# no IO, no secrets), so it builds the same value/state result the exec-tool path
# does and delivers the snippet's value the same way, mirroring the untrusted
# tool body
_GUEST_ENTRY_EXEC_PYSNIPPET = r"""
import io
import json
import contextlib

from tokeo.pact.ai.data import ToolResult
from tokeo.pact.ai.tool import create_tool_result
from tokeo.pact.ai.pysnippet import run_snippet

with open('/io/task.json') as f:
    task = json.load(f)

reply = {}
try:
    code = (task.get('arguments') or {}).get('code') or ''
    namespace = {}
    # capture the snippet's stdout so a print() lands in state.stdout instead
    # of being lost, the same way the trusted path and the subprocess runner do
    out_buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(out_buf):
            result = run_snippet(code, namespace)
    except Exception as code_err:
        # the snippet is the problem -- it raised at run time, did not parse, or
        # used a form the wrap does not support: a result with no value and the
        # message in state.exception (A), NOT a machinery error
        output = ToolResult(value=None)
        output.state.exception = '{}: {}'.format(type(code_err).__name__, code_err)
    else:
        # a None result carries no value; any other value is wrapped the way
        # the framework would -- create_tool_result builds the coherent views
        output = ToolResult(value=None) if result is None else create_tool_result(result)
    if output.state.stdout is None and out_buf.getvalue():
        output.state.stdout = out_buf.getvalue()
    value = output.value
    value_doc = None if value is None else dict(as_str=value.as_str, as_json=value.as_json)
    state = output.state
    reply = dict(value=value_doc, state=dict(
        incomplete=state.incomplete,
        stdout=state.stdout,
        stderr=state.stderr,
        exception=state.exception,
    ))
except Exception as err:
    # anything outside the snippet run -- reading the task, encoding, json -- is
    # a machinery error (B): an error reply the host turns into a TokeoAiError
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
        # an untrusted tool flags itself for pysnippet exec: the guest runs the
        # code argument directly via run_snippet, without rebuilding the tool, so
        # no tokeo mount is needed and the framework stays invisible to the snippet
        is_exec_pysnippet = getattr(tool, 'wasm_exec_pysnippet', False)
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
                f.write(_GUEST_ENTRY_EXEC_PYSNIPPET if is_exec_pysnippet else _GUEST_ENTRY_EXEC_TOOL)
            self._run_guest(wasmtime, io_dir, dotted)
            reply_path = os.path.join(io_dir, 'reply.json')
            if not os.path.exists(reply_path):
                raise TokeoAiError(f'tool {dotted!r} produced no result in the wasm sandbox')
            with open(reply_path) as f:
                reply = json.load(f)
        if 'error' in reply:
            raise TokeoAiError(f'tool {dotted!r} failed in the wasm sandbox: {reply["error"]}')
        return self._rebuild(reply)

    def _rebuild(self, reply):
        # turn the guest's JSON back into a ToolResult: a null value stays None
        # (the tool delivered nothing), otherwise the two string views cross and
        # as_data is reconstructed from as_json -- only the JSON view of a value
        # survives the bridge, so a raw datetime returns as its encoded form;
        # the run states are carried across as recorded
        value_doc = reply.get('value')
        if value_doc is None:
            value = None
        else:
            as_json = value_doc.get('as_json', '')
            value = ToolValue(
                as_str=value_doc.get('as_str', ''),
                as_json=as_json,
                as_data=json.loads(as_json) if as_json else None,
            )
        state_doc = reply.get('state') or {}
        state = ToolStates(
            incomplete=state_doc.get('incomplete', False),
            stdout=state_doc.get('stdout'),
            stderr=state_doc.get('stderr'),
            exception=state_doc.get('exception'),
        )
        return ToolResult(value=value, state=state)

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
        # the pact contract, made importable as the dotted tokeo.pact.* the
        # guest entry expects. a leaf mount at /lib/tokeo/pact/{name} cannot
        # work: /lib is the stdlib mount and does not list a tokeo child, so the
        # dotted import fails at its first step. instead a writable package root
        # /pkg is given the REAL namespace chain tokeo/pact (plain dirs, no
        # __init__ -- PEP 420) with an empty stub per subpackage as the mount
        # point, then each real subpackage is mounted OVER its stub. pact is a
        # split namespace (utils from tokeo, ai from fundi), each name single-
        # origin, so a name is mounted once. pact is dependency-free -- no
        # framework, no IO, no secrets -- so it is carried for BOTH paths, the
        # untrusted one too: seeing it cannot weaken the isolation
        import tokeo.pact

        pkg_host = os.path.join(io_dir, 'pkg')
        pact_host = os.path.join(pkg_host, 'tokeo', 'pact')
        os.makedirs(pact_host, exist_ok=True)
        seen = set()
        pact_mounts = []
        for origin in tokeo.pact.__path__:
            if not os.path.isdir(origin):
                continue
            for name in sorted(os.listdir(origin)):
                child = os.path.join(origin, name)
                if name.startswith(('_', '.')) or not os.path.isdir(child) or name in seen:
                    continue
                seen.add(name)
                # the empty stub is the mount point the parent listing shows;
                # the real subpackage is mounted over it to supply the content
                os.makedirs(os.path.join(pact_host, name), exist_ok=True)
                pact_mounts.append((child, f'/pkg/tokeo/pact/{name}'))
        # the staging root carries the namespace chain read-only; untrusted code
        # gets no write to the contract even though the stubs live under io_dir
        mount(pkg_host, '/pkg', False, 'pact package root')
        for child, guest_path in pact_mounts:
            mount(child, guest_path, False, f'pact {os.path.basename(guest_path)!r}')
        # the bundled wasi stdlib shims: ours, like pact, so they live under the
        # /pkg root rather than inside the user's /lib stdlib. mounted read-only
        # at /pkg/shims and kept FIRST on PYTHONPATH so they stand in for the
        # absent multiprocessing/threading modules a rebuilt framework imports.
        # a leaf mount needs no stub here: /pkg/shims is itself a path root, so
        # the import lists it directly and never traverses /pkg to find it
        shim_path = None
        if self._config('shim_wasi_stdlib'):
            shim_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wasi_shims')
            mount(shim_path, '/pkg/shims', False, 'wasi shims')
        # the scrubbed env: only listed keys survive, expanded like elsewhere.
        # PYTHONPATH/PYTHONHOME point the interpreter at the stdlib mounted at
        # /lib so it can boot (find encodings etc); the shims lead, then /lib,
        # then /pkg so the dotted tokeo.pact contract resolves, then a user
        # PYTHONPATH so mounted tool code is importable too
        env = expand_env(self._config('env'))
        user_pythonpath = env.get('PYTHONPATH')
        parts = (['/pkg/shims'] if shim_path else []) + ['/lib', '/pkg']
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
