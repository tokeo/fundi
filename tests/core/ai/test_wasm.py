"""
Tests for the wasm sandbox itself.

The runtime guarantees -- memory cap, timeout, exit status, option validation --
and the in-process exec paths, driven directly without an app. Runs through the
agent chain live in ```tests/ext/ai/test_ai_wasm.py```.
"""

import pytest


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
        sandbox.exec(tool, dict(code='1'))


def test_wasm_validate_options_rejects_unknown():
    sandbox = TokeoAiWasmSandbox(None)
    issues = sandbox.validate_options(dict(runtime='x', net=True))
    assert issues and any('net' in m for m in issues)


def test_untrusted_exec_runs_in_process():
    # the tool returns the snippet's raw value now -- the sandbox layer wraps it
    # into a ToolResult, so exec itself is sandbox-agnostic and value-only
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='sum(range(10))') == 45


def test_untrusted_exec_sets_the_pysnippet_flag():
    # the flag is what tells the wasm sandbox to run the snippet via run_snippet
    # in the guest instead of rebuilding the tool
    assert TokeoAiPythonUntrustedExecTool(None).wasm_exec_pysnippet is True
    assert getattr(TokeoAiPythonTrustedExecTool(None), 'wasm_exec_pysnippet', False) is False


def test_trusted_exec_runs_in_process():
    tool = TokeoAiPythonTrustedExecTool(None)
    assert tool.exec(code='sum(range(10))') == 45


def test_untrusted_exec_delivers_by_return():
    # a return is the other delivery form: the wrap makes a top-level return
    # legal and hands its value back
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='return sum(range(10))') == 45


def test_untrusted_exec_without_a_value_returns_none():
    # a snippet that ends on a statement -- here a loop, not a value-bearing
    # line -- delivers nothing: the tool returns None, read as 'no value'
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='total = 0\nfor i in range(10):\n    total += i') is None
