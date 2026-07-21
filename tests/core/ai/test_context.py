"""
Tests for the run context, its trace of steps, and its caches (tokeo core).

Covers ```TokeoAiContext``` as the run's manager: the trace as a list of
```TraceStep``` (origin + object), the bare-object caches
(```messages```/```invocations```/```results```), ```track``` (a fresh object)
and ```supersede``` (a guard's step, with the identity rule and the cache swap),
```cur_invocation```, ```status```, ```userdata```, and the ```ChatMessage``` dict
subclass. These are the mechanics in isolation; the full LLM loop that drives
them is exercised by the Spiral tests.
"""

import copy
from tokeo.core.ai import (
    TokeoAiContext,
    TokeoAiLoopdata,
    TokeoAiTurndata,
    TraceStep,
    ChatMessage,
    Invocation,
    ChatResult,
    ToolResult,
    TokeoAiError,
)


def test_invocation_decision_constants_carry_the_expected_values():
    # the decision constants are the single source for the allow/deny values;
    # pin them to their raw strings so a careless change to either is caught
    # (the literal-based guard tests cross-check the same values from the other
    # side, so constant and literal must agree)
    assert Invocation.ALLOW == 'allow'
    assert Invocation.DENY == 'deny'
    # a fresh invocation allows by default
    assert Invocation(id='t1', name='calc').decision == Invocation.ALLOW


def test_context_seeds_incoming_messages_as_chat_messages():
    # the incoming conversation is recorded at construction: a step on the trace
    # (originated by the context) and the bare message in the messages cache
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    assert len(ctx.messages) == 1
    assert len(ctx.trace) == 1
    message = ctx.messages[0]
    assert isinstance(message, ChatMessage)
    assert isinstance(message, dict)
    # it is still the plain OpenAI-style dict the provider expects
    assert message['role'] == 'user'
    assert message['content'] == 'hi'
    # the trace entry is a step that wraps the message; the context is its origin
    step = ctx.trace[0]
    assert isinstance(step, TraceStep)
    assert step.object is message
    assert step.origin is ctx
    assert step.changed is True


def test_context_empty_when_unseeded():
    # no messages means empty caches and trace, and fresh zero counters
    ctx = TokeoAiContext()
    assert ctx.trace == []
    assert ctx.messages == []
    assert ctx.invocations == []
    assert ctx.results == []
    assert ctx.cur_invocation is None
    assert isinstance(ctx.loopdata, TokeoAiLoopdata)
    assert ctx.loopdata.steps == 0
    assert ctx.loopdata.failed_loops == 0
    assert isinstance(ctx.turndata, TokeoAiTurndata)
    assert ctx.turndata == {}


def test_turndata_preset_seeds_values_one_to_one():
    # a preset seeds the run's turndata; the type stays TokeoAiTurndata
    src = {'chain': ['toolA'], 'depth': 1}
    ctx = TokeoAiContext(messages=[], turndata_preset=src)
    assert isinstance(ctx.turndata, TokeoAiTurndata)
    assert ctx.turndata == {'chain': ['toolA'], 'depth': 1}
    # taken 1:1, NOT deep-copied: the inner object is the same one
    assert ctx.turndata['chain'] is src['chain']


def test_turndata_preset_absent_starts_empty():
    # without a preset the run still starts with an empty turndata
    ctx = TokeoAiContext(messages=[])
    assert ctx.turndata == {}


def test_track_records_a_step_and_caches_the_bare_object():
    # track appends a step to the trace and the bare object to its cache; it
    # returns the same object so a caller can keep the reference
    ctx = TokeoAiContext()
    origin = object()
    invocation = Invocation(id='t1', name='calc', arguments={})
    returned = ctx.track(origin, invocation)
    assert returned is invocation
    # the cache holds the bare object
    assert ctx.invocations == [invocation]
    # the trace holds a step wrapping it, attributed to the origin
    assert len(ctx.trace) == 1
    assert ctx.trace[0].object is invocation
    assert ctx.trace[0].origin is origin
    # other caches stay empty -- isinstance filed it only into invocations
    assert ctx.messages == []
    assert ctx.results == []


def test_caches_hold_objects_trace_holds_steps():
    # the two are different kinds of list: the cache is bare objects, the trace
    # is steps wrapping them
    ctx = TokeoAiContext()
    invocation = Invocation(id='t1', name='calc')
    ctx.track(object(), invocation)
    assert ctx.invocations[0] is invocation
    assert isinstance(ctx.trace[0], TraceStep)
    assert ctx.trace[0].object is invocation


def test_trace_keeps_chronological_order_across_kinds():
    # the trace is the full ordered history of steps; each cache collects only
    # its kind, as bare objects
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'go'}])
    origin = object()
    result = ctx.track(origin, ChatResult(text='', tool_calls=[]))
    invocation = ctx.track(origin, Invocation(id='t1', name='calc'))
    answer = ctx.track(origin, ChatMessage(role='assistant', content='done'))
    # trace order is exactly the order things were tracked (as steps)
    assert [step.object for step in ctx.trace] == [ctx.messages[0], result, invocation, answer]
    # and the caches partition by type, holding bare objects
    assert ctx.results == [result]
    assert ctx.invocations == [invocation]
    assert ctx.messages == [ctx.trace[0].object, answer]


def test_tracked_returns_the_bare_object_cache_by_type():
    # tracked(Type) returns the same live cache list the property exposes
    ctx = TokeoAiContext()
    result = ctx.track(object(), ChatResult(text='hi', tool_calls=[]))
    assert ctx.tracked(ChatResult) == [result]
    assert ctx.tracked(ChatResult) is ctx.results


def test_tracked_unknown_kind_is_empty_not_error():
    # an uncached kind yields an empty list, so a caller can iterate without
    # guarding for absence (ToolResult is not a cached kind)
    ctx = TokeoAiContext()
    assert ctx.tracked(ToolResult) == []


def test_supersede_with_none_records_an_unchanged_step_and_keeps_the_cache():
    # a guard that returns None added no new object: a step is still recorded
    # (the guard ran, attributable), but the cache is untouched and the working
    # reference is unchanged
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    guard = object()
    returned = ctx.supersede(guard, None, invocation)
    assert returned is invocation
    # a step was appended, marked unchanged, originated by the guard
    assert len(ctx.trace) == 2
    assert ctx.trace[-1].origin is guard
    assert ctx.trace[-1].changed is False
    assert ctx.trace[-1].object is invocation
    # the cache still holds exactly the one invocation
    assert ctx.invocations == [invocation]


def test_supersede_with_same_object_is_also_unchanged():
    # returning the same object (not a fresh one) is the same as returning None:
    # no new state, an unchanged step
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    returned = ctx.supersede(object(), invocation, invocation)
    assert returned is invocation
    assert ctx.trace[-1].changed is False
    assert ctx.invocations == [invocation]


def test_supersede_with_a_new_object_swaps_the_cache_and_records_a_changed_step():
    # a guard that returns a fresh copy supersedes the last cache entry of its
    # kind; the step is marked changed and the copy becomes the working reference
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    guard = object()
    revised = copy.deepcopy(invocation)
    revised.decision = Invocation.DENY
    returned = ctx.supersede(guard, revised, invocation)
    assert returned is revised
    # the cache now holds the copy in place of the original (still one entry)
    assert ctx.invocations == [revised]
    assert ctx.cur_invocation is revised
    # the trace grew: the original track step plus the changed supersede step
    assert len(ctx.trace) == 2
    assert ctx.trace[-1].changed is True
    assert ctx.trace[-1].object is revised
    assert ctx.trace[-1].origin is guard


def test_supersede_with_wrong_type_raises():
    # an on_return guard must return an Invocation; returning another kind is a
    # bug, so supersede fails loud rather than mis-filing it
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    try:
        ctx.supersede(object(), 'not an invocation', invocation)
        assert False, 'expected TokeoAiError'
    except TokeoAiError:
        pass


def test_cur_invocation_is_the_latest_tool_call():
    # cur_invocation is a convenience over the invocations cache's last entry
    ctx = TokeoAiContext()
    assert ctx.cur_invocation is None
    first = ctx.track(object(), Invocation(id='t1', name='calc'))
    assert ctx.cur_invocation is first
    second = ctx.track(object(), Invocation(id='t2', name='time'))
    assert ctx.cur_invocation is second


def test_status_counters_are_mutable_in_place():
    # the loop advances the counters on the status struct; they live there, not
    # on the context directly
    ctx = TokeoAiContext()
    ctx.loopdata.steps += 1
    ctx.loopdata.failed_loops += 2
    assert ctx.loopdata.steps == 1
    assert ctx.loopdata.failed_loops == 2


def test_userdata_defaults_to_none():
    # no userdata given means the field is present and None, so a guard can
    # read it unconditionally
    ctx = TokeoAiContext()
    assert ctx.userdata is None


def test_userdata_is_carried_unchanged_as_the_same_reference():
    # the framework never copies or interprets userdata: whatever the caller
    # set is the exact same object on the context (a string, a dict, anything)
    marker = {'session': 'abc', 'nested': [1, 2]}
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}], userdata=marker)
    assert ctx.userdata is marker
    # a plain value works just as well
    ctx2 = TokeoAiContext(userdata='client-42')
    assert ctx2.userdata == 'client-42'


def test_track_and_supersede_record_the_stage_on_the_step():
    # the stage that produced a step is recorded on it, so the trace is readable
    # without inferring the station from order; a loop track has no stage (None)
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    assert ctx.trace[-1].stage is None
    revised = copy.copy(invocation)
    ctx.supersede(object(), revised, invocation, stage='on_call')
    assert ctx.trace[-1].stage == 'on_call'


def test_refine_messages_in_place_records_an_unchanged_step():
    # a begin/prompt guard that mutates ctx.messages in place returns None; the
    # step is recorded (the guard is on the trace) but changed is False and the
    # cache is left as the same live list
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    before = ctx.messages
    returned = ctx.refine_messages(object(), None, stage='on_begin')
    assert returned is before
    assert ctx.trace[-1].changed is False
    assert ctx.trace[-1].stage == 'on_begin'


def test_refine_messages_same_list_is_also_unchanged():
    # returning the live messages list itself counts as in place, not a replace
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    returned = ctx.refine_messages(object(), ctx.messages, stage='on_prompt')
    assert returned is ctx.messages
    assert ctx.trace[-1].changed is False


def test_refine_messages_with_a_new_list_replaces_the_whole_conversation():
    # a guard that hands back a fresh conversation (a system turn injected)
    # replaces the WHOLE messages cache in place, records a changed step, and
    # wraps plain dicts as ChatMessage so the cache stays typed
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    live = ctx.messages
    new_convo = [{'role': 'system', 'content': 'be brief'}, {'role': 'user', 'content': 'hi'}]
    returned = ctx.refine_messages(object(), new_convo, stage='on_prompt')
    # same live list object, new content, every entry a ChatMessage
    assert returned is live
    assert [m['role'] for m in ctx.messages] == ['system', 'user']
    assert all(isinstance(m, ChatMessage) for m in ctx.messages)
    assert ctx.trace[-1].changed is True
    assert ctx.trace[-1].stage == 'on_prompt'


def test_refine_messages_with_an_invalid_list_raises():
    # a returned conversation must be a list of mappings; anything else is a
    # guard bug, so refine_messages fails loud rather than corrupting the cache
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    try:
        ctx.refine_messages(object(), ['not a message'], stage='on_prompt')
        assert False, 'expected TokeoAiError'
    except TokeoAiError:
        pass


def test_refine_messages_step_is_a_snapshot_not_the_live_list():
    # a pre-model step must show the conversation as it stood at that stage; the
    # live cache keeps growing, so the step holds a shallow copy, not the cache
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    ctx.refine_messages(object(), None, stage='on_begin')
    begin_step = ctx.trace[-1]
    # the conversation grows after the begin step
    ctx.track(object(), ChatMessage(role='assistant', content='later'))
    # the begin step still shows only the one message it saw, not the new one
    assert len(begin_step.object) == 1
    assert begin_step.object is not ctx.messages
    assert len(ctx.messages) == 2


def test_trace_false_skips_the_trace_but_fills_the_caches():
    # with trace recording off, no steps are appended, but the typed caches
    # still fill so guards keep working off messages/invocations/results
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}], trace=False)
    ctx.track(object(), Invocation(id='t1', name='calc'))
    ctx.supersede(object(), None, ctx.cur_invocation, stage='on_call')
    ctx.refine_messages(object(), None, stage='on_prompt')
    # the history is empty, the state by kind is intact
    assert ctx.trace == []
    assert len(ctx.messages) == 1
    assert ctx.cur_invocation.name == 'calc'
