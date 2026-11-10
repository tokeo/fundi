"""
Tests for the ai extension's sandbox seam.

Driven through ```app.ai._get_agent``` and ```app.ai._exec_in_sandbox```: which
sandbox a chain picks, how deny narrows the tool set at every level, how a tool
reads its configured options, and what the invocation records about where it ran.
The app-free helpers are tested in ```tests/core/ai/test_fundi.py```.
"""

import os
import sys
import inspect
import threading
import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError, TokeoAiFundiAgent, TokeoAiAgent
from tokeo.core.ai import TokeoAiProvider, TokeoAiSandbox, TokeoAiTool, TokeoAiGovernor
from tests.ext.ai.lib.ai_tests_helper import GreetTool
from tokeo.core.ai.sandboxes.in_process import TokeoAiInProcessSandbox
from tokeo.core.ai.sandboxes.subprocess import TokeoAiSubprocessSandbox
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.ext.ai import TokeoAi
from tests.utils import wait_until

# the class paths of the importable test tools the worker loads in a child
ECHO = 'tests.ext.ai.lib.ai_tests_helper:EchoTool'
CWD = 'tests.ext.ai.lib.ai_tests_helper:CwdTool'
GREET = 'tests.ext.ai.lib.ai_tests_helper:GreetTool'
ENV = 'tests.ext.ai.lib.ai_tests_helper:EnvTool'

# the jailed options every platform can enforce; the memory limit is added on
# linux only: macos cannot bind RLIMIT_AS/DATA below the already mapped
# address space and the runner refuses sham limits by design
JAILED_OPTIONS = dict(timeout=5)
if sys.platform == 'linux':
    JAILED_OPTIONS['max_memory_mb'] = 256
SLEEP = 'tests.ext.ai.lib.ai_tests_helper:SleepTool'
SPAWN = 'tests.ext.ai.lib.ai_tests_helper:SpawnTool'
CHATTER = 'tests.ext.ai.lib.ai_tests_helper:ChatterTool'

# the argv of the ai lint command: the setup steps aside, the lint reports
LINT = ['ai', 'lint']
# every tool of the config, offered by the agents that exercise the sandboxes
TOOLS = ['echo', 'cwd', 'env', 'sleep', 'greeter', 'plain_greeter']


class FundiTest(TokeoTest):
    """Test app with the ai extension and the extensions it builds on."""

    class Meta:
        extensions = [
            'tokeo.ext.yaml',
            'tokeo.ext.appenv',
            'tokeo.ext.print',
            'tokeo.ext.jinja2',
            'tokeo.ext.appshare',
            'tokeo.ext.ai',
        ]


def _make_ai_config():
    """Return the ai config every test starts from, as config_defaults."""
    # a self-contained ai config: tools by class path, a group, both core
    # sandboxes (one subprocess "jailed", the in_process catch-all "allow"),
    # and agents that exercise every selection branch
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='mock', agent=None),
        tools={
            'echo': dict(type=ECHO),
            'cwd': dict(type=CWD),
            'env': dict(type=ENV),
            'sleep': dict(type=SLEEP),
            # a tool with options: the item overlays its config_defaults
            'greeter': dict(type=GREET, options=dict(prefix='moin')),
            # the same class without options: it runs on its config_defaults
            'plain_greeter': dict(type=GREET),
            'bundle': ['echo', 'cwd'],
        },
        sandboxes={
            'allow': dict(type='in_process', tools=list(TOOLS)),
            'jailed': dict(
                type='subprocess',
                tools=['echo', 'cwd', 'env', 'sleep'],
                options=dict(JAILED_OPTIONS),
            ),
            # lists the bundle but excepts cwd, so the chain must walk on for it
            'partial': dict(type='subprocess', tools=['bundle'], **{'except': ['cwd']}),
        },
        agents={
            # everything in process
            'plain': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), sandboxes=['allow'])),
            # echo -> jailed (subprocess), the rest falls through to allow
            'mixed': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), sandboxes=['jailed', 'allow'])),
            # no catch-all: a tool no sandbox lists is denied
            'strict': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), sandboxes=['jailed'])),
            # hard deny wins before any sandbox lookup
            'banning': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), deny=['echo'], sandboxes=['allow'])),
            # partial sandbox skips cwd via except, then allow catches it
            'walking': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), sandboxes=['partial', 'allow'])),
            # empty chain -> deny by default
            'empty': dict(type='fundi', options=dict(governors=[], tools=list(TOOLS), sandboxes=[])),
            # carries echo + cwd; the narrow profile denies cwd off this set
            'echoer': dict(type='fundi', options=dict(governors=[], tools=['echo', 'cwd'], sandboxes=['allow'])),
        },
        profiles={
            'mock': dict(type='mock', agent='plain'),
            # shares the plain agent but denies one tool/group of its set
            'narrow': dict(type='mock', agent='echoer', deny=['cwd']),
        },
    )
    return cfg


def _run_tool(app, tool_name, agent_name, **arguments):
    """Run one tool call through the agent's sandbox chain, return its text."""
    # drive just the seam: resolve the agent and execute one tool call through
    # its sandbox chain, returning the model-facing text. value is None when the
    # tool returned nothing, so guard the access
    agent = app.ai._get_agent(agent_name)
    out = app.ai._exec_in_sandbox(tool_name, arguments, agent, None, None, None, None)
    return out.value.as_str if out.value else ''


# ---- the agent classes ------------------------------------------------------


def test_fundi_agent_is_the_concrete_default():
    """'fundi' resolves to TokeoAiFundiAgent, a concrete TokeoAiAgent subclass."""
    assert issubclass(TokeoAiFundiAgent, TokeoAiAgent)
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert app.ai.resolve('agent', 'fundi') is TokeoAiFundiAgent
        # the old alias is gone
        with pytest.raises(TokeoAiError):
            app.ai.resolve('agent', 'default')


# ---- in_process sandbox (behaviour 1:1) -------------------------------------


def test_in_process_runs_directly():
    """echo under the plain agent runs in process: 'hi' comes back as 'hi'."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert _run_tool(app, 'echo', 'plain', text='hi') == 'hi'


# ---- subprocess sandbox: real execution, cwd, env, timeout ------------------


def test_subprocess_runs_in_a_child(tmp):
    """echo under mixed runs in the jailed child, 'from-child' comes back."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        # echo is in jailed.tools (subprocess) under the mixed agent
        assert _run_tool(app, 'echo', 'mixed', text='from-child') == 'from-child'


def test_subprocess_cwd_takes_effect(tmp_path):
    """The child runs in the sandbox's cwd <tmp_path>/sbx, made on demand."""
    cfg = _make_ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['cwd'] = str(tmp_path / 'sbx')
    with FundiTest(config_defaults=cfg) as app:
        out = _run_tool(app, 'cwd', 'mixed')
        assert out == str(tmp_path / 'sbx')


def test_subprocess_env_is_scrubbed():
    """jailed lists no env, so host HOME is '<unset>' in the scrubbed child."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert _run_tool(app, 'env', 'mixed', name='HOME') == '<unset>'


def test_subprocess_timeout_is_enforced():
    """A 3 s sleep under a 1 s jailed timeout raises TokeoAiError 'timed out'."""
    cfg = _make_ai_config()
    # tighten the jailed timeout and have the tool sleep past it
    cfg['ai']['sandboxes']['jailed']['options']['timeout'] = 1
    with FundiTest(config_defaults=cfg) as app:
        with pytest.raises(TokeoAiError, match='timed out'):
            _run_tool(app, 'sleep', 'mixed', seconds=3)


def test_in_process_keeps_the_output_of_parallel_calls_apart(capsys):
    """Two calls in parallel threads each get only their own 20 lines of output."""
    cfg = _make_ai_config()
    cfg['ai']['tools']['chatter'] = dict(type=CHATTER)
    cfg['ai']['sandboxes']['allow']['tools'].append('chatter')
    cfg['ai']['agents']['plain']['options']['tools'].append('chatter')
    with FundiTest(config_defaults=cfg) as app:
        agent = app.ai._get_agent('plain')
        start = threading.Barrier(2)
        results = {}

        def run_call(text):
            start.wait()
            results[text] = app.ai._exec_in_sandbox('chatter', dict(text=text), agent, None, None, None, None)

        threads = [threading.Thread(target=run_call, args=(text,)) for text in ('left', 'right')]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        print('outside')
    for text in ('left', 'right'):
        assert results[text].state.stdout == f'{text}\n' * 20
        assert results[text].state.stderr == f'{text}\n' * 20
    # a print outside a call still reaches the real stream
    assert 'outside' in capsys.readouterr().out


def _make_spawn_config(tmp_path):
    """The config with spawn in jailed, running in tmp_path, with a 1 s timeout."""
    cfg = _make_ai_config()
    cfg['ai']['tools']['spawn'] = dict(type=SPAWN)
    cfg['ai']['sandboxes']['jailed']['tools'].append('spawn')
    cfg['ai']['sandboxes']['jailed']['options'].update(timeout=1, cwd=str(tmp_path))
    cfg['ai']['agents']['mixed']['options']['tools'].append('spawn')
    return cfg


def _is_alive(pid):
    """Whether the pid still runs; a zombie left unreaped counts as ended."""
    try:
        with open(f'/proc/{pid}/stat') as f:
            # the state follows the name in parentheses, Z marks a zombie
            return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def test_subprocess_timeout_ends_the_processes_the_tool_started(tmp_path):
    """On the timeout the sleep the tool started ends too, not only the tool."""
    with FundiTest(config_defaults=_make_spawn_config(tmp_path)) as app:
        with pytest.raises(TokeoAiError, match='timed out'):
            _run_tool(app, 'spawn', 'mixed', seconds=3)
    pid = int((tmp_path / 'pid.txt').read_text())
    assert wait_until(lambda: not _is_alive(pid))


def test_subprocess_end_ends_the_processes_the_tool_started(tmp_path):
    """A tool that returns at once leaves no sleep it started behind."""
    cfg = _make_spawn_config(tmp_path)
    # the 1 s timeout is for the timeout test, under load this call takes longer
    cfg['ai']['sandboxes']['jailed']['options']['timeout'] = 30
    with FundiTest(config_defaults=cfg) as app:
        assert _run_tool(app, 'spawn', 'mixed', seconds=0) == 'spawned'
    pid = int((tmp_path / 'pid.txt').read_text())
    assert wait_until(lambda: not _is_alive(pid))


# ---- selection: chain order, except, hard deny, deny-by-default -------------


def test_chain_picks_first_sandbox_whose_tools_contain():
    """Under mixed, echo and env go to jailed, the first sandbox listing them."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._get_agent('mixed')
        # echo is in jailed.tools (first in the chain) -> subprocess
        assert isinstance(app.ai._find_sandbox('echo', agent), TokeoAiSubprocessSandbox)
        # env is in jailed.tools too
        assert isinstance(app.ai._find_sandbox('env', agent), TokeoAiSubprocessSandbox)


def test_except_skips_a_sandbox_and_chain_walks_on():
    """partial excepts cwd: echo runs in partial, cwd walks on to allow."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._get_agent('walking')
        # partial lists the bundle (echo, cwd) but excepts cwd; so echo runs
        # in partial (subprocess) while cwd falls through to allow (in process)
        assert isinstance(app.ai._find_sandbox('echo', agent), TokeoAiSubprocessSandbox)
        assert isinstance(app.ai._find_sandbox('cwd', agent), TokeoAiInProcessSandbox)


def test_sandbox_runs_only_the_tools_it_names():
    """A tool allow does not name finds no sandbox under plain, even if offered."""
    cfg = _make_ai_config()
    cfg['ai']['sandboxes']['allow']['tools'].remove('sleep')
    with FundiTest(config_defaults=cfg) as app:
        assert app.ai._find_sandbox('sleep', app.ai._get_agent('plain')) is None


def test_linter_flags_an_unknown_name_in_tool_policy():
    """A tool_policy deny of 'no_such' is flagged, the name matches no tool."""
    cfg = _make_ai_config()
    cfg['ai']['guards'] = {'readonly': dict(type='tool_policy', options=dict(deny=['echo', 'no_such']))}
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert [i.path for i in issues if 'no_such' in i.message] == ['ai.guards.readonly.options.deny']


@pytest.mark.parametrize(
    'name, message',
    [('__implicit', 'reserved for the framework'), ('__own', 'reserved for the framework'), (None, 'must be a string')],
    ids=['implicit', 'dunder', 'yaml_null'],
)
def test_linter_flags_a_reserved_or_no_agent_name(name, message):
    """An agent named __implicit or __own is reserved, a yaml key null no name."""
    cfg = _make_ai_config()
    cfg['ai']['agents'][name] = dict(type='fundi', options=dict(governors=[]))
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any(i.level == 'error' and message in i.message for i in issues)


def test_linter_flags_an_agent_without_governors():
    """An agent that does not list governors is an error, [] only a note."""
    cfg = _make_ai_config()
    cfg['ai']['agents']['bare'] = dict(type='fundi', options=dict(tools=['echo'], sandboxes=['allow']))
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = {i.path: i.level for i in TokeoAiLinter(app).lint()}
        assert issues['ai.agents.bare.options.governors'] == 'error'
        assert issues['ai.agents.plain.options.governors'] == 'note'


def test_linter_flags_the_all_catch_all():
    """tools: _all on a sandbox is flagged, there is no catch-all."""
    cfg = _make_ai_config()
    cfg['ai']['sandboxes']['allow']['tools'] = '_all'
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any(i.path == 'ai.sandboxes.allow.tools' and 'no catch-all' in i.message for i in issues)


def test_hard_deny_forbids_before_lookup():
    """The agent's deny refuses echo before any sandbox lookup: 'is denied'."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        with pytest.raises(TokeoAiError, match='is denied'):
            _run_tool(app, 'echo', 'banning', text='x')


def test_empty_chain_denies_by_default():
    """An agent with an empty sandbox chain refuses echo: 'no sandbox'."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        with pytest.raises(TokeoAiError, match='no sandbox'):
            _run_tool(app, 'echo', 'empty', text='x')


def test_profile_deny_subtracts_from_the_agent_set():
    """Active is agent.tools minus profile.deny: echo + cwd minus cwd is echo."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        name, profile = app.ai._resolve_profile(profile='narrow')
        agent = app.ai._get_agent('echoer')
        active = app.ai._get_active_tools(agent, profile)
        assert 'echo' in active and 'cwd' not in active


def test_profile_deny_is_enforced_at_exec():
    """A profile-denied cwd is refused at exec, the defence behind trimmed specs."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        name, profile = app.ai._resolve_profile(profile='narrow')
        agent = app.ai._get_agent('echoer')
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('cwd', {}, agent, profile, None, None, None)


def test_call_deny_narrows_further():
    """A call-level deny of echo leaves echoer's echo + cwd as just ['cwd']."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._get_agent('echoer')
        active = app.ai._get_active_tools(agent, None, ['echo'])
        assert active == ['cwd']


def test_call_deny_is_enforced_at_exec():
    """A call can only narrow: a call-denied echo is refused at the seam."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._get_agent('echoer')
        with pytest.raises(TokeoAiError, match='is denied'):
            app.ai._exec_in_sandbox('echo', {'text': 'x'}, agent, None, ['echo'], None, None)


def test_strict_agent_denies_unlisted_tool():
    """strict runs jailed's echo; greeter, which jailed does not list, is denied."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        # the strict agent has only jailed; jailed lists echo, so echo is ok
        assert _run_tool(app, 'echo', 'strict', text='ok') == 'ok'
        # greeter is configured and offered, but no sandbox of the chain lists it
        with pytest.raises(TokeoAiError, match='no sandbox in the agent chain'):
            _run_tool(app, 'greeter', 'strict', name='x')


def test_agent_refuses_a_tool_it_does_not_offer():
    """echoer offers echo and cwd only; env, though in a sandbox, is refused."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        # the allow sandbox would take env, the agent does not offer it
        with pytest.raises(TokeoAiError, match="tool 'env' is not offered by the agent"):
            _run_tool(app, 'env', 'echoer')


def test_no_agent_named_runs_the_implicit_agent():
    """No agent named gives __implicit: no tools, sandboxes or governors."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._resolve_agent(None)
        assert agent.config_name == '__implicit'
        assert (agent._config('tools'), agent._config('sandboxes'), agent._config('governors')) == ([], [], [])
        assert app.ai._resolve_agent(None) is agent


@pytest.mark.parametrize('name', ['__implicit', 'null'])
def test_the_implicit_agent_cannot_be_named(name):
    """agent __implicit or null in a call is an unknown agent like any typo."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        with pytest.raises(TokeoAiError, match='is not configured under ai.agents'):
            app.ai._resolve_agent(name)


def test_a_profile_agent_null_is_no_agent_a_missing_one_the_default():
    """agent: null on a profile gives __implicit, without the key the default."""
    cfg = _make_ai_config()
    cfg['ai']['defaults']['agent'] = 'plain'
    with FundiTest(config_defaults=cfg) as app:
        assert app.ai._resolve_agent(None, dict(type='mock', agent=None)).config_name == '__implicit'
        assert app.ai._resolve_agent(None, dict(type='mock')).config_name == 'plain'
        assert app.ai._resolve_agent('echoer', dict(type='mock', agent=None)).config_name == 'echoer'


def _make_single_value_config():
    """make_ai_config() with an agent that names every list option by one value."""
    cfg = _make_ai_config()
    cfg['ai']['guards'] = {'audit': dict(type='trace_audit'), 'redact': dict(type='regex_redact', options=dict(patterns=['x']))}
    options = dict(tools='echo', sandboxes='allow', governors='audit', omit='redact', deny='cwd')
    cfg['ai']['agents']['single'] = dict(type='fundi', options=options)
    return cfg


def test_an_agent_takes_a_single_value_as_a_list_of_one():
    """tools echo, sandboxes allow and governors audit as one value each run."""
    with FundiTest(config_defaults=_make_single_value_config()) as app:
        agent = app.ai._get_agent('single')
        assert _run_tool(app, 'echo', 'single', text='moin') == 'moin'
        assert [entry.config_name for entry in app.ai._resolve_governors(agent)] == ['audit']


def test_linter_takes_a_single_value_as_a_list_of_one():
    """The single values of the agent and a stage {audit: on_call} lint clean."""
    cfg = _make_single_value_config()
    cfg['ai']['agents']['staged'] = dict(type='fundi', options=dict(governors={'audit': 'on_call'}))
    assert _get_lint_errors(cfg) == {}


@pytest.mark.parametrize('key, value', [('max_steps', -1), ('max_loops', 'many'), ('trace', 'yes')])
def test_linter_checks_the_keys_at_ai_level(key, value):
    """max_steps -1, max_loops 'many' and trace 'yes' at ai level are errors."""
    cfg = _make_ai_config()
    cfg['ai'][key] = value
    assert f'ai.{key}' in _get_lint_errors(cfg)


def test_a_short_form_tool_and_guard_run_like_the_long_form():
    """echo: <class path> runs, audit: trace_audit builds as a guard."""
    cfg = _make_ai_config()
    cfg['ai']['tools']['echo'] = ECHO
    cfg['ai']['guards'] = {'audit': 'trace_audit'}
    with FundiTest(config_defaults=cfg) as app:
        assert _run_tool(app, 'echo', 'plain', text='moin') == 'moin'
        assert app.ai._get_governor('audit').config_name == 'audit'
        assert _get_lint_errors(cfg) == {}


@pytest.mark.parametrize('section, name', [('sandboxes', 'allow'), ('agents', 'plain'), ('profiles', 'mock')])
def test_linter_flags_a_short_form_where_the_long_form_is_required(section, name):
    """A sandbox, agent or profile as a string is an error naming the long form."""
    cfg = _make_ai_config()
    cfg['ai'][section][name] = 'some_type'
    message = _get_lint_errors(cfg)[f'ai.{section}.{name}']
    assert message.endswith('has no short form, write it as a mapping: {type: some_type, ...}')


@pytest.mark.parametrize(
    'profile, path',
    [
        (dict(type='mock', base_url='http://x/v1'), 'ai.profiles.p.base_url'),
        (dict(type='oai_compat', model='m', options=dict(base_url='http://x/v1', model='m')), 'ai.profiles.p.options.model'),
        (dict(type='mock', native_tools_call=True), 'ai.profiles.p.native_tools_call'),
        (dict(type='mock', model_params='hot'), 'ai.profiles.p.model_params'),
    ],
    ids=['base_url_on_top', 'model_in_options', 'native_tools_call', 'model_params_no_mapping'],
)
def test_linter_holds_each_profile_key_to_its_one_place(profile, path):
    """base_url on top, model in options, a dropped key, a str model_params fail."""
    cfg = _make_ai_config()
    cfg['ai']['profiles']['p'] = profile
    assert path in _get_lint_errors(cfg)


def test_linter_accepts_a_profile_in_its_layout():
    """model, model_params on top and the connection under options lint clean."""
    cfg = _make_ai_config()
    options = dict(base_url='http://x/v1', key='k', timeout=30)
    cfg['ai']['profiles']['p'] = dict(type='oai_compat', model='m', model_params=dict(anything=1), options=options)
    assert _get_lint_errors(cfg) == {}


def test_setup_aborts_on_a_lint_error():
    """An unknown key toolss ends the setup with the lint report, not later."""
    cfg = _make_ai_config()
    cfg['ai']['toolss'] = []
    with pytest.raises(TokeoAiError, match=r"invalid ai configuration[\s\S]*ai\.toolss: unknown key 'toolss'"):
        with FundiTest(config_defaults=cfg):
            pass


def test_setup_logs_a_lint_warning():
    """defaults null is only a warning: the setup logs it and goes on."""
    logged_lines = []

    def record(app):
        app.log.warning = logged_lines.append

    cfg = _make_ai_config()
    cfg['ai']['defaults'] = None
    with FundiTest(config_defaults=cfg, hooks=[('post_setup', record, 50)]):
        assert any('ai.defaults' in message for message in logged_lines)


@pytest.mark.parametrize('cfg', [dict(ai=dict(defaults=dict(profile='mock'))), {}], ids=['missing', 'no_ai_config'])
def test_setup_aborts_without_license_acknowledged(cfg):
    """Without license_acknowledged, also without any ai config, the start ends."""
    with pytest.raises(TokeoAiError, match=r'ai\.license_acknowledged: is required'):
        with FundiTest(config_defaults=cfg):
            pass


def _make_notice_test(**kw):
    """A FundiTest that records its info log, and the list it records into."""
    logged_lines = []

    def record(app):
        app.log.info = logged_lines.append

    return FundiTest(hooks=[('post_setup', record, 50)], **kw), logged_lines


def test_the_ai_lint_command_shows_the_license_notice():
    """ai lint logs the license notice while license_acknowledged is not true."""
    app, logged_lines = _make_notice_test(argv=LINT, config_defaults=_make_ai_config())
    with app:
        app.run()
    assert logged_lines.count(TokeoAi.FUNDI_LICENSE_NOTICE) == 1


def test_the_first_ai_call_from_code_shows_the_license_notice_once():
    """Two ask calls from code log the license notice once; acknowledged, never."""
    app, logged_lines = _make_notice_test(config_defaults=_make_ai_config())
    with app:
        app.ai.ask('hi', agent='plain')
        app.ai.ask('hi', agent='plain')
    assert logged_lines.count(TokeoAi.FUNDI_LICENSE_NOTICE) == 1
    cfg = _make_ai_config()
    cfg['ai']['license_acknowledged'] = True
    app, logged_lines = _make_notice_test(config_defaults=cfg)
    with app:
        app.ai.ask('hi', agent='plain')
    assert TokeoAi.FUNDI_LICENSE_NOTICE not in logged_lines


def test_the_implicit_agent_offers_no_tool():
    """Under __implicit echo is refused, even though a sandbox would take it."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        agent = app.ai._resolve_agent(None)
        with pytest.raises(TokeoAiError, match="tool 'echo' is not offered by the agent"):
            app.ai._exec_in_sandbox('echo', dict(text='x'), agent, None, None, None, None)


# ---- env expansion helper ---------------------------------------------------


def test_linter_accepts_a_sound_sandbox_config():
    """The linter reports no error for the base _make_ai_config()."""
    with FundiTest(argv=LINT, config_defaults=_make_ai_config()) as app:
        issues = TokeoAiLinter(app).lint()
        errors = [i for i in issues if i.level == 'error']
        assert errors == [], errors


def test_linter_flags_unknown_sandbox_in_chain():
    """A chain naming the unconfigured sandbox 'ghost' is a linter error."""
    cfg = _make_ai_config()
    cfg['ai']['agents']['plain']['options']['sandboxes'] = ['ghost']
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('ghost' in i.message and i.level == 'error' for i in issues)


def test_linter_accepts_a_tuple_as_list():
    """A chain built in python as tuple ('jailed',) lints clean like a yaml list."""
    cfg = _make_ai_config()
    cfg['ai']['agents']['plain']['options']['sandboxes'] = ('jailed',)
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert [i for i in issues if i.path.endswith('.sandboxes')] == []


def test_linter_flags_missing_sandbox_tools():
    """A sandbox without tools is flagged: tools is required."""
    cfg = _make_ai_config()
    del cfg['ai']['sandboxes']['jailed']['tools']
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any('required' in i.message for i in issues)


def _get_lint_errors(cfg):
    """The error issues of the linter for a config, as {path: message}."""
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        return {i.path: i.message for i in TokeoAiLinter(app).lint() if i.level == 'error'}


@pytest.mark.parametrize('sandbox_type', ['subprocess', 'in_process', 'tokeo.core.ai.sandboxes:TokeoAiWasmSandbox'])
def test_linter_flags_an_unknown_sandbox_option(sandbox_type):
    """The option 'net', which no sandbox reads, is an error on each sandbox."""
    cfg = _make_ai_config()
    cfg['ai']['sandboxes']['jailed'] = dict(type=sandbox_type, tools=['echo'], options=dict(net=False))
    assert _get_lint_errors(cfg)['ai.sandboxes.jailed.options.net'].startswith("unknown option 'net'")


@pytest.mark.parametrize(
    'section, name, item',
    [
        ('tools', 'echo', dict(type=ECHO)),
        ('guards', 'audit', dict(type='trace_audit')),
        ('sandboxes', 'jailed', dict(type='subprocess', tools=['echo'])),
        ('agents', 'plain', dict(type='fundi')),
        ('profiles', 'mock', dict(type='mock', agent='plain')),
    ],
)
def test_options_null_is_an_error_and_a_missing_block_becomes_empty(section, name, item):
    """options: null is an error in each section; omitted, the block is {}."""
    cfg = _make_ai_config()
    cfg['ai'].setdefault(section, {})[name] = dict(item, options=None)
    assert _get_lint_errors(cfg)[f'ai.{section}.{name}.options'] == 'must be a mapping, write {} or omit it'
    cfg['ai'][section][name] = dict(item)
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        assert app.config.get('ai', section)[name]['options'] == {}


def test_linter_flags_guards_on_an_agent_as_unknown_option():
    """guards instead of governors on an agent is an error, not ignored."""
    cfg = _make_ai_config()
    cfg['ai']['agents']['plain']['options']['guards'] = ['audit']
    assert _get_lint_errors(cfg)['ai.agents.plain.options.guards'] == "unknown option 'guards'"


def test_linter_flags_an_unknown_tool_option():
    """prefixx on the greeter is an error, prefix is its one option."""
    cfg = _make_ai_config()
    cfg['ai']['tools']['greeter']['options'] = dict(prefixx='moin')
    assert _get_lint_errors(cfg)['ai.tools.greeter.options.prefixx'] == "unknown option 'prefixx'; did you mean 'prefix'?"


def test_linter_flags_an_unknown_governor_option_also_per_stage():
    """denny in the options and in on_call of a tool_policy are both errors."""
    cfg = _make_ai_config()
    cfg['ai']['guards'] = {'policy': dict(type='tool_policy', options=dict(denny=['echo']), on_call=dict(options=dict(denny=['cwd'])))}
    errors = _get_lint_errors(cfg)
    assert errors['ai.guards.policy.options.denny'].startswith("unknown option 'denny'")
    assert errors['ai.guards.policy.on_call.options.denny'].startswith("unknown option 'denny'")


def test_linter_flags_a_stage_block_with_other_keys():
    """An on_call block holds only options: a type in it is an error."""
    cfg = _make_ai_config()
    cfg['ai']['guards'] = {'policy': dict(type='tool_policy', on_call=dict(type='trace_audit'))}
    assert "unknown key 'type'" in _get_lint_errors(cfg)['ai.guards.policy.on_call.type']


@pytest.mark.parametrize(
    'section, path',
    [
        (None, 'ai.toolss'),
        ('defaults', 'ai.defaults.toolss'),
        ('sandboxes.allow', 'ai.sandboxes.allow.toolss'),
        ('agents.plain', 'ai.agents.plain.toolss'),
    ],
)
def test_linter_flags_an_unknown_key_as_error(section, path):
    """A key toolss is an error at ai level, in defaults, a sandbox, an agent."""
    cfg = _make_ai_config()
    target = cfg['ai']
    for key in section.split('.') if section else []:
        target = target[key]
    target['toolss'] = ['echo']
    assert _get_lint_errors(cfg)[path].startswith("unknown key 'toolss'")


@pytest.mark.parametrize('value', [False, True, 'yes', 1])
def test_linter_takes_any_value_of_license_acknowledged(value):
    """license_acknowledged false, true, yes or 1 is set, so it lints clean."""
    cfg = _make_ai_config()
    cfg['ai']['license_acknowledged'] = value
    assert 'ai.license_acknowledged' not in _get_lint_errors(cfg)


def test_linter_requires_license_acknowledged():
    """Without license_acknowledged the linter reports an error."""
    cfg = _make_ai_config()
    del cfg['ai']['license_acknowledged']
    assert _get_lint_errors(cfg)['ai.license_acknowledged'].startswith('is required')


@pytest.mark.parametrize('value', [True, -1, 1.5, '3'])
def test_linter_flags_max_steps_that_is_no_whole_number_of_0_or_more(value):
    """A bool, negative, float or str max_steps is flagged; 0 means unlimited."""
    cfg = _make_ai_config()
    cfg['ai']['agents']['plain']['options']['max_steps'] = value
    with FundiTest(argv=LINT, config_defaults=cfg) as app:
        issues = TokeoAiLinter(app).lint()
        assert any(i.path.endswith('.max_steps') for i in issues)


def test_subprocess_resolves_registry_alias_tools():
    """A registry alias runs in the child: the LOADED class's path crosses over."""
    from tests.ext.ai.lib.ai_tests_helper import EchoTool

    cfg = _make_ai_config()
    cfg['ai']['tools']['shorty'] = dict(type='echo_short')
    cfg['ai']['sandboxes']['jailed']['tools'].append('shorty')
    cfg['ai']['agents']['mixed']['options']['tools'].append('shorty')
    # the project registers the alias in a post_setup hook: weight 50 runs after
    # app.ai exists (weight 0) and before the lint (weight 100)
    hooks = [('post_setup', lambda app: app.ai.register('tool', 'echo_short', EchoTool), 50)]
    with FundiTest(config_defaults=cfg, hooks=hooks) as app:
        assert _run_tool(app, 'shorty', 'mixed', text='hi') == 'hi'


def test_subprocess_refuses_classes_a_child_cannot_import():
    """A nested Outer.GhostTool is refused early, not importable by module path."""
    # a script's __main__ case fails the same guard; ```python -m``` resolves
    # via the module spec
    from tests.ext.ai.lib.ai_tests_helper import EchoTool

    Ghost = type('GhostTool', (EchoTool,), {'__qualname__': 'Outer.GhostTool'})
    with FundiTest(config_defaults=_make_ai_config()) as app:
        tool = Ghost(None)
        sandbox = app.ai._get_sandbox('jailed')
        with pytest.raises(TokeoAiError, match='not importable by module path'):
            sandbox.exec(tool, {'text': 'hi'})


def test_subprocess_keeps_a_user_pythonpath_in_the_lead():
    """A PYTHONPATH in options.env stays first, the parent's path is appended."""
    cfg = _make_ai_config()
    cfg['ai']['sandboxes']['jailed']['options']['env'] = {'PYTHONPATH': '/user/extra'}
    with FundiTest(config_defaults=cfg) as app:
        out = _run_tool(app, 'env', 'mixed', name='PYTHONPATH')
        assert out.startswith('/user/extra' + os.pathsep)


# ---- the config surface: every ai class is set up the same way --------------


def test_setup_is_one_form_across_the_ai_classes():
    """All ai classes set up via _setup(app, config_name, item), one form."""
    # the handler writes no attributes from outside
    for cls in (TokeoAiProvider, TokeoAiSandbox, TokeoAiAgent, TokeoAiTool, TokeoAiGovernor):
        params = list(inspect.signature(cls._setup).parameters)
        assert params == ['self', 'app', 'config_name', 'item'], cls.__name__


def test_a_tool_reads_its_configured_options_through_config():
    """Option prefix='moin' overlays config_defaults, read lazily by _config."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert _run_tool(app, 'greeter', 'plain', name='ada') == 'moin ada'
        tool = app.ai._get_tool('greeter')
        assert tool.config_name == 'greeter'
        assert tool._config('prefix') == 'moin'


def test_a_tool_without_options_runs_on_its_config_defaults():
    """No options configured: the Meta default 'hello' stands, no None leaks in."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert _run_tool(app, 'plain_greeter', 'plain', name='ada') == 'hello ada'
        assert app.ai._get_tool('plain_greeter')._config('prefix') == 'hello'


def test_two_items_of_one_class_stay_separate():
    """One class under two keys: two objects, names, options; defaults unmutated."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        greeter, plain = app.ai._get_tool('greeter'), app.ai._get_tool('plain_greeter')
        assert greeter is not plain
        assert (greeter.config_name, plain.config_name) == ('greeter', 'plain_greeter')
        assert (greeter._config('prefix'), plain._config('prefix')) == ('moin', 'hello')
        assert TokeoAiTool.Meta.config_defaults == {}
        assert GreetTool.Meta.config_defaults == {'prefix': 'hello'}


class DescribedGreetTool(GreetTool):
    """A greeter that declares an option named like its Meta description."""

    class Meta:
        config_defaults = dict(prefix='hello', description=None)


def test_options_do_not_reach_the_class_meta():
    """An option named description stays an option, the spec keeps the Meta text."""
    cfg = _make_ai_config()
    options = dict(prefix='moin', description='REWRITTEN')
    cfg['ai']['tools']['greeter'] = dict(type='tests.ext.ai.test_ai:DescribedGreetTool', options=options)
    with FundiTest(config_defaults=cfg) as app:
        spec = app.ai._build_tool_specs(['greeter'])[0]['function']
        assert spec['description'] == 'greet the given name'
        assert spec['name'] == 'greeter'
        # it stays reachable as what it is: an option
        assert app.ai._get_tool('greeter')._config('description') == 'REWRITTEN'


def test_the_sandbox_carries_its_config_name_to_the_invocation():
    """The jailed sandbox knows its key 'jailed', so a call records WHERE it ran."""
    with FundiTest(config_defaults=_make_ai_config()) as app:
        assert app.ai._get_sandbox('jailed').config_name == 'jailed'
