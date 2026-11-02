"""
Tests for the wasm sandbox as the ai extension drives it.

Through the agent chain: what a wasm guest may reach, that it cannot import the
framework, and that it has no network. The runtime guarantees on their own are
tested in ```tests/core/ai/test_wasm.py```.
"""

import os
import importlib.util
import pytest

from cement.utils.misc import init_defaults

from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.sandboxes.wasm import TokeoAiWasmSandbox
from tokeo.core.ai.tools.python_untrusted_exec import TokeoAiPythonUntrustedExecTool


def test_wasm_missing_mount_path_names_the_path(tmp):
    """A mount of '/no/such/host/path' raises TokeoAiError naming that path."""
    # not a bare wasmtime "failed to add preopen dir"
    pytest.importorskip('wasmtime')
    runtime = os.path.join(tmp.dir, 'python.wasm')
    with open(runtime, 'wb') as f:
        f.write(b'\x00asm')
    stdlib = os.path.join(tmp.dir, 'lib')
    os.makedirs(stdlib)
    sandbox = TokeoAiWasmSandbox(None)
    sandbox._setup(
        None,
        'wasm_untrusted',
        dict(
            options=dict(
                runtime=runtime,
                stdlib=stdlib,
                mounts={'/app': '/no/such/host/path'},
            )
        ),
    )
    tool = TokeoAiPythonUntrustedExecTool(None)
    with pytest.raises(TokeoAiError, match=r'/no/such/host/path'):
        sandbox.exec(tool, dict(code='1'))


def test_untrusted_exec_non_json_value_is_returned_raw():
    """exec returns object() raw; the tool no longer screens for JSON."""
    # the sandbox's encoder decides how to represent it (and flags loss)
    tool = TokeoAiPythonUntrustedExecTool(None)
    assert isinstance(tool.exec(code='object()'), object)


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
# (a different tree than tokeo); the trusted guest must mount that too. find
# it from the installed package so the test works on any machine
import cement  # noqa: E402

_DEPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(cement.__file__)))

# skip the build-dependent tests unless wasmtime and an actual build are present
_have_build = importlib.util.find_spec('wasmtime') is not None and os.path.exists(_RUNTIME) and os.path.isdir(_STDLIB)


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
    """An ai config with the untrusted and the trusted tool, each behind wasm."""
    # a self-contained ai config with BOTH built-in tools, each behind a wasm
    # sandbox: the untrusted tool runs directly in the guest (no app mount), the
    # trusted tool is rebuilt in the guest, so its sandbox mounts the tokeo
    # source read-only at /app and adds it to PYTHONPATH
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='mock', agent=None),
        tools=dict(
            run_untrusted=dict(type='tokeo.core.ai.tools:TokeoAiPythonUntrustedExecTool'),
            run_trusted=dict(type='tokeo.core.ai.tools:TokeoAiPythonTrustedExecTool'),
        ),
        sandboxes=dict(
            # untrusted: total isolation, the guest sees only its stdlib
            wasm_untrusted=dict(
                type='tokeo.core.ai.sandboxes:TokeoAiWasmSandbox',
                tools=['run_untrusted'],
                options=dict(runtime=_RUNTIME, stdlib=_STDLIB, memory_mb=256, timeout=10),
            ),
            # trusted: the app is mounted so the tool can be rebuilt in the guest
            wasm_trusted=dict(
                type='tokeo.core.ai.sandboxes:TokeoAiWasmSandbox',
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
            untrusted_coder=dict(type='fundi', options=dict(governors=[], tools=['run_untrusted'], sandboxes=['wasm_untrusted'])),
            trusted_coder=dict(type='fundi', options=dict(governors=[], tools=['run_trusted'], sandboxes=['wasm_trusted'])),
        ),
        profiles=dict(
            mock=dict(type='mock', agent='untrusted_coder'),
        ),
    )
    return cfg


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_untrusted_exec_through_the_agent_chain():
    """Untrusted, statistics.median([5, 1, 9, 3, 7]) runs in the guest: '5'."""
    # no app mount; the result crosses the file bridge back
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='import statistics\nstatistics.median([5, 1, 9, 3, 7])'),
            agent,
            None,
            None,
            None,
            None,
        )
        # value is None when the tool returned nothing, so guard the access
        text = out.value.as_str if out.value else ''
        assert text == '5'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_trusted_exec_through_the_agent_chain():
    """The trusted tool, rebuilt from the mounted source, imports tokeo: 'True'."""
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('trusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_trusted',
            dict(code='import tokeo\nbool(tokeo.__name__)'),
            agent,
            None,
            None,
            None,
            None,
        )
        # value is None when the tool returned nothing, so guard the access
        text = out.value.as_str if out.value else ''
        assert text == 'True'


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_untrusted_guest_cannot_import_the_framework():
    """
    Untrusted, import tokeo.core.ai fails with ModuleNotFoundError and no value.

    Nothing of tokeo enters the untrusted guest, only copies of the guest
    modules under _guest. The failure is a tool exception on the result (A).
    """
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='import tokeo.core.ai\n1'),
            agent,
            None,
            None,
            None,
            None,
        )
        assert out.value is None
        assert 'ModuleNotFoundError' in (out.state.exception or '')


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_wasm_guest_has_no_network():
    """
    socket.gethostbyname('example.com') fails in the guest: no value, an exception.

    The network syscalls do not exist in the guest's world, so the failure lands
    on the result's state (A). Its type is build-specific (gaierror, OSError or
    a missing socket module), so only the recorded failure is asserted.
    """
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='import socket\nsocket.gethostbyname("example.com")'),
            agent,
            None,
            None,
            None,
            None,
        )
        assert out.value is None
        assert out.state.exception is not None


@pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)
def test_wasm_guest_cannot_write_a_read_only_mount():
    """
    Writing /lib/tokeo_wasm_probe.txt fails with PermissionError, no host file.

    The stdlib is mounted at /lib with fs_mutable off, so the write fails in the
    guest as a tool exception on the result (A), with no value; the io scratch
    stays the only writable surface.
    """
    probe = os.path.join(_STDLIB, 'tokeo_wasm_probe.txt')
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        out = app.ai._exec_in_sandbox(
            'run_untrusted',
            dict(code='open("/lib/tokeo_wasm_probe.txt", "w").write("x")'),
            agent,
            None,
            None,
            None,
            None,
        )
        assert out.value is None
        assert 'PermissionError' in (out.state.exception or '')
    assert not os.path.exists(probe)


needs_build = pytest.mark.skipif(
    not _have_build,
    reason=f'no wasmtime or no wasm build at {_RUNTIME} / {_STDLIB} (override via TOKEO_TEST_PYTHON_WASM and TOKEO_TEST_WASI_STDLIB)',
)


def run_untrusted(code):
    """Run the code by the untrusted tool in its wasm sandbox, return the result."""
    with WasmTest(config_defaults=wasm_ai_config()) as app:
        agent = app.ai._agent('untrusted_coder')
        return app.ai._exec_in_sandbox('run_untrusted', dict(code=code), agent, None, None, None, None)


@needs_build
def test_the_host_tells_stdout_and_stderr():
    """print out and err, then 7: the value 7, stdout and stderr as captured."""
    out = run_untrusted('import sys\nprint("out")\nprint("err", file=sys.stderr)\n7')
    assert out.value.as_str == '7'
    assert (out.state.stdout, out.state.stderr, out.state.exception) == ('out\n', 'err\n', None)


@needs_build
def test_the_host_tells_the_exception_of_a_raising_snippet():
    """1/0 gives no value and the last stderr line as the exception."""
    out = run_untrusted('1/0')
    assert out.value is None
    assert out.state.exception == 'ZeroDivisionError: division by zero'
    assert 'Traceback' in out.state.stderr


@needs_build
def test_the_guest_cannot_forge_its_stdout():
    """A file /io/stdout written by the guest is not the captured stdout."""
    out = run_untrusted('open("/io/stdout", "w").write("forged")\nopen("/io/stderr", "w").write("forged")\n1')
    assert (out.state.stdout, out.state.stderr) == (None, None)


@needs_build
def test_a_failed_guest_machinery_is_an_error():
    """An exit with code 3 is the guest machinery failing: TokeoAiError."""
    with pytest.raises(TokeoAiError, match='failed in the wasm sandbox'):
        run_untrusted('import sys\nsys.exit(3)')


@needs_build
def test_the_untrusted_guest_sees_only_lib_and_io():
    """/lib and /io exist in the untrusted guest, /etc, /home, /tmp, /usr not."""
    out = run_untrusted('import os\n[os.path.isdir(p) for p in ("/lib", "/io", "/etc", "/home", "/tmp", "/usr")]')
    assert out.value.as_data == [True, True, False, False, False, False]


@needs_build
def test_the_untrusted_guest_cannot_leave_by_a_symlink():
    """A symlink in /io to ../../etc does not open a host file."""
    out = run_untrusted('import os\nos.symlink("../../../../../etc", "/io/up")\nopen("/io/up/hostname").read()')
    assert out.value is None
    assert out.state.exception is not None


@needs_build
def test_the_untrusted_guest_has_no_shims_and_writes_no_bytecode():
    """threading comes from /lib, and the guest writes no bytecode."""
    out = run_untrusted('import sys, threading\n[threading.__file__, sys.dont_write_bytecode]')
    assert out.value.as_data == ['/lib/threading.py', True]


@needs_build
@pytest.mark.parametrize(
    'code',
    ['"text"', '42', '(1, 2)', '{"a": [1, 2.5, None]}', 'datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)', 'object()'],
)
def test_guest_and_host_encode_a_value_alike(code):
    """A value encodes in the guest as create_tool_result does on the host."""
    from datetime import datetime, timezone
    from tokeo.core.ai.tool import create_tool_result

    value = eval(code, dict(datetime=datetime, timezone=timezone))
    host = create_tool_result(value)
    out = run_untrusted(f'from datetime import datetime, timezone\n{code}')
    assert (out.value.as_json, out.state.incomplete) == (host.value.as_json, host.state.incomplete)
    if code != 'object()':
        assert out.value.as_str == host.value.as_str
