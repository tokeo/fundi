"""
Tests for the wasm sandbox and the python exec tools.

The enforcement mechanics (hard memory cap, epoch timeout) are proven with
tiny WAT guests so they run anywhere wasmtime is installed. The real
end-to-end path (the exec tools across the file bridge in a CPython-WASI guest)
needs a user-supplied python.wasm and is skipped without one -- set
TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB to run it.
"""

import os
import pytest

from cement.utils.misc import init_defaults

from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.sandboxes.wasm import TokeoAiWasmSandbox
from tokeo.core.ai.tools.python_untrusted_exec import TokeoAiPythonUntrustedExecTool
from tokeo.core.ai.tools.python_trusted_exec import TokeoAiPythonTrustedExecTool

wasmtime = pytest.importorskip('wasmtime')


# a guest that grows memory forever, trapping once the store limit refuses
_GROW_WAT = r"""(module (memory (export "memory") 1)
  (func (export "_start")
    (loop $g (if (i32.eq (memory.grow (i32.const 1)) (i32.const -1))
      (then unreachable)) (br $g))))"""

# a guest that spins forever, interrupted only by an epoch tick
_SPIN_WAT = r"""(module (memory (export "memory") 1)
  (func (export "_start") (loop $s (br $s))))"""


def _run_wat(wat, memory_mb=None, timeout=None):
    config = wasmtime.Config()
    if timeout:
        config.epoch_interruption = True
    engine = wasmtime.Engine(config)
    store = wasmtime.Store(engine)
    if memory_mb:
        store.set_limits(memory_size=memory_mb * 1024 * 1024)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    store.set_wasi(wasmtime.WasiConfig())
    module = wasmtime.Module(engine, wat)
    instance = linker.instantiate(store, module)
    start = instance.exports(store)['_start']
    if timeout:
        store.set_epoch_deadline(1)
        import threading

        ticker = threading.Timer(timeout, engine.increment_epoch)
        ticker.daemon = True
        ticker.start()
    start(store)


def test_wasm_memory_cap_is_hard():
    # the store limit makes the guest trap instead of eating host memory --
    # platform-independent, the gap the subprocess sandbox has on macos
    with pytest.raises(wasmtime.Trap):
        _run_wat(_GROW_WAT, memory_mb=8)


def test_wasm_timeout_interrupts_a_spin():
    # an endless loop is stopped by the epoch tick, in-process, no child kill
    with pytest.raises(wasmtime.Trap):
        _run_wat(_SPIN_WAT, timeout=1)


# a guest that calls proc_exit(N): proves the exit-status handling the sandbox
# relies on (status 0 = clean run, nonzero = failure) without a real build
def _exit_wat(code):
    return (
        '(module '
        '(import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32))) '
        '(memory (export "memory") 1) '
        f'(func (export "_start") (call $exit (i32.const {code}))))'
    )


def test_wasm_exit_status_zero_is_a_clean_run():
    # the guest interpreter exits via proc_exit; status 0 carries no failure
    with pytest.raises(wasmtime.ExitTrap) as info:
        _run_wat(_exit_wat(0))
    assert getattr(info.value, 'code', None) == 0


def test_wasm_exit_status_nonzero_is_a_failure():
    # a nonzero status is what the sandbox turns into a TokeoAiError
    with pytest.raises(wasmtime.ExitTrap) as info:
        _run_wat(_exit_wat(1))
    assert getattr(info.value, 'code', None) == 1


def test_wasm_needs_a_runtime():
    # without a runtime/stdlib the sandbox refuses with a clear reason rather
    # than silently doing nothing
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._setup(None)
    tool = TokeoAiPythonUntrustedExecTool(None)
    with pytest.raises(TokeoAiError, match='runtime'):
        sandbox.exec(tool, dict(code='result = 1'))


def test_wasm_validate_options_rejects_unknown():
    sandbox = TokeoAiWasmSandbox(None)
    issues = sandbox.validate_options(dict(runtime='x', net=True))
    assert issues and any('net' in m for m in issues)


def test_wasm_missing_mount_path_names_the_path(tmp):
    # a mount whose host path does not exist must fail with a clear message
    # naming the path, not a bare wasmtime "failed to add preopen dir". the
    # repo tmp fixture is used (not pytest's tmp_path) so the suite's
    # tmp/tests/.gitkeep is not wiped by pytest's basetemp management
    runtime = os.path.join(tmp.dir, 'python.wasm')
    with open(runtime, 'wb') as f:
        f.write(b'\x00asm')
    stdlib = os.path.join(tmp.dir, 'lib')
    os.makedirs(stdlib)
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._declaration = dict(
        options=dict(
            runtime=runtime,
            stdlib=stdlib,
            mounts={'/app': '/no/such/host/path'},
        )
    )
    sandbox._setup(None)
    tool = TokeoAiPythonUntrustedExecTool(None)
    with pytest.raises(TokeoAiError, match=r'/no/such/host/path'):
        sandbox.exec(tool, dict(code='result = 1'))


def test_untrusted_exec_runs_in_process():
    # the tool logic itself (compile/exec, result -> text) is sandbox-agnostic
    tool = TokeoAiPythonUntrustedExecTool(None)
    result = tool.exec(code='result = sum(range(10))')
    assert result.text == '45' and result.data == 45


def test_untrusted_exec_sets_the_direct_flag():
    # the flag is what tells the wasm sandbox to skip tool rebuild in the guest
    assert TokeoAiPythonUntrustedExecTool(None).wasm_direct_exec is True
    assert getattr(TokeoAiPythonTrustedExecTool(None), 'wasm_direct_exec', False) is False


def test_trusted_exec_runs_in_process():
    tool = TokeoAiPythonTrustedExecTool(None)
    result = tool.exec(code='result = sum(range(10))')
    assert result.text == '45' and result.data == 45


def test_untrusted_exec_non_json_result_keeps_text_only():
    tool = TokeoAiPythonUntrustedExecTool(None)
    result = tool.exec(code='result = object()')
    assert result.text.startswith('<object') and result.data is None


_PYTHON_WASM = os.environ.get('TOKEO_TEST_PYTHON_WASM')
_WASI_STDLIB = os.environ.get('TOKEO_TEST_WASI_STDLIB')

# the build paths default to the documented relative spot; override via env
_RUNTIME = _PYTHON_WASM or os.path.abspath('./wasm/python.wasm')
_STDLIB = _WASI_STDLIB or os.path.abspath('./wasm/lib/python3.13')

# the tokeo source roots, mounted into the guest for the TRUSTED tool so it can
# be rebuilt there (the untrusted tool needs no such mount). tokeo is now a
# PEP 420 namespace split across two repos (the fundi tree holds
# tokeo.core.ai..., the tokeo tree holds tokeo.core.exc...), so both portions
# must be mounted for `import tokeo` to merge in the guest as it does on the host
import tokeo  # noqa: E402

_TOKEO_ROOTS = sorted({os.path.dirname(os.path.abspath(p)) for p in tokeo.__path__})

# the trusted tool's base class pulls in cement, which lives in site-packages
# (a different tree than tokeo) -- the trusted guest must mount that too. find
# it from the installed package so the test works on any machine
import cement  # noqa: E402

_DEPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(cement.__file__)))

# skip the build-dependent tests unless an actual build is present
_have_build = os.path.exists(_RUNTIME) and os.path.isdir(_STDLIB)


class WasmTest(TokeoTest):
    """A test app with just the ai extension, like the fundi harness."""

    class Meta:
        """Load the extensions the ai handler needs."""

        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def wasm_ai_config():
    # a self-contained ai config with BOTH built-in tools, each behind a wasm
    # sandbox: the untrusted tool runs directly in the guest (no mount), the
    # trusted tool is rebuilt in the guest, so its sandbox mounts the tokeo
    # source read-only at /app and adds it to PYTHONPATH
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='mock', agent=None),
        tools=dict(
            run_untrusted=dict(type='tokeo.core.ai.tools.TokeoAiPythonUntrustedExecTool'),
            run_trusted=dict(type='tokeo.core.ai.tools.TokeoAiPythonTrustedExecTool'),
        ),
        sandboxes=dict(
            # untrusted: total isolation, the guest sees only its stdlib
            wasm_untrusted=dict(
                type='tokeo.core.ai.sandboxes.TokeoAiWasmSandbox',
                tools=['run_untrusted'],
                options=dict(runtime=_RUNTIME, stdlib=_STDLIB, memory_mb=256, timeout=10),
            ),
            # trusted: the app is mounted so the tool can be rebuilt in the guest
            wasm_trusted=dict(
                type='tokeo.core.ai.sandboxes.TokeoAiWasmSandbox',
                tools=['run_trusted'],
                options=dict(
                    runtime=_RUNTIME,
                    stdlib=_STDLIB,
                    memory_mb=256,
                    timeout=10,
                    # the tool's base class needs tokeo, tokeo-fundi AND cement (a
                    # separate site-packages tree), so mount both read-only
                    mounts={
                        **{f'/app{i}': root for i, root in enumerate(_TOKEO_ROOTS)},
                        '/deps': _DEPS_ROOT,
                    },
                    env=dict(
                        PYTHONPATH=':'.join(
                            # fmt: skip
                            [f'/app{i}' for i in range(len(_TOKEO_ROOTS))]
                            + ['/deps']
                        )
                    ),
                ),
            ),
        ),
        agents=dict(
            untrusted_coder=dict(type='fundi', options=dict(sandboxes=['wasm_untrusted'])),
            trusted_coder=dict(type='fundi', options=dict(sandboxes=['wasm_trusted'])),
        ),
        profiles=dict(
            mock=dict(type='mock', agent='untrusted_coder'),
        ),
    )
    return cfg


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_untrusted_exec_through_the_agent_chain():
    # the untrusted tool runs the snippet directly in the guest (no tokeo
    # mount) and the result crosses the file bridge back
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='import statistics\nresult = statistics.median([5, 1, 9, 3, 7])'),
            agent,
        )
        # value is None when the tool returned nothing, so guard the access
        text = out.value.as_str if out.value else ''
        assert text == '5'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_trusted_exec_through_the_agent_chain():
    # the trusted tool is rebuilt in the guest from the mounted tokeo source;
    # proving the snippet can import tokeo confirms the app is reachable there
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('trusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_trusted',
            dict(code='import tokeo\nresult = bool(tokeo.__name__)'),
            agent,
        )
        # value is None when the tool returned nothing, so guard the access
        text = out.value.as_str if out.value else ''
        assert text == 'True'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_untrusted_guest_cannot_import_tokeo():
    # the untrusted path mounts no app: importing tokeo must fail in the guest,
    # proving the framework stays invisible to untrusted code
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        with pytest.raises(TokeoAiError, match='ModuleNotFoundError'):
            app.ai._exec_in_sandbox(
                'run_untrusted',
                dict(code='import tokeo\nresult = 1'),
                agent,
            )


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_wasm_guest_has_no_network():
    # the guest must not be able to open a socket -- the syscalls do not exist
    # in its world; the snippet's failure surfaces as a tool error
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        with pytest.raises(TokeoAiError):
            app.ai._exec_in_sandbox(
                'run_untrusted',
                dict(code='import socket\nresult = socket.gethostbyname("example.com")'),
                agent,
            )
