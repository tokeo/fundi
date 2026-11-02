"""
Tests for the agent loop of the ai extension, ```app.ai.chat``` and ```ask```.

A scripted provider plays a fixed sequence of model answers, so each test
drives the loop through known rounds: tool calls, the follow-up model call,
the budgets ```max_steps``` and ```max_loops```, and the path with and without
governors at the tool stages. The tools run in process.
"""

import pytest
from cement.utils.misc import init_defaults
from tokeo.main import TokeoTest
from tokeo.core.ai import TokeoAiError, TokeoAiProvider, ChatResult, ToolCall
from tokeo.core.ai.data import Invocation

SCRIPT = 'tests.ext.ai.test_loop.ScriptProvider'
ECHO = 'tests.ext.ai.lib.ai_tests_helper.EchoTool'


class LoopTest(TokeoTest):
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


class ScriptProvider(TokeoAiProvider):
    """
    A provider that answers from the ```script``` option of its profile.

    The script is a list of turns, each ```{'text': ..., 'calls': [(name,
    arguments), ...]}```. The turn played is the number of assistant turns in
    the messages so far, so a provider call needs no state of its own; past the
    end the last turn repeats. Every call records the messages it got in
    ```seen```.
    """

    def __init__(self, app, *args, **kw):
        super().__init__(app, *args, **kw)
        self.seen = []

    def chat(self, profile, messages, tools=None, model_params=None):
        """Play the next turn of the script, record the messages."""
        self.seen.append(list(messages))
        script = profile['options']['script']
        index = min(sum(1 for m in messages if m.get('role') == 'assistant'), len(script) - 1)
        turn = script[index]
        calls = [ToolCall(id=f'c{index}_{n}', name=name, arguments=arguments) for n, (name, arguments) in enumerate(turn.get('calls', []))]
        return ChatResult(text=turn.get('text', ''), tool_calls=calls)


def loop_config(script, **agent_options):
    """An ai config: echo in process, agents plain and audited, the script."""
    cfg = init_defaults('ai')
    cfg['ai'] = dict(
        defaults=dict(profile='script', agent=None),
        tools={'echo': dict(type=ECHO), 'shout': dict(type=ECHO)},
        guards={'audit': dict(type='trace_audit')},
        # shout is configured and has a sandbox, but no agent offers it
        sandboxes={'local': dict(type='in_process', tools=['echo', 'shout'])},
        agents={
            'plain': dict(type='fundi', options=dict(tools=['echo'], sandboxes=['local'], **agent_options)),
            'audited': dict(type='fundi', options=dict(tools=['echo'], governors=['audit'], sandboxes=['local'], **agent_options)),
        },
        profiles={'script': dict(type=SCRIPT, options=dict(script=script))},
    )
    return cfg


def call(text):
    """A script turn that calls echo with the text."""
    return dict(calls=[('echo', dict(text=text))])


def tool_contents(result):
    """The contents of the tool messages on the trace of a run, in order."""
    return [s.object['content'] for s in result.trace if isinstance(s.object, dict) and s.object.get('role') == 'tool']


# chat


def test_chat_without_tool_calls_answers_at_once():
    """A first answer 'hello' without calls ends the run after 0 tool rounds."""
    with LoopTest(config_defaults=loop_config([dict(text='hello')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'hello'
        assert result.loopdata.steps == 0


def test_chat_runs_a_tool_and_asks_the_model_again():
    """Echo of 'moin' runs once, the model sees its result and answers 'done'."""
    with LoopTest(config_defaults=loop_config([call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'done'
        assert result.loopdata.steps == 1
        assert tool_contents(result) == ['moin']
        # the follow-up model call got the assistant turn and the tool result
        last = app.ai._provider(SCRIPT).seen[-1]
        assert [m['role'] for m in last] == ['user', 'assistant', 'tool']


def test_chat_max_steps_reached_aborts():
    """A model that never stops calling aborts at max_steps 2 with an error."""
    with LoopTest(config_defaults=loop_config([call('again')])) as app:
        with pytest.raises(TokeoAiError, match=r'max_steps \(2\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain', max_steps=2)


def test_chat_max_loops_reached_aborts():
    """Three rounds of only failing calls abort at max_loops 3 with an error."""
    script = [dict(calls=[('nope', {})])]
    with LoopTest(config_defaults=loop_config(script)) as app:
        with pytest.raises(TokeoAiError, match=r'max_loops \(3\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain', max_loops=3)


def test_chat_successful_call_resets_the_failed_loops():
    """Fail, fail, success, fail, fail ends at 'done': max_loops 3 is never hit."""
    fail = dict(calls=[('nope', {})])
    script = [fail, fail, call('ok'), fail, fail, dict(text='done')]
    with LoopTest(config_defaults=loop_config(script)) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain', max_loops=3)
        assert result.answer.text == 'done'
        assert result.loopdata.steps == 5
        assert result.loopdata.failed_loops == 2


def test_chat_agent_budget_applies_without_a_call_argument():
    """The agent's max_steps 1 aborts the second tool round of the run."""
    with LoopTest(config_defaults=loop_config([call('again')], max_steps=1)) as app:
        with pytest.raises(TokeoAiError, match=r'max_steps \(1\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain')


def test_chat_without_tool_stage_governors_runs_lean():
    """An agent without governors runs echo without an Invocation on the trace."""
    with LoopTest(config_defaults=loop_config([call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert tool_contents(result) == ['moin']
        assert not [s for s in result.trace if isinstance(s.object, Invocation)]


def test_chat_with_tool_stage_governors_runs_governed():
    """With trace_audit at the tool stages echo runs as an allowed Invocation."""
    with LoopTest(config_defaults=loop_config([call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='audited')
        assert tool_contents(result) == ['moin']
        invocations = [s.object for s in result.trace if isinstance(s.object, Invocation)]
        assert invocations and all(i.name == 'echo' and i.decision != Invocation.DENY for i in invocations)


def test_chat_refuses_a_tool_the_agent_does_not_offer():
    """A call of shout, not offered by plain, returns an error, the run goes on."""
    script = [dict(calls=[('shout', dict(text='loud'))]), dict(text='done')]
    with LoopTest(config_defaults=loop_config(script)) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'done'
        assert tool_contents(result) == ["error: TokeoAiError: tool 'shout' is not offered by the agent"]


# ask


def test_ask_returns_the_text_of_the_answer():
    """ask('hi') gives 'done' after the echo round, as plain text."""
    with LoopTest(config_defaults=loop_config([call('moin'), dict(text='done')])) as app:
        assert app.ai.ask('hi', agent='plain') == 'done'
