"""
Tests for the agent loop of the ai extension, ```app.ai.chat``` and ```ask```.

A scripted provider plays a fixed sequence of model answers, so each test
drives the loop through known rounds: tool calls, the follow-up model call,
the limits ```max_steps``` and ```max_loops```, and the path with and without
governors at the tool stages. The tools run in process.
"""

import pytest
from tokeo.core.ai import TokeoAiError, TokeoAiGuard, TokeoAiRunDeclinedError
from tokeo.core.ai.data import Invocation
from tests.ext.ai.lib.ai_tests_helper import SCRIPT, AiTest, make_loop_config, make_call, get_tool_contents

HINT = 'error: ran ok, printed output but set no result: put your answer in the last line'


class DecliningGuard(TokeoAiGuard):
    """Declines the run at the stage named in its option stage."""

    class Meta:
        config_defaults = dict(stage='on_prompt')

    def on_prompt(self, ctx):
        if self._config('stage') == 'on_prompt':
            raise TokeoAiRunDeclinedError('on_prompt', 'not today', by='human')

    def on_answer(self, ctx, result):
        if self._config('stage') == 'on_answer':
            raise TokeoAiRunDeclinedError('on_answer', 'not this answer', by='auto')


def _make_declining_config(stage):
    """make_loop_config with an agent whose one governor declines at the stage."""
    cfg = make_loop_config([dict(text='hello')])
    cfg['ai']['guards']['decline'] = dict(type='tests.ext.ai.test_loop:DecliningGuard', options=dict(stage=stage))
    cfg['ai']['agents']['declining'] = dict(type='fundi', options=dict(governors=['decline']))
    return cfg


# chat


def test_chat_without_tool_calls_answers_at_once():
    """A first answer 'hello' without calls ends the run after 0 tool rounds."""
    with AiTest(config_defaults=make_loop_config([dict(text='hello')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'hello'
        assert result.loopdata.steps == 0


def test_chat_runs_a_tool_and_asks_the_model_again():
    """Echo of 'moin' runs once, the model sees its result and answers 'done'."""
    with AiTest(config_defaults=make_loop_config([make_call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'done'
        assert result.loopdata.steps == 1
        assert get_tool_contents(result) == ['moin']
        # the follow-up model call got the assistant turn and the tool result
        last = app.ai._get_provider(SCRIPT).seen_messages[-1]
        assert [m['role'] for m in last] == ['user', 'assistant', 'tool']


def test_chat_max_steps_reached_aborts():
    """A model that never stops calling aborts at max_steps 2 with an error."""
    with AiTest(config_defaults=make_loop_config([make_call('again')])) as app:
        with pytest.raises(TokeoAiError, match=r'max_steps \(2\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain', max_steps=2)


def test_chat_max_loops_reached_aborts():
    """Three rounds of only failing calls abort at max_loops 3 with an error."""
    script = [dict(calls=[('nope', {})])]
    with AiTest(config_defaults=make_loop_config(script)) as app:
        with pytest.raises(TokeoAiError, match=r'max_loops \(3\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain', max_loops=3)


def test_chat_successful_call_resets_the_failed_loops():
    """Fail, fail, success, fail, fail ends at 'done': max_loops 3 is never hit."""
    fail = dict(calls=[('nope', {})])
    script = [fail, fail, make_call('ok'), fail, fail, dict(text='done')]
    with AiTest(config_defaults=make_loop_config(script)) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain', max_loops=3)
        assert result.answer.text == 'done'
        assert result.loopdata.steps == 5
        assert result.loopdata.failed_loop_cnt == 2


def test_chat_agent_limit_applies_without_a_call_argument():
    """The agent's max_steps 1 aborts the second tool round of the run."""
    with AiTest(config_defaults=make_loop_config([make_call('again')], max_steps=1)) as app:
        with pytest.raises(TokeoAiError, match=r'max_steps \(1\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain')


def test_chat_without_tool_stage_governors_runs_lean():
    """An agent without governors runs echo without an Invocation on the trace."""
    with AiTest(config_defaults=make_loop_config([make_call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert get_tool_contents(result) == ['moin']
        assert not [s for s in result.trace if isinstance(s.object, Invocation)]


def test_chat_with_tool_stage_governors_runs_governed():
    """With trace_audit at the tool stages echo runs as an allowed Invocation."""
    with AiTest(config_defaults=make_loop_config([make_call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='audited')
        assert get_tool_contents(result) == ['moin']
        invocations = [s.object for s in result.trace if isinstance(s.object, Invocation)]
        assert invocations and all(i.name == 'echo' and i.decision != Invocation.DENY for i in invocations)


def test_chat_refuses_a_tool_the_agent_does_not_offer():
    """A call of shout, not offered by plain, returns an error, the run goes on."""
    script = [dict(calls=[('shout', dict(text='loud'))]), dict(text='done')]
    with AiTest(config_defaults=make_loop_config(script)) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert result.answer.text == 'done'
        assert get_tool_contents(result) == ["error: TokeoAiError: tool 'shout' is not offered by the agent"]


@pytest.mark.parametrize('agent', ['plain', 'audited'])
def test_chat_refuses_arguments_that_are_no_json_object(agent):
    """Arguments 'hello' are refused with or without governors, raw on the trace."""
    script = [dict(calls=[('echo', 'hello')]), dict(text='done')]
    with AiTest(config_defaults=make_loop_config(script)) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent=agent)
        assert result.answer.text == 'done'
        assert get_tool_contents(result) == ['error: arguments must be a JSON object, got str']
        # every step of the answer that asked for the call keeps what it sent
        calls = [c for s in result.trace if getattr(s.object, 'tool_calls', None) for c in s.object.tool_calls]
        assert calls and all(c.arguments == 'hello' for c in calls)
        assert result.loopdata.failed_loop_cnt == 1


def _make_code_call(source):
    """A script turn that runs the source by the coding tool."""
    return dict(calls=[('coding', dict(code=source))])


def test_chat_code_that_only_prints_gets_the_hint():
    """print(9) delivers nothing: the model gets the hint, not the printed 9."""
    with AiTest(config_defaults=make_loop_config([_make_code_call('print(9)'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert get_tool_contents(result) == [HINT]
        assert result.loopdata.failed_loop_cnt == 1


def test_chat_code_that_delivers_a_value_gets_it():
    """x = 9 on the last line delivers 9 to the model."""
    with AiTest(config_defaults=make_loop_config([_make_code_call('x = 9'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='plain')
        assert get_tool_contents(result) == ['9']
        assert result.loopdata.failed_loop_cnt == 0


def test_chat_code_that_keeps_printing_ends_at_max_loops():
    """A model that keeps printing stops at max_loops 3, not after 13 rounds."""
    with AiTest(config_defaults=make_loop_config([_make_code_call('print(9)')])) as app:
        with pytest.raises(TokeoAiError, match=r'max_loops \(3\) reached'):
            app.ai.chat([dict(role='user', content='hi')], agent='plain', max_loops=3)


def test_chat_redact_masks_the_call_in_history_and_trace():
    """password=geheim reaches echo masked and stays nowhere in history or trace."""
    with AiTest(config_defaults=make_loop_config([make_call('password=geheim'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='redacting')
        assert get_tool_contents(result) == ['[redacted]']
        # the follow-up model call got the masked assistant turn
        assert 'geheim' not in repr(app.ai._get_provider(SCRIPT).seen_messages[-1])
        assert 'geheim' not in repr([s.object for s in result.trace])


def test_chat_without_an_agent_runs_the_implicit_agent():
    """Without an agent the model gets no tools and a call of echo is refused."""
    with AiTest(config_defaults=make_loop_config([make_call('moin'), dict(text='done')])) as app:
        result = app.ai.chat([dict(role='user', content='hi')])
        assert result.answer.text == 'done'
        assert get_tool_contents(result) == ["error: TokeoAiError: tool 'echo' is not offered by the agent"]


# ask


def test_ask_returns_the_text_of_the_answer():
    """ask('hi') gives 'done' after the echo round, as plain text."""
    with AiTest(config_defaults=make_loop_config([make_call('moin'), dict(text='done')])) as app:
        assert app.ai.ask('hi', agent='plain') == 'done'


# declined


def test_a_decline_at_on_prompt_raises_with_the_run_so_far():
    """Declined at on_prompt: no model call, the error names stage, reason, who."""
    with AiTest(config_defaults=_make_declining_config('on_prompt')) as app:
        with pytest.raises(TokeoAiRunDeclinedError, match='run declined at on_prompt: not today') as err_info:
            app.ai.chat([dict(role='user', content='hi')], agent='declining')
        assert app.ai._get_provider(SCRIPT).seen_messages == []
    assert (err_info.value.stage, err_info.value.reason, err_info.value.by) == ('on_prompt', 'not today', 'human')
    assert err_info.value.result.trace[-1].object.content == dict(decline=dict(stage='on_prompt', reason='not today', by='human'))


def test_a_decline_at_on_answer_keeps_the_declined_answer_off_the_result():
    """Declined at on_answer: the answer 'hello' is on the trace, not the result."""
    with AiTest(config_defaults=_make_declining_config('on_answer')) as app:
        with pytest.raises(TokeoAiRunDeclinedError) as err_info:
            app.ai.chat([dict(role='user', content='hi')], agent='declining')
    result = err_info.value.result
    assert (err_info.value.by, result.answer.text) == ('auto', '')
    assert any(getattr(step.object, 'text', None) == 'hello' for step in result.trace)
