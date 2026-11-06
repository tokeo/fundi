"""
Tests for the wasm sandbox itself.

The runtime guarantees (memory limit, timeout, exit status, option validation)
and the in-process exec paths, driven directly without an app. Runs through the
agent chain live in ```tests/ext/ai/test_wasm.py```.
"""

import os
import sys
import ast
import importlib.util
import pytest

from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.sandboxes import wasm
from tokeo.core.ai.sandboxes.wasm import TokeoAiWasmSandbox, WASM_GUEST_MODULE_COPIES
from tokeo.core.ai.tools.python_untrusted_exec import TokeoAiPythonUntrustedExecTool
from tokeo.core.ai.tools.python_trusted_exec import TokeoAiPythonTrustedExecTool

# a guest that grows memory forever, trapping once the store limit refuses
_GROW_WAT = r"""(module (memory (export "memory") 1)
  (func (export "_start")
    (loop $g (if (i32.eq (memory.grow (i32.const 1)) (i32.const -1))
      (then unreachable)) (br $g))))"""

# a guest that spins forever, interrupted only by an epoch tick
_SPIN_WAT = r"""(module (memory (export "memory") 1)
  (func (export "_start") (loop $s (br $s))))"""


def _run_wat(wat, memory_mb=None, timeout=None):
    wasmtime = pytest.importorskip('wasmtime')
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


def test_wasm_memory_limit_is_hard():
    """
    An 8 MB store limit makes a growing guest trap, not eat host memory.

    Platform-independent, the gap the subprocess sandbox has on macos.
    """
    wasmtime = pytest.importorskip('wasmtime')
    with pytest.raises(wasmtime.Trap):
        _run_wat(_GROW_WAT, memory_mb=8)


def test_wasm_timeout_interrupts_a_spin():
    """An endless loop traps on the epoch tick, in-process, no child kill."""
    wasmtime = pytest.importorskip('wasmtime')
    with pytest.raises(wasmtime.Trap):
        _run_wat(_SPIN_WAT, timeout=1)


# a guest that calls proc_exit(N): proves the exit-status handling the sandbox
# relies on (status 0 = clean run, nonzero = failure) without a real build
def _make_exit_wat(code):
    return (
        '(module '
        '(import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32))) '
        '(memory (export "memory") 1) '
        f'(func (export "_start") (call $exit (i32.const {code}))))'
    )


def test_wasm_exit_status_zero_is_a_clean_run():
    """A guest proc_exit(0) gives ExitTrap code 0, a run with no failure."""
    wasmtime = pytest.importorskip('wasmtime')
    with pytest.raises(wasmtime.ExitTrap) as info:
        _run_wat(_make_exit_wat(0))
    assert getattr(info.value, 'code', None) == 0


def test_wasm_exit_status_nonzero_is_a_failure():
    """proc_exit(1) gives ExitTrap code 1, made a TokeoAiError by the sandbox."""
    wasmtime = pytest.importorskip('wasmtime')
    with pytest.raises(wasmtime.ExitTrap) as info:
        _run_wat(_make_exit_wat(1))
    assert getattr(info.value, 'code', None) == 1


def test_wasm_needs_a_runtime():
    """Without runtime and stdlib exec raises TokeoAiError naming the runtime."""
    pytest.importorskip('wasmtime')
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._setup(None)
    tool = TokeoAiPythonUntrustedExecTool(None)
    with pytest.raises(TokeoAiError, match='runtime'):
        sandbox.exec(tool, dict(code='1'))


def test_wasm_without_wasmtime_names_the_extra(monkeypatch):
    """Without wasmtime exec raises naming pip install 'tokeo-fundi[wasm]'."""
    monkeypatch.setitem(sys.modules, 'wasmtime', None)
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._setup(None)
    with pytest.raises(TokeoAiError, match=r"pip install 'tokeo-fundi\[wasm\]'"):
        sandbox.exec(TokeoAiPythonUntrustedExecTool(None), dict(code='1'))


def test_untrusted_exec_runs_in_process():
    """
    The untrusted exec runs sum(range(10)) in process and returns raw 45.

    The sandbox layer wraps the value into a ToolResult, so exec itself is
    sandbox-agnostic and value-only.
    """
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='sum(range(10))') == 45


def test_untrusted_exec_sets_the_pysnippet_flag():
    """
    Only the untrusted exec tool sets wasm_exec_pysnippet.

    The flag tells the wasm sandbox to run the snippet via run_snippet in
    the guest instead of rebuilding the tool.
    """
    assert TokeoAiPythonUntrustedExecTool(None).wasm_exec_pysnippet is True
    assert getattr(TokeoAiPythonTrustedExecTool(None), 'wasm_exec_pysnippet', False) is False


def test_trusted_exec_runs_in_process():
    """The trusted exec runs sum(range(10)) in process and returns 45."""
    tool = TokeoAiPythonTrustedExecTool(None)
    assert tool.exec(code='sum(range(10))') == 45


def test_untrusted_exec_delivers_by_return():
    """A top-level return sum(range(10)) is legal and hands back 45."""
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='return sum(range(10))') == 45


def test_untrusted_exec_without_a_value_returns_none():
    """A snippet ending on a for loop delivers None, read as 'no value'."""
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert tool.exec(code='total = 0\nfor i in range(10):\n    total += i') is None


def test_guest_modules_import_only_the_stdlib_or_each_other():
    """Each module of WASM_GUEST_MODULE_COPIES imports the stdlib or another one."""
    names = {name.rpartition('.')[2] for name in WASM_GUEST_MODULE_COPIES}
    for name in WASM_GUEST_MODULE_COPIES:
        with open(importlib.util.find_spec(name).origin) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found = [alias.name.partition('.')[0] for alias in node.names]
                assert all(module in sys.stdlib_module_names for module in found), (name, found)
            elif isinstance(node, ast.ImportFrom) and node.level:
                found = [node.module] if node.module else [alias.name for alias in node.names]
                assert set(found) <= names, (name, found)
            elif isinstance(node, ast.ImportFrom):
                assert node.module.partition('.')[0] in sys.stdlib_module_names, (name, node.module)


def test_wasm_refuses_a_guest_module_without_source(tmp, monkeypatch):
    """A guest module no.such.module that does not exist aborts the run."""
    pytest.importorskip('wasmtime')
    monkeypatch.setattr(wasm, 'WASM_GUEST_MODULE_COPIES', ('no.such.module',))
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._setup(None, 'wasm', dict(options=dict(runtime=os.path.join(tmp.dir, 'python.wasm'), stdlib=tmp.dir)))
    with pytest.raises(TokeoAiError, match="guest module 'no.such.module' has no source file"):
        sandbox.exec(TokeoAiPythonUntrustedExecTool(None), dict(code='1'))
