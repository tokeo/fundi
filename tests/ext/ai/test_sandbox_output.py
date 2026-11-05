"""
Tests for the output limit of the sandboxes, the option output_mb.

A tool prints more than the limit: its captured stdout and stderr keep the
first output_mb and end with a mark. A tool returns a value larger than the
limit: the host reads no more and the call fails as a machinery error. Run in
process and in the subprocess sandbox; docker and wasm have their own tests.
"""

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.linter import TokeoAiLinter
from tokeo.core.ai.sandboxes._common import OutputBuffer, reply_bytes

LOUD = 'tests.ext.ai.lib.ai_tests_helper:LoudTool'
BIG = 'tests.ext.ai.lib.ai_tests_helper:BigTool'
# 0.001 MB are 1048 bytes
LIMIT = dict(output_mb=0.001)


class OutputTest(TokeoTest):
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


def ai_config(options):
    """The tools loud and big in process (agent local), in a subprocess (jail)."""
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        license_acknowledged=False,
        defaults=dict(profile='mock', agent=None),
        tools=dict(loud=dict(type=LOUD), big=dict(type=BIG)),
        sandboxes=dict(
            local=dict(type='in_process', tools=['loud', 'big'], options=dict(options)),
            jailed=dict(type='subprocess', tools=['loud', 'big'], options=dict(timeout=10, **options)),
        ),
        agents=dict(
            local=dict(type='fundi', options=dict(governors=[], tools=['loud', 'big'], sandboxes=['local'])),
            jail=dict(type='fundi', options=dict(governors=[], tools=['loud', 'big'], sandboxes=['jailed'])),
        ),
        profiles=dict(mock=dict(type='mock', agent='local')),
    )
    return cfg


def run(agent, tool, size, options=LIMIT):
    """Run the tool with the size in the sandbox of the agent."""
    with OutputTest(config_defaults=ai_config(options)) as app:
        return app.ai._exec_in_sandbox(tool, dict(size=size), app.ai._agent(agent), None, None, None, None)


@pytest.mark.parametrize('agent', ['local', 'jail'])
def test_the_captured_output_is_cut_at_output_mb(agent):
    """5000 bytes printed with output_mb 0.001: 1048 kept, then the mark."""
    out = run(agent, 'loud', 5000)
    assert out.value.as_str == 'done'
    assert out.state.stdout == 'x' * 1048 + '\n[... cut after 0.001 MB]'
    assert out.state.stderr == 'y' * 1048 + '\n[... cut after 0.001 MB]'


@pytest.mark.parametrize('agent', ['local', 'jail'])
def test_output_within_output_mb_is_kept_whole(agent):
    """500 bytes printed with output_mb 0.001 stay as they are."""
    out = run(agent, 'loud', 500)
    assert (out.state.stdout, out.state.stderr) == ('x' * 500, 'y' * 500)


@pytest.mark.parametrize('agent, where', [('local', 'in_process'), ('jail', 'subprocess')])
def test_a_value_over_output_mb_is_an_error(agent, where):
    """A value of 5000 bytes with output_mb 0.001 is no result but an error."""
    with pytest.raises(TokeoAiError, match=rf'delivered more than 0.001 MB in the {where} sandbox'):
        run(agent, 'big', 5000)


def test_the_buffer_cuts_between_whole_characters():
    """Ten bytes of three-byte characters keep three of them, then the mark."""
    buffer = OutputBuffer(10, '10 B')
    assert buffer.write('€' * 5) == 5
    assert buffer.getvalue() == '€€€\n[... cut after 10 B]'


def test_the_raw_reply_may_hold_the_views_and_the_captures():
    """1 MB of output allows a raw reply of 8 MB and 64 KiB."""
    assert reply_bytes(1024 * 1024) == 8 * 1024 * 1024 + 64 * 1024


def test_the_default_output_mb_keeps_a_normal_reply():
    """Without output_mb a value of 5000 bytes is well below the default 1 MB."""
    assert run('jail', 'big', 5000, options={}).value.as_str == 'z' * 5000


@pytest.mark.parametrize('value', [0, -1, 'big', None, True])
def test_the_linter_checks_output_mb_of_every_sandbox(value):
    """output_mb is a number above 0, for in_process and subprocess alike."""
    with OutputTest(argv=['ai', 'lint'], config_defaults=ai_config(dict(output_mb=value))) as app:
        errors = {i.path: i.message for i in TokeoAiLinter(app).lint() if i.level == 'error'}
    expected = 'output_mb must be a number of MB above 0'
    assert (errors.get('ai.sandboxes.local.options'), errors.get('ai.sandboxes.jailed.options')) == (expected, expected)
