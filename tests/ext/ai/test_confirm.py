"""
Tests for the confirm guard: the channel hook, the answers, and who decided.

A scripted model calls echo; a test channel on the hook ```tokeo_ai_confirm```
answers from a list and records every request. The tests cover the default
without a channel, the answers ```no```, ```once``` and ```run```, the option
```tools```, the auto rules, a person lifting the no of a guard, the limits of
the config no answer lifts, and the stages without a tool call.
"""

import pytest
from tokeo.core.ai import TokeoAiError, TokeoAiResult, TokeoAiGuard, Invocation
from tokeo.core.ai.guards.confirm import CONFIRM_HOOK, TokeoAiConfirmAnswer
from tests.ext.ai.test_loop import LoopTest, loop_config, call, SCRIPT

HUMAN_ONCE = TokeoAiConfirmAnswer('once', who='tom')
HUMAN_RUN = TokeoAiConfirmAnswer('run', who='tom')
HUMAN_NO = TokeoAiConfirmAnswer('no', who='tom', reason='not now')
AUTO_ONCE = TokeoAiConfirmAnswer('once', by='auto', who='a model')


class ForbidWordGuard(TokeoAiGuard):
    """Denies a call whose arguments contain the word bad."""

    def on_call(self, ctx, invocation):
        if 'bad' in repr(invocation.arguments):
            invocation.decision, invocation.reason = Invocation.DENY, 'says bad'


def confirm_config(script, stages=('on_call',), options=None, policy=False, forbid=False):
    """loop_config with an agent asked by confirm at the stages, maybe policed."""
    cfg = loop_config(script)
    cfg['ai']['guards']['ask'] = dict(type='confirm', options=dict(options or {}))
    cfg['ai']['guards']['policy'] = dict(type='tool_policy', options=dict(deny=['echo']))
    cfg['ai']['guards']['forbid'] = dict(type='tests.ext.ai.test_confirm:ForbidWordGuard')
    governors = (['policy'] if policy else []) + (['forbid'] if forbid else []) + [{'ask': list(stages)}]
    cfg['ai']['agents']['careful'] = dict(type='fundi', options=dict(tools=['echo'], governors=governors, sandboxes=['local']))
    return cfg


def run(cfg, *answers, **chat):
    """Chat once as careful; the channel answers in order, none means no channel."""
    asked = []

    def channel(app, request):
        asked.append(request)
        return answers[min(len(asked), len(answers)) - 1] if answers else None

    hooks = [(CONFIRM_HOOK, channel)] if answers else []
    with LoopTest(config_defaults=cfg, hooks=hooks) as app:
        result = app.ai.chat([dict(role='user', content='hi')], agent='careful', **chat)
    return result, asked


def tool_contents(result):
    """The contents of the tool messages on the trace of a run, in order."""
    return [s.object['content'] for s in result.trace if isinstance(s.object, dict) and s.object.get('role') == 'tool']


def test_without_a_channel_the_default_says_no():
    """No channel: echo is skipped, the record says no by default."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]))
    assert tool_contents(result) == ['denied: declined by confirmation']
    assert [(c['answer'], c['by']) for c in result.loopdata.confirmations] == [('no', 'default')]


def test_a_human_once_lets_the_call_run():
    """once by tom: echo runs, the record keeps answer, who and the call."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')]), HUMAN_ONCE)
    assert tool_contents(result) == ['moin']
    record = result.loopdata.confirmations[0]
    assert (record['answer'], record['by'], record['who']) == ('once', 'human', 'tom')
    assert record['subject'] == dict(tool='echo', arguments=dict(text='moin'))
    assert asked[0].stage == 'on_call' and asked[0].answers == ('no', 'once', 'run', 'change')


def test_a_human_no_skips_the_call_with_its_reason():
    """no by tom with reason 'not now': echo is skipped with that reason."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]), HUMAN_NO)
    assert tool_contents(result) == ['denied: not now']


def test_run_is_asked_once_and_recalled_after():
    """run for the first echo: the second echo in the run is not asked again."""
    result, asked = run(confirm_config([call('a'), call('b'), dict(text='done')]), HUMAN_RUN)
    assert tool_contents(result) == ['a', 'b']
    assert len(asked) == 1
    assert [c['recalled'] for c in result.loopdata.confirmations] == [False, True]


def test_the_option_tools_limits_what_is_asked():
    """tools [shout]: echo is not asked and runs."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')], options=dict(tools=['shout'])), HUMAN_NO)
    assert (tool_contents(result), asked) == (['moin'], [])


@pytest.mark.parametrize('rule, content', [('auto_allow', 'moin'), ('auto_deny', 'denied: declined by confirmation')])
def test_an_auto_rule_decides_without_asking(rule, content):
    """auto_allow lets echo run, auto_deny skips it; nobody is asked."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')], options={rule: ['echo']}), HUMAN_NO)
    assert (tool_contents(result), asked) == ([content], [])
    assert result.loopdata.confirmations[0]['by'] == 'auto'


def test_a_human_lifts_the_no_of_a_guard_and_it_is_recorded():
    """tool_policy denies echo, tom answers once: echo runs, the verdict is kept."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')], policy=True), HUMAN_ONCE)
    assert tool_contents(result) == ['moin']
    assert 'not permitted by policy' in asked[0].verdict['reason']
    assert 'not permitted by policy' in result.loopdata.confirmations[0]['verdict']['reason']


def test_an_auto_answer_does_not_lift_the_no_of_a_guard():
    """tool_policy denies echo, an auto once keeps the no."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')], policy=True), AUTO_ONCE)
    assert tool_contents(result) == ["denied: tool 'echo' is not permitted by policy"]


def test_a_default_no_keeps_the_reason_of_the_guard():
    """tool_policy denies echo, no channel: the policy's reason stays."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')], policy=True))
    assert tool_contents(result) == ["denied: tool 'echo' is not permitted by policy"]


def test_no_answer_lifts_the_limits_of_the_config():
    """echo denied by the call: a human once does not make it run."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]), HUMAN_ONCE, deny='echo')
    assert tool_contents(result) == ["error: TokeoAiError: tool 'echo' is denied"]


def test_a_no_at_on_return_replaces_the_result():
    """Asked at on_return, no: the model gets the denial, not 'moin'."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')], stages=('on_return',)), HUMAN_NO)
    assert tool_contents(result) == ['denied: not now']


@pytest.mark.parametrize('stage', ['on_begin', 'on_prompt', 'on_answer', 'on_close'])
def test_a_no_at_a_stage_without_a_call_declines_the_run(stage):
    """A human no at a stage without a tool call: status declined at that stage."""
    result, _ = run(confirm_config([dict(text='done')], stages=(stage,)), HUMAN_NO)
    assert result.status == TokeoAiResult.DECLINED
    assert result.declined == dict(stage=stage, reason='not now', by='human')


def test_the_request_carries_the_userdata_of_the_run():
    """userdata {'user': 'tom'} reaches the channel on the request."""
    _, asked = run(confirm_config([call('moin'), dict(text='done')]), HUMAN_ONCE, userdata=dict(user='tom'))
    assert asked[0].userdata == dict(user='tom')


def test_an_answer_that_is_not_allowed_is_an_error():
    """A channel answering 'maybe' ends the run with TokeoAiError."""
    with pytest.raises(TokeoAiError, match="answer 'maybe' is not one of"):
        run(confirm_config([call('moin'), dict(text='done')]), TokeoAiConfirmAnswer('maybe'))


def test_the_linter_takes_the_confirm_options_and_flags_a_typo():
    """The confirm config lints clean; an option tool, not tools, is an error."""
    from tokeo.core.ai.linter import TokeoAiLinter

    cfg = confirm_config([dict(text='done')], options=dict(tools=['echo'], auto_allow=['echo']))
    with LoopTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        assert [i for i in TokeoAiLinter(app).lint() if i.level == 'error'] == []
    cfg['ai']['guards']['ask']['options'] = dict(tool=['echo'])
    with LoopTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        assert [i.path for i in TokeoAiLinter(app).lint() if i.level == 'error'] == ['ai.guards.ask.options.tool']


# change


def change(content):
    """A human answer that changes the content to the given one."""
    return TokeoAiConfirmAnswer('change', who='tom', content=content)


def test_a_change_of_the_arguments_runs_the_call_with_them():
    """echo('moin') changed to 'neu': echo runs with 'neu', asked once."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')]), change(dict(text='neu')))
    assert tool_contents(result) == ['neu']
    assert len(asked) == 1
    assert result.loopdata.confirmations[0]['content'] == dict(text='neu')


def test_a_change_passes_the_guards_of_the_stage_again():
    """'bad' is denied, changed to 'bad too' denied again, then 'good' runs."""
    script = [call('bad'), dict(text='done')]
    result, asked = run(confirm_config(script, forbid=True), change(dict(text='bad too')), change(dict(text='good')))
    assert tool_contents(result) == ['good']
    assert asked[1].answers == ('no', 'change') and asked[1].verdict['reason'] == 'says bad'


def test_after_a_change_a_no_of_a_guard_cannot_be_lifted():
    """A change still denied, then a human once: not allowed, an error."""
    script = [call('bad'), dict(text='done')]
    with pytest.raises(TokeoAiError, match="answer 'once' is not one of"):
        run(confirm_config(script, forbid=True), change(dict(text='bad too')), TokeoAiConfirmAnswer('once'))


def test_too_many_changes_decline_the_run():
    """A content changed over and over, always denied: the run is declined."""
    result, asked = run(confirm_config([call('bad'), dict(text='done')], forbid=True), change(dict(text='bad again')))
    assert result.status == TokeoAiResult.DECLINED and 'changed more than 5 times' in result.declined['reason']


def test_a_change_at_on_return_replaces_the_result():
    """Asked at on_return, changed to 'edited': the model gets 'edited'."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')], stages=('on_return',)), change('edited'))
    assert tool_contents(result) == ['edited']


def test_a_change_at_on_answer_replaces_the_answer():
    """Asked at on_answer, changed to 'edited': the answer is 'edited'."""
    result, _ = run(confirm_config([dict(text='done')], stages=('on_answer',)), change('edited'))
    assert result.answer.text == 'edited'


def test_a_change_at_on_prompt_reaches_the_model():
    """Asked at on_prompt, the user text changed to 'hello': the model sees it."""

    def channel(app, request):
        return change('hello')

    with LoopTest(config_defaults=confirm_config([dict(text='done')], stages=('on_prompt',)), hooks=[(CONFIRM_HOOK, channel)]) as app:
        app.ai.chat([dict(role='user', content='hi')], agent='careful')
        assert app.ai._provider(SCRIPT).seen[0][-1]['content'] == 'hello'


# terminal


def terminal(*lines):
    """A terminal channel that reads the lines in order, and what it wrote."""
    from tokeo.core.ai.guards.confirm.terminal import terminal_channel

    lines, written = list(lines), []
    return terminal_channel(lambda prompt: lines.pop(0), written.append), written


def test_the_terminal_asks_again_until_an_allowed_answer():
    """x is no answer, o is once: the call echo and the verdict are shown."""
    from tokeo.core.ai.guards.confirm import TokeoAiConfirmRequest

    channel, written = terminal('x', 'o')
    invocation = Invocation(id='c1', name='echo', arguments=dict(text='moin'))
    request = TokeoAiConfirmRequest('on_call', 'ask', invocation, dict(reason='says bad'), ('no', 'once', 'run', 'change'))
    assert channel(None, request).answer == 'once'
    assert written == ['confirm on_call (ask): echo {"text": "moin"}', '  a guard said no: says bad']


def test_the_terminal_reads_changed_arguments_as_json():
    """c, then no JSON, then {"text": "neu"}: the change carries the dict."""
    from tokeo.core.ai.guards.confirm import TokeoAiConfirmRequest

    channel, written = terminal('c', 'neu', '{"text": "neu"}')
    request = TokeoAiConfirmRequest('on_call', 'ask', Invocation(id='c1', name='echo'), None, ('no', 'once', 'run', 'change'))
    answer = channel(None, request)
    assert (answer.answer, answer.content, answer.by) == ('change', dict(text='neu'), 'human')
    assert written[-1].startswith('  the arguments are a JSON object')


@pytest.mark.parametrize('key, out', [('o', 'done'), ('n', 'declined at on_prompt: declined by confirmation (by human)')])
def test_ai_ask_asks_in_the_terminal(monkeypatch, capsys, key, out):
    """ai ask in a terminal: o lets the run answer done, n prints the decline."""
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt='': key)
    cfg = confirm_config([dict(text='done')], stages=('on_prompt',))
    with LoopTest(argv=['ai', 'ask', 'hi', '--agent', 'careful'], config_defaults=cfg) as app:
        app.run()
    assert capsys.readouterr().out.splitlines()[-1] == out
