"""
Tests for the run context, its trace of steps, and its caches (tokeo core).

Covers ```TokeoAiContext``` as the run's manager: the trace as a list of
```TraceStep``` (origin + object), the bare-object caches
(```messages```/```invocations```/```results```), ```track``` (a fresh object)
and ```supersede``` (a guard's step, with the identity rule and the cache swap),
```current_invocation```, ```status```, ```userdata```, and the
```TokeoAiChatMessage``` dict subclass. These are the mechanics in isolation; the
full LLM loop that drives them is exercised by the Spiral tests.
"""

import copy
from tokeo.core.ai import (
    TokeoAiContext,
    TokeoAiLoopdata,
    TokeoAiTurndata,
    TokeoAiTraceNote,
    TraceStep,
    TokeoAiChatMessage,
    Invocation,
    TokeoAiChatResult,
    ToolResult,
    TokeoAiError,
)


def test_invocation_decision_constants_carry_the_expected_values():
    """Invocation.ALLOW is 'allow', DENY is 'deny', and a fresh call allows."""
    # the constants are the single source for the allow/deny values; pinned to
    # their raw strings so a careless change is caught (the literal-based guard
    # tests cross-check the same values from the other side, so constant and
    # literal must agree)
    assert Invocation.ALLOW == 'allow'
    assert Invocation.DENY == 'deny'
    # a fresh invocation allows by default
    assert Invocation(id='t1', name='calc').decision == Invocation.ALLOW


def test_context_seeds_incoming_messages_as_chat_messages():
    """An incoming dict is cached as TokeoAiChatMessage, traced with origin ctx."""
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    assert len(ctx.messages) == 1
    assert len(ctx.trace) == 1
    message = ctx.messages[0]
    assert isinstance(message, TokeoAiChatMessage)
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
    """Without messages the trace and caches are empty and the counters zero."""
    ctx = TokeoAiContext()
    assert ctx.trace == []
    assert ctx.messages == []
    assert ctx.invocations == []
    assert ctx.results == []
    assert ctx.current_invocation is None
    assert isinstance(ctx.loopdata, TokeoAiLoopdata)
    assert ctx.loopdata.steps == 0
    assert ctx.loopdata.failed_loop_cnt == 0
    assert isinstance(ctx.turndata, TokeoAiTurndata)
    assert ctx.turndata == {}


def test_turndata_preset_seeds_values_one_to_one():
    """A turndata seeds a TokeoAiTurndata 1:1, not deep-copied."""
    src = {'chain': ['toolA'], 'depth': 1}
    ctx = TokeoAiContext(messages=[], turndata=src)
    assert isinstance(ctx.turndata, TokeoAiTurndata)
    assert ctx.turndata == {'chain': ['toolA'], 'depth': 1}
    # taken 1:1, NOT deep-copied: the inner object is the same one
    assert ctx.turndata['chain'] is src['chain']


def test_turndata_preset_absent_starts_empty():
    """Without a turndata the run starts with an empty turndata."""
    ctx = TokeoAiContext(messages=[])
    assert ctx.turndata == {}


def test_track_records_a_step_and_caches_the_bare_object():
    """track traces a step by its origin, caches the Invocation and returns it."""
    # returning the same object lets a caller keep the reference
    ctx = TokeoAiContext()
    origin = object()
    invocation = Invocation(id='t1', name='calc', arguments={})
    result = ctx.track(origin, invocation)
    assert result is invocation
    # the cache holds the bare object
    assert ctx.invocations == [invocation]
    # the trace holds a step wrapping it, attributed to the origin
    assert len(ctx.trace) == 1
    assert ctx.trace[0].object is invocation
    assert ctx.trace[0].origin is origin
    # other caches stay empty; isinstance filed it only into invocations
    assert ctx.messages == []
    assert ctx.results == []


def test_caches_hold_objects_trace_holds_steps():
    """The invocations cache holds the bare Invocation, the trace a TraceStep."""
    ctx = TokeoAiContext()
    invocation = Invocation(id='t1', name='calc')
    ctx.track(object(), invocation)
    assert ctx.invocations[0] is invocation
    assert isinstance(ctx.trace[0], TraceStep)
    assert ctx.trace[0].object is invocation


def test_trace_keeps_chronological_order_across_kinds():
    """The trace keeps the tracked order across kinds, each cache only its kind."""
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'go'}])
    origin = object()
    result = ctx.track(origin, TokeoAiChatResult(text='', tool_calls=[]))
    invocation = ctx.track(origin, Invocation(id='t1', name='calc'))
    answer = ctx.track(origin, TokeoAiChatMessage(role='assistant', content='done'))
    # trace order is exactly the order things were tracked (as steps)
    assert [step.object for step in ctx.trace] == [ctx.messages[0], result, invocation, answer]
    # and the caches partition by type, holding bare objects
    assert ctx.results == [result]
    assert ctx.invocations == [invocation]
    assert ctx.messages == [ctx.trace[0].object, answer]


def test_get_tracked_returns_the_bare_object_cache_by_type():
    """get_tracked(TokeoAiChatResult) is the same live list as ctx.results."""
    ctx = TokeoAiContext()
    result = ctx.track(object(), TokeoAiChatResult(text='hi', tool_calls=[]))
    assert ctx.get_tracked(TokeoAiChatResult) == [result]
    assert ctx.get_tracked(TokeoAiChatResult) is ctx.results


def test_get_tracked_unknown_kind_is_empty_not_error():
    """get_tracked(ToolResult), not a cached kind, returns [] instead of raising."""
    # so a caller can iterate without guarding for absence
    ctx = TokeoAiContext()
    assert ctx.get_tracked(ToolResult) == []


def test_supersede_with_none_records_an_unchanged_step_and_keeps_the_cache():
    """A guard returning None gets an unchanged step; cache and reference stay."""
    # the step is still recorded: the guard ran and is attributable
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    guard = object()
    result = ctx.supersede(guard, None, invocation)
    assert result is invocation
    # a step was appended, marked unchanged, originated by the guard
    assert len(ctx.trace) == 2
    assert ctx.trace[-1].origin is guard
    assert ctx.trace[-1].changed is False
    assert ctx.trace[-1].object is invocation
    # the cache still holds exactly the one invocation
    assert ctx.invocations == [invocation]


def test_supersede_with_same_object_is_also_unchanged():
    """A guard returning the same object gets an unchanged step, as for None."""
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    result = ctx.supersede(object(), invocation, invocation)
    assert result is invocation
    assert ctx.trace[-1].changed is False
    assert ctx.invocations == [invocation]


def test_supersede_with_a_new_object_swaps_the_cache_and_records_a_changed_step():
    """A guard's fresh copy replaces the cached Invocation, the step is changed."""
    # the copy becomes the working reference
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    guard = object()
    revised_invocation = copy.deepcopy(invocation)
    revised_invocation.decision = Invocation.DENY
    result = ctx.supersede(guard, revised_invocation, invocation)
    assert result is revised_invocation
    # the cache now holds the copy in place of the original (still one entry)
    assert ctx.invocations == [revised_invocation]
    assert ctx.current_invocation is revised_invocation
    # the trace grew: the original track step plus the changed supersede step
    assert len(ctx.trace) == 2
    assert ctx.trace[-1].changed is True
    assert ctx.trace[-1].object is revised_invocation
    assert ctx.trace[-1].origin is guard


def test_supersede_with_wrong_type_raises():
    """A str returned for an Invocation makes supersede raise TokeoAiError."""
    # an on_return guard must return an Invocation; another kind is a guard
    # bug, so supersede fails loud rather than mis-filing it
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    try:
        ctx.supersede(object(), 'not an invocation', invocation)
        assert False, 'expected TokeoAiError'
    except TokeoAiError:
        pass


def test_cur_invocation_is_the_latest_tool_call():
    """current_invocation is the last cached Invocation: None, then t1, then t2."""
    ctx = TokeoAiContext()
    assert ctx.current_invocation is None
    first = ctx.track(object(), Invocation(id='t1', name='calc'))
    assert ctx.current_invocation is first
    second = ctx.track(object(), Invocation(id='t2', name='time'))
    assert ctx.current_invocation is second


def test_status_counters_are_mutable_in_place():
    """The loop counters live on ctx.loopdata and are advanced there in place."""
    ctx = TokeoAiContext()
    ctx.loopdata.steps += 1
    ctx.loopdata.failed_loop_cnt += 2
    assert ctx.loopdata.steps == 1
    assert ctx.loopdata.failed_loop_cnt == 2


def test_userdata_defaults_to_none():
    """Without userdata the field is there and None, so a guard can read it."""
    ctx = TokeoAiContext()
    assert ctx.userdata is None


def test_userdata_is_carried_unchanged_as_the_same_reference():
    """userdata is the caller's very object, a dict or 'client-42', never copied."""
    # the framework never copies or interprets userdata
    marker = {'session': 'abc', 'nested': [1, 2]}
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}], userdata=marker)
    assert ctx.userdata is marker
    # a plain value works just as well
    ctx2 = TokeoAiContext(userdata='client-42')
    assert ctx2.userdata == 'client-42'


def test_track_and_supersede_record_the_stage_on_the_step():
    """A loop track records stage None, a supersede at on_call records 'on_call'."""
    # the stage on the step makes the trace readable without inferring the
    # stage from order
    ctx = TokeoAiContext()
    invocation = ctx.track(object(), Invocation(id='t1', name='calc'))
    assert ctx.trace[-1].stage is None
    revised_invocation = copy.copy(invocation)
    ctx.supersede(object(), revised_invocation, invocation, stage='on_call')
    assert ctx.trace[-1].stage == 'on_call'


def test_refine_messages_in_place_records_an_unchanged_step():
    """A guard refining in place gets an unchanged on_begin step, list kept."""
    # a begin/prompt guard that mutates ctx.messages in place returns None; it
    # is on the trace and the cache stays the same live list
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    before = ctx.messages
    result = ctx.refine_messages(object(), None, stage='on_begin')
    assert result is before
    assert ctx.trace[-1].changed is False
    assert ctx.trace[-1].stage == 'on_begin'


def test_refine_messages_same_list_is_also_unchanged():
    """Returning the live messages list counts as in place: an unchanged step."""
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    result = ctx.refine_messages(object(), ctx.messages, stage='on_prompt')
    assert result is ctx.messages
    assert ctx.trace[-1].changed is False


def test_refine_messages_with_a_new_list_replaces_the_whole_conversation():
    """A new list with a system turn replaces the whole live conversation."""
    # the step is marked changed; plain dicts are wrapped as TokeoAiChatMessage so
    # the cache stays typed
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    live = ctx.messages
    new_convo = [{'role': 'system', 'content': 'be brief'}, {'role': 'user', 'content': 'hi'}]
    result = ctx.refine_messages(object(), new_convo, stage='on_prompt')
    # same live list object, new content, every entry a TokeoAiChatMessage
    assert result is live
    assert [m['role'] for m in ctx.messages] == ['system', 'user']
    assert all(isinstance(m, TokeoAiChatMessage) for m in ctx.messages)
    assert ctx.trace[-1].changed is True
    assert ctx.trace[-1].stage == 'on_prompt'


def test_refine_messages_with_an_invalid_list_raises():
    """A returned ['not a message'] makes refine_messages raise TokeoAiError."""
    # a conversation must be a list of mappings; anything else is a guard bug,
    # so refine_messages fails loud rather than corrupting the cache
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    try:
        ctx.refine_messages(object(), ['not a message'], stage='on_prompt')
        assert False, 'expected TokeoAiError'
    except TokeoAiError:
        pass


def test_refine_messages_step_is_a_snapshot_not_the_live_list():
    """An on_begin step keeps its one message while the live list grows to two."""
    # the step holds a shallow copy, so it shows the conversation as it stood
    # at that stage
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}])
    ctx.refine_messages(object(), None, stage='on_begin')
    begin_step = ctx.trace[-1]
    # the conversation grows after the begin step
    ctx.track(object(), TokeoAiChatMessage(role='assistant', content='later'))
    # the begin step still shows only the one message it saw, not the new one
    assert len(begin_step.object) == 1
    assert begin_step.object is not ctx.messages
    assert len(ctx.messages) == 2


def test_trace_false_skips_the_trace_but_fills_the_caches():
    """With trace=False no steps are recorded, but the typed caches still fill."""
    # so guards keep working off messages/invocations/results
    ctx = TokeoAiContext(messages=[{'role': 'user', 'content': 'hi'}], trace=False)
    ctx.track(object(), Invocation(id='t1', name='calc'))
    ctx.supersede(object(), None, ctx.current_invocation, stage='on_call')
    ctx.refine_messages(object(), None, stage='on_prompt')
    # the history is empty, the state by kind is intact
    assert ctx.trace == []
    assert len(ctx.messages) == 1
    assert ctx.current_invocation.name == 'calc'


# ---- free notes on the trace (TokeoAiTraceNote) -----------------------------


def test_track_wraps_plain_values_in_a_note():
    """A str, a dict and an int are each traced as a TokeoAiTraceNote."""
    ctx = TokeoAiContext(messages=[])
    origin = object()
    ctx.track(origin, 'policy checked')
    ctx.track(origin, {'checked': 3})
    ctx.track(origin, 42)
    trace_notes = [step.object for step in ctx.trace]
    assert all(isinstance(obj, TokeoAiTraceNote) for obj in trace_notes)
    assert [obj.content for obj in trace_notes] == ['policy checked', {'checked': 3}, 42]


class _TrackMyKind:
    # a class of its own: it names itself on the trace, so it passes through
    def __init__(self, label):
        self.label = label


def test_track_passes_mykind_classes_through():
    """An object of an own class is tracked as is, not wrapped in a note."""
    ctx = TokeoAiContext(messages=[])
    mykind = _TrackMyKind('mine')
    assert ctx.track(object(), mykind) is mykind
    assert ctx.trace[0].object is mykind


def test_track_skips_none():
    """track(None) returns None and records nothing on the trace."""
    ctx = TokeoAiContext(messages=[])
    assert ctx.track(object(), None) is None
    assert ctx.trace == []


def test_note_keeps_origin_and_stage():
    """A note's step keeps its origin and the stage 'on_call'."""
    ctx = TokeoAiContext(messages=[])
    origin = object()
    ctx.track(origin, 'checked', stage='on_call')
    step = ctx.trace[0]
    assert step.origin is origin
    assert step.stage == 'on_call'


def test_notes_do_not_reach_the_caches():
    """A note is history, not state: messages and invocations stay empty."""
    ctx = TokeoAiContext(messages=[])
    ctx.track(object(), 'just a note')
    assert ctx.messages == []
    assert ctx.invocations == []
