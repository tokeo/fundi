"""
Tests for the confirm guard: the state the guards reached, the answers, notes.

A scripted model calls echo; a test channel on the hook ```tokeo_ai_confirm```
answers from a list and records every request. The tests cover the default,
the answers ```yes```, ```yes_run```, ```no``` and ```no_retry```, that a confirm
never turns a no into a yes, notes the guards judge anew with, the limits of
the config, the stages without a tool call, and the terminal channel.
"""

import pytest
from tokeo.core.ai import TokeoAiError, TokeoAiRunDeclinedError, TokeoAiGuard, Invocation
from tokeo.core.ai.guards.confirm import CONFIRM_HOOK, TokeoAiConfirmAnswer, TokeoAiConfirmRequest, TokeoAiConfirmRetry
from tests.ext.ai.test_loop import LoopTest, loop_config, call

YES = TokeoAiConfirmAnswer('yes', by='human', who='tom')
YES_RUN = TokeoAiConfirmAnswer('yes_run', by='human', who='tom')
NO = TokeoAiConfirmAnswer('no', by='human', who='tom', note='not now')
TRUST = TokeoAiConfirmAnswer('no_retry', by='human', who='tom', note='trust it')


class ForbidWordGuard(TokeoAiGuard):
    """Denies a call that says bad, unless a note says trust it."""

    def on_call(self, ctx, invocation):
        trusted = any(note.get('note') == 'trust it' for note in ctx.notes)
        if 'bad' in repr(invocation.arguments) and not trusted:
            invocation.decision, invocation.reason = Invocation.DENY, 'says bad'


def confirm_config(script, stages=('on_call',), options=None, forbid=False):
    """loop_config with an agent careful, confirmed at the stages, maybe forbid."""
    cfg = loop_config(script)
    cfg['ai']['guards']['ask'] = dict(type='confirm', options=dict(options or {}))
    cfg['ai']['guards']['forbid'] = dict(type='tests.ext.ai.test_confirm:ForbidWordGuard')
    governors = (['forbid'] if forbid else []) + [{'ask': list(stages)}]
    cfg['ai']['agents']['careful'] = dict(type='fundi', options=dict(tools=['echo'], governors=governors, sandboxes=['local']))
    return cfg


def run(cfg, *answers, asked=None, **chat):
    """
    Chat once as careful; the channel answers in order, none means no channel.
    The requests go to asked, a list of the caller's to keep them on a raise.
    """
    asked = [] if asked is None else asked

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
    """No channel: echo is skipped, the record says no by default, allow to deny."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]))
    assert tool_contents(result) == ['denied: declined by confirmation']
    record = result.confirmations[0]
    assert (record['answer'], record['by']) == ('no', 'default')
    assert (record['state_before']['decision'], record['state_after']) == ('allow', 'deny')


@pytest.mark.parametrize('word, content', [('moin', 'moin'), ('bad', 'denied: says bad')])
def test_the_default_yes_confirms_the_state_of_the_guards(word, content):
    """default yes: a yes of the guards runs echo, a no stays a no."""
    result, _ = run(confirm_config([call(word), dict(text='done')], options=dict(default='yes'), forbid=True))
    assert tool_contents(result) == [content]


def test_a_yes_confirms_the_state_and_is_recorded():
    """yes by tom: echo runs; the request shows the state, the record the answer."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')]), YES)
    assert tool_contents(result) == ['moin']
    assert asked[0].answers == ('yes', 'yes_run', 'no', 'no_retry')
    assert asked[0].state == dict(decision='allow', reason=None)
    record = result.confirmations[0]
    assert (record['answer'], record['by'], record['who'], record['state_after']) == ('yes', 'human', 'tom', 'allow')
    assert record['subject'] == dict(tool='echo', arguments=dict(text='moin'))


def test_a_yes_never_turns_the_no_of_a_guard_into_a_yes():
    """forbid denies bad, tom answers yes: the call stays denied."""
    result, asked = run(confirm_config([call('bad'), dict(text='done')], forbid=True), YES)
    assert tool_contents(result) == ['denied: says bad']
    assert asked[0].state == dict(decision='deny', reason='says bad')


def test_a_no_turns_a_yes_into_a_no_with_its_note():
    """no by tom with the note 'not now': echo is skipped with that note."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]), NO)
    assert tool_contents(result) == ['denied: not now']


def test_yes_run_is_recalled_only_for_the_same_question():
    """yes_run on echo a: a second echo a is not asked, echo b is."""
    result, asked = run(confirm_config([call('a'), call('a'), call('b'), dict(text='done')]), YES_RUN)
    assert tool_contents(result) == ['a', 'a', 'b']
    assert [request.subject.arguments for request in asked] == [dict(text='a'), dict(text='b')]
    assert [(c['recalled'], c['by']) for c in result.confirmations] == [(False, 'human'), (True, 'human'), (False, 'human')]


def test_no_retry_lets_the_guards_judge_anew_with_the_note():
    """forbid denies bad, no_retry 'trust it': forbid now allows, then a yes."""
    result, asked = run(confirm_config([call('bad'), dict(text='done')], forbid=True), TRUST, YES)
    assert tool_contents(result) == ['bad']
    assert [request.state['decision'] for request in asked] == ['deny', 'allow']
    assert [note['note'] for note in asked[1].notes] == ['trust it']
    assert [c['answer'] for c in result.confirmations] == ['no_retry', 'yes']


def test_the_dialog_is_asked_again_after_each_retry_until_yes():
    """Two no_retry that do not help, then yes: the no of forbid stays."""
    wrong = TokeoAiConfirmAnswer('no_retry', by='human', note='still')
    result, asked = run(confirm_config([call('bad'), dict(text='done')], forbid=True), wrong, wrong, YES)
    assert len(asked) == 3 and tool_contents(result) == ['denied: says bad']


def test_no_retry_needs_a_note():
    """no_retry without a note is an error."""
    with pytest.raises(TokeoAiError, match='no_retry needs a note'):
        run(confirm_config([call('moin'), dict(text='done')]), TokeoAiConfirmAnswer('no_retry', by='human'))


def test_more_than_ten_retries_decline_the_run():
    """A channel that always retries: after 10 retries the run is declined."""
    again = TokeoAiConfirmAnswer('no_retry', by='auto', note='again')
    asked = []
    with pytest.raises(TokeoAiRunDeclinedError, match='retried more than 10 times'):
        run(confirm_config([call('bad'), dict(text='done')], forbid=True), again, asked=asked)
    assert len(asked) == 11


def test_notes_of_an_earlier_run_reach_the_guards():
    """notes with 'trust it' from before: forbid allows bad at once."""
    notes = [dict(stage='on_call', note='trust it')]
    result, asked = run(confirm_config([call('bad'), dict(text='done')], forbid=True), YES, notes=notes)
    assert tool_contents(result) == ['bad']
    assert asked[0].notes == notes


def test_a_note_on_a_yes_is_kept_for_the_run_and_the_result():
    """yes with a note: the record carries it, the next request shows it."""
    noted = TokeoAiConfirmAnswer('yes', by='auto', who='a model', note='+20 % for this header')
    result, asked = run(confirm_config([call('a'), call('b'), dict(text='done')]), noted)
    assert result.confirmations[0]['note'] == '+20 % for this header'
    assert [note['note'] for note in asked[1].notes] == ['+20 % for this header']


def test_the_option_tools_limits_what_is_asked():
    """tools [shout]: echo is not asked and runs."""
    result, asked = run(confirm_config([call('moin'), dict(text='done')], options=dict(tools=['shout'])), NO)
    assert (tool_contents(result), asked) == (['moin'], [])


def test_no_answer_lifts_the_limits_of_the_config():
    """echo denied by the call: a yes does not make it run."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')]), YES, deny='echo')
    assert tool_contents(result) == ["error: TokeoAiError: tool 'echo' is denied"]


def test_a_no_at_on_return_replaces_the_result():
    """Asked at on_return, no: the model gets the denial, not 'moin'."""
    result, _ = run(confirm_config([call('moin'), dict(text='done')], stages=('on_return',)), NO)
    assert tool_contents(result) == ['denied: not now']


@pytest.mark.parametrize('stage', ['on_begin', 'on_prompt', 'on_answer', 'on_close'])
def test_a_no_at_a_stage_without_a_call_declines_the_run(stage):
    """A no at a stage without a tool call declines the run, its record stays."""
    with pytest.raises(TokeoAiRunDeclinedError) as declined:
        run(confirm_config([dict(text='done')], stages=(stage,)), NO)
    assert (declined.value.stage, declined.value.reason, declined.value.by) == (stage, 'not now', 'human')
    assert [c['answer'] for c in declined.value.result.confirmations] == ['no']


def test_no_retry_at_a_stage_without_a_call_asks_again():
    """no_retry at on_answer, then yes: the run answers done."""
    result, asked = run(confirm_config([dict(text='done')], stages=('on_answer',)), TRUST, YES)
    assert (result.answer.text, len(asked)) == ('done', 2)


def test_the_request_carries_the_userdata_of_the_run():
    """userdata {'user': 'tom'} reaches the channel on the request."""
    _, asked = run(confirm_config([call('moin'), dict(text='done')]), YES, userdata=dict(user='tom'))
    assert asked[0].userdata == dict(user='tom')


@pytest.mark.parametrize(
    'answer, message',
    [
        (TokeoAiConfirmAnswer('maybe', by='human'), "answer 'maybe' is not one of"),
        (TokeoAiConfirmAnswer('yes', by='default'), 'must say by'),
    ],
    ids=['unknown_answer', 'by_default'],
)
def test_a_wrong_answer_is_an_error(answer, message):
    """An unknown answer, or by default from a channel, ends the run in an error."""
    with pytest.raises(TokeoAiError, match=message):
        run(confirm_config([call('moin'), dict(text='done')]), answer)


def test_ask_raises_when_the_run_is_declined():
    """ask declined at on_prompt raises TokeoAiRunDeclinedError with the stage."""

    def channel(app, request):
        return NO

    with LoopTest(config_defaults=confirm_config([dict(text='done')], stages=('on_prompt',)), hooks=[(CONFIRM_HOOK, channel)]) as app:
        with pytest.raises(TokeoAiRunDeclinedError) as declined:
            app.ai.ask('hi', agent='careful')
    assert (declined.value.stage, declined.value.reason) == ('on_prompt', 'not now')


def test_the_linter_takes_the_confirm_options_and_flags_a_typo():
    """The confirm config lints clean; an option tool, not tools, is an error."""
    from tokeo.core.ai.linter import TokeoAiLinter

    cfg = confirm_config([dict(text='done')], options=dict(tools=['echo'], default='yes'))
    with LoopTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        assert [i for i in TokeoAiLinter(app).lint() if i.level == 'error'] == []
    cfg['ai']['guards']['ask']['options'] = dict(tool=['echo'])
    with LoopTest(argv=['ai', 'lint'], config_defaults=cfg) as app:
        assert [i.path for i in TokeoAiLinter(app).lint() if i.level == 'error'] == ['ai.guards.ask.options.tool']


# terminal


def terminal(*lines):
    """A terminal channel that reads the lines in order, and what it wrote."""
    from tokeo.core.ai.guards.confirm.terminal import terminal_channel

    lines, written = list(lines), []
    return terminal_channel(lambda prompt: lines.pop(0), written.append), written


def request(state, notes=()):
    """A confirm request at on_call for echo('moin') in the given state."""
    invocation = Invocation(id='c1', name='echo', arguments=dict(text='moin'))
    return TokeoAiConfirmRequest('on_call', 'ask', invocation, state, list(notes), ('yes', 'yes_run', 'no', 'no_retry'))


def test_the_terminal_shows_the_state_and_asks_until_an_answer():
    """x is no answer, y is yes: the call, the state and the notes are shown."""
    channel, written = terminal('x', 'y', '')
    answer = channel(None, request(dict(decision='deny', reason='says bad'), [dict(note='trust it')]))
    assert (answer.answer, answer.by, answer.note) == ('yes', 'human', None)
    assert written == ['confirm on_call (ask): echo {"text": "moin"}', '  state: deny (says bad)', '  note: trust it']


def test_the_terminal_asks_for_the_note_of_a_no_retry():
    """t, an empty note, then 'trust it': the no_retry carries the note."""
    channel, _ = terminal('t', '', 'trust it')
    answer = channel(None, request(dict(decision='deny', reason='says bad')))
    assert (answer.answer, answer.note) == ('no_retry', 'trust it')


@pytest.mark.parametrize('key, out', [('y', 'done'), ('n', 'declined at on_prompt: declined by confirmation (by human)')])
def test_ai_ask_asks_in_the_terminal(monkeypatch, capsys, key, out):
    """ai ask in a terminal: y lets the run answer done, n prints the decline."""
    lines = [key, '']
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda prompt='': lines.pop(0))
    cfg = confirm_config([dict(text='done')], stages=('on_prompt',))
    with LoopTest(argv=['ai', 'ask', 'hi', '--agent', 'careful'], config_defaults=cfg) as app:
        app.run()
    assert capsys.readouterr().out.splitlines()[-1] == out


def test_a_retry_passes_an_except_exception_of_a_governor():
    """TokeoAiConfirmRetry is a signal to the loop, no Exception to catch."""
    assert not issubclass(TokeoAiConfirmRetry, Exception)
