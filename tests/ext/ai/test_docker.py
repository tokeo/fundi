"""
Tests for the docker sandbox.

A fake ```docker``` command stands in for the client: it logs its arguments,
answers ```run -d``` and ```kill```, and for a run or an exec starts the
generic runner locally with only the ```-e``` variables. So the tests see the
exact command line and a real tool result without a docker service. The live
test runs only with a reachable docker and ```TOKEO_TEST_DOCKER_IMAGE```.
"""

import os
import sys
import json
import shutil
import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.linter import TokeoAiLinter

ECHO = 'tests.ext.ai.lib.ai_tests_helper:EchoTool'
ENV = 'tests.ext.ai.lib.ai_tests_helper:EnvTool'
SLEEP = 'tests.ext.ai.lib.ai_tests_helper:SleepTool'
RAISE = 'tests.ext.ai.lib.ai_tests_helper:RaiseTool'
LOUD = 'tests.ext.ai.lib.ai_tests_helper:LoudTool'
BIG = 'tests.ext.ai.lib.ai_tests_helper:BigTool'
TOOLS = ['echo', 'env', 'sleep', 'boom', 'loud', 'big']
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

DOCKER_FAKE_SCRIPT = """#!{python}
import os, sys, json
args = sys.argv[1:]
with open({log!r}, 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[0] == 'kill':
    sys.exit(0)
if args[0] == 'run' and '-d' in args:
    fail = os.environ.get('FAKE_DOCKER_FAIL')
    sys.stderr.write(fail or '')
    sys.exit(1 if fail else 0)
env = dict(PYTHONPATH={root!r})
for i, arg in enumerate(args):
    if arg == '-e':
        key, _, value = args[i + 1].partition('=')
        env[key] = value
python = args.index('-m') - 1
os.chdir({root!r})
os.execve(sys.executable, [sys.executable] + args[python + 1:], env)
"""


class DockerTest(TokeoTest):
    """A test app with the ai extension and the extensions it needs."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


@pytest.fixture
def docker(tmp_path):
    """The path of a fake docker command; its log is calls()."""
    path = tmp_path / 'docker'
    path.write_text(DOCKER_FAKE_SCRIPT.format(python=sys.executable, log=str(tmp_path / 'calls.log'), root=ROOT))
    path.chmod(0o755)
    return str(path)


def calls(docker):
    """The argument lists the fake docker got, in order."""
    log = os.path.join(os.path.dirname(docker), 'calls.log')
    if not os.path.exists(log):
        return []
    with open(log) as f:
        return [json.loads(line) for line in f]


def ai_config(command, **options):
    """An ai config with the tools in the docker sandbox docked."""
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='mock', agent=None),
        tools=dict(
            echo=dict(type=ECHO),
            env=dict(type=ENV),
            sleep=dict(type=SLEEP),
            boom=dict(type=RAISE),
            loud=dict(type=LOUD),
            big=dict(type=BIG),
        ),
        sandboxes=dict(docked=dict(type='docker', tools=TOOLS, options=dict(image='app:test', command=command, **options))),
        agents=dict(contained=dict(type='fundi', options=dict(governors=[], tools=TOOLS, sandboxes=['docked']))),
        profiles=dict(mock=dict(type='mock', agent='contained')),
    )
    return cfg


def run(app, tool, arguments):
    """Run a tool through the agent contained."""
    return app.ai._exec_in_sandbox(tool, arguments, app.ai._agent('contained'), None, None, None, None)


def test_the_docker_sandbox_runs_a_tool_in_a_hardened_container(docker):
    """The runner runs in a throwaway container with every hardening flag."""
    with DockerTest(config_defaults=ai_config(docker)) as app:
        assert run(app, 'echo', dict(text='moin')).value.as_str == 'moin'
    [args] = calls(docker)
    assert args[:5] == ['run', '--rm', '-i', '--name', args[4]]
    assert args[4].startswith(f'tokeo-ai-docked-{os.getpid()}-')
    assert args[5:] == [
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--network', 'none',
        '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m',
        '--memory', '512m', '--pids-limit', '128', '--user', '65534:65534',
        '--entrypoint', 'python3', 'app:test', '-m', 'tokeo.core.ai.sandboxes.runner',
    ]  # fmt: skip


def test_the_docker_sandbox_mounts_read_only_and_passes_only_its_env(docker, monkeypatch):
    """A mount is :ro and absolute, ${NAME} expands, other variables stay out."""
    monkeypatch.setenv('DOCKER_TEST_HOST', 'from host')
    monkeypatch.setenv('DOCKER_TEST_OTHER', 'secret')
    cfg = ai_config(docker, mounts={'/data': '/srv/data', '/conf': 'conf'}, env=dict(GREETING='${DOCKER_TEST_HOST}'))
    with DockerTest(config_defaults=cfg) as app:
        assert run(app, 'env', dict(name='GREETING')).value.as_str == 'from host'
        assert run(app, 'env', dict(name='DOCKER_TEST_OTHER')).value.as_str == '<unset>'
    args = calls(docker)[0]
    mounts = [args[i + 1] for i, arg in enumerate(args) if arg == '-v']
    assert mounts == ['/srv/data:/data:ro', f'{os.path.abspath("conf")}:/conf:ro']
    assert args[args.index('-e') + 1] == 'GREETING=from host'


def test_the_docker_sandbox_takes_its_limits_from_the_options(docker):
    """network, read_only, memory_mb, pids and user follow the options."""
    cfg = ai_config(docker, network='bridge', read_only=False, memory_mb=0, pids=0, user='1000:1000', python='python3.13')
    with DockerTest(config_defaults=cfg) as app:
        run(app, 'echo', dict(text='x'))
    args = calls(docker)[0]
    assert args[args.index('--network') + 1] == 'bridge'
    assert args[args.index('--user') + 1] == '1000:1000'
    assert '--read-only' not in args and '--memory' not in args and '--pids-limit' not in args
    assert args[-5:] == ['--entrypoint', 'python3.13', 'app:test', '-m', 'tokeo.core.ai.sandboxes.runner']
    assert '--privileged' not in args


def test_disk_mb_sizes_the_tmpfs_of_a_read_only_container(docker):
    """disk_mb 16 makes /tmp a tmpfs of 16m."""
    with DockerTest(config_defaults=ai_config(docker, disk_mb=16)) as app:
        run(app, 'echo', dict(text='x'))
    args = calls(docker)[0]
    assert args[args.index('--tmpfs') + 1] == '/tmp:rw,noexec,nosuid,size=16m'


def test_the_docker_sandbox_cuts_the_output_and_refuses_a_large_value(docker):
    """output_mb 0.001: 5000 bytes printed are cut, a value of 5000 bytes fails."""
    with DockerTest(config_defaults=ai_config(docker, output_mb=0.001)) as app:
        out = run(app, 'loud', dict(size=5000))
        assert out.state.stdout == 'x' * 1048 + '\n[... cut after 0.001 MB]'
        with pytest.raises(TokeoAiError, match='delivered more than 0.001 MB in the docker sandbox'):
            run(app, 'big', dict(size=5000))


def test_a_raising_tool_in_the_docker_sandbox_is_a_tool_error(docker):
    """The runner catches the raise; the result carries state.exception."""
    with DockerTest(config_defaults=ai_config(docker)) as app:
        out = run(app, 'boom', {})
    assert out.value is None
    assert 'tool failed on purpose' in out.state.exception


def test_a_timeout_in_the_docker_sandbox_raises_and_kills_the_container(docker):
    """A call over the timeout raises TokeoAiError and kills its container."""
    with DockerTest(config_defaults=ai_config(docker, timeout=1)) as app:
        with pytest.raises(TokeoAiError, match='timed out after 1s in the docker sandbox'):
            run(app, 'sleep', dict(seconds=5))
    started, killed = calls(docker)
    assert killed == ['kill', started[4]]


def test_reuse_runs_each_call_in_one_container_and_close_removes_it(docker):
    """reuse starts one container, execs each call in it, close kills it."""
    with DockerTest(config_defaults=ai_config(docker, reuse=True)) as app:
        assert run(app, 'echo', dict(text='a')).value.as_str == 'a'
        assert run(app, 'echo', dict(text='b')).value.as_str == 'b'
        app.ai._sandbox('docked').close()
    start, first, second, kill = calls(docker)
    name = start[start.index('--name') + 1]
    assert start[:3] == ['run', '--rm', '-d']
    assert start[-5:] == ['--entrypoint', 'python3', 'app:test', '-c', 'import signal; signal.pause()']
    assert '--cap-drop' in start and '--network' in start
    assert first == second == ['exec', '-i', name, 'python3', '-m', 'tokeo.core.ai.sandboxes.runner']
    assert kill == ['kill', name]


def test_reuse_starts_a_new_container_after_a_timeout(docker):
    """A timeout removes the reused container; the next call starts a new one."""
    with DockerTest(config_defaults=ai_config(docker, reuse=True, timeout=1)) as app:
        with pytest.raises(TokeoAiError, match='timed out'):
            run(app, 'sleep', dict(seconds=5))
        assert run(app, 'echo', dict(text='again')).value.as_str == 'again'
        app.ai._sandbox('docked').close()
    log = calls(docker)
    starts = [args[args.index('--name') + 1] for args in log if args[0] == 'run']
    assert len(starts) == 2 and starts[0] != starts[1]
    assert [args for args in log if args[0] == 'kill'] == [['kill', starts[0]], ['kill', starts[1]]]


def test_reuse_reports_a_container_that_does_not_start(docker, monkeypatch):
    """A failed run -d raises with the client's message; nothing is kept."""
    monkeypatch.setenv('FAKE_DOCKER_FAIL', 'no such image')
    with DockerTest(config_defaults=ai_config(docker, reuse=True)) as app:
        with pytest.raises(TokeoAiError, match='cannot start its container: no such image'):
            run(app, 'echo', dict(text='x'))
        assert app.ai._sandbox('docked')._container is None


def test_a_missing_docker_command_is_a_machinery_error(tmp_path):
    """A command that does not exist raises 'cannot run'."""
    with DockerTest(config_defaults=ai_config(str(tmp_path / 'nodocker'))) as app:
        with pytest.raises(TokeoAiError, match='cannot run'):
            run(app, 'echo', dict(text='x'))


def test_the_docker_sandbox_needs_an_image(docker):
    """Without an image the call raises before any client runs."""
    cfg = ai_config(docker)
    cfg['ai']['sandboxes']['docked']['options']['image'] = None
    with DockerTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        with pytest.raises(TokeoAiError, match='needs an image'):
            run(app, 'echo', dict(text='x'))
    assert calls(docker) == []


@pytest.mark.parametrize(
    'options, path, message',
    [
        (dict(), None, None),
        (dict(image=None), 'ai.sandboxes.docked.options', 'image is required'),
        (dict(user='root'), 'ai.sandboxes.docked.options', 'user must not be root'),
        (dict(user='0:0'), 'ai.sandboxes.docked.options', 'user must not be root'),
        (dict(privileged=True), 'ai.sandboxes.docked.options.privileged', "unknown option 'privileged'"),
        (dict(disk_mb=0), 'ai.sandboxes.docked.options', 'disk_mb must be a whole number'),
        (dict(disk_mb=1.5), 'ai.sandboxes.docked.options', 'disk_mb must be a whole number'),
        (dict(output_mb=0), 'ai.sandboxes.docked.options', 'output_mb must be a number of MB above 0'),
    ],
)
def test_the_linter_checks_the_docker_options(options, path, message):
    """Clean options lint clean; no image, root or an unknown key is an error."""
    cfg = ai_config('docker')
    cfg['ai']['sandboxes']['docked']['options'].update(options)
    with DockerTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        errors = {i.path: i.message for i in TokeoAiLinter(app).lint() if i.level == 'error'}
    if path is None:
        assert errors == {}
    else:
        assert errors[path].startswith(message)


def test_the_linter_reports_a_missing_image_without_an_options_block():
    """Without options the block is {}, so the missing image is a lint error."""
    cfg = ai_config('docker')
    del cfg['ai']['sandboxes']['docked']['options']
    with DockerTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        errors = {i.path: i.message for i in TokeoAiLinter(app).lint() if i.level == 'error'}
    assert errors == {'ai.sandboxes.docked.options': 'image is required'}


@pytest.mark.skipif(
    not (shutil.which('docker') and os.environ.get('TOKEO_TEST_DOCKER_IMAGE')),
    reason='needs docker and TOKEO_TEST_DOCKER_IMAGE (an image with tokeo, fundi and the tests)',
)
def test_live_the_docker_sandbox_runs_a_tool_in_a_real_container():
    """With a real docker the echo tool answers from a container."""
    cfg = ai_config('docker')
    cfg['ai']['sandboxes']['docked']['options']['image'] = os.environ['TOKEO_TEST_DOCKER_IMAGE']
    with DockerTest(config_defaults=cfg) as app:
        assert run(app, 'echo', dict(text='live')).value.as_str == 'live'
