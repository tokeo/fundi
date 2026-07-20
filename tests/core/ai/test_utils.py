"""
Tests for the shared --model_param parse+coerce helper.

The helper turns repeatable ```key=value``` strings into a model_params dict,
used the same way by ```ai ask```, the ```ai chat``` start flags and the
interactive chat switches. It coerces the value like a yaml scalar (so numbers,
booleans and null get their types) and treats a null/empty value as "remove the
key". These tests pin that behaviour down in one place.
"""

import pytest

from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.utils import parse_model_params, coerce_model_param_value
from tokeo.core.ai.utils import add_tool_call, drop_tool_calls
from tokeo.core.ai.data import ChatResult


def test_coerce_types_like_a_yaml_scalar():
    # numbers, booleans and strings get their proper types
    assert coerce_model_param_value('0.2') == 0.2
    assert coerce_model_param_value('42') == 42
    assert coerce_model_param_value('true') is True
    assert coerce_model_param_value('false') is False
    assert coerce_model_param_value('qwen2.5') == 'qwen2.5'
    assert coerce_model_param_value('null') is None


def test_parse_sets_typed_values():
    params = parse_model_params(['temperature=0.2', 'max_tokens=1024', 'model=qwen'])
    assert params == {'temperature': 0.2, 'max_tokens': 1024, 'model': 'qwen'}


def test_parse_null_removes_a_key():
    # null and empty both drop the key, so a later pair can undo an earlier one
    params = parse_model_params(['temperature=0.2', 'temperature=null'])
    assert 'temperature' not in params


def test_parse_empty_value_removes_a_key():
    params = parse_model_params(['top_p=0.9', 'top_p='])
    assert 'top_p' not in params


def test_parse_later_value_overrides_earlier():
    params = parse_model_params(['temperature=0.2', 'temperature=0.9'])
    assert params['temperature'] == 0.9


def test_parse_value_with_equals_sign_is_kept_whole():
    # partition keeps everything after the first '=' as the value
    params = parse_model_params(['stop=a=b'])
    assert params['stop'] == 'a=b'


def test_parse_rejects_token_without_equals():
    with pytest.raises(TokeoAiError):
        parse_model_params(['temperature'])


def test_parse_rejects_empty_key():
    with pytest.raises(TokeoAiError):
        parse_model_params(['=0.2'])


def test_parse_none_input_is_empty():
    assert parse_model_params(None) == {}


# ---- add_tool_call / drop_tool_calls (the conductor originate helpers) ------


def _blank_result():
    return ChatResult(text='', reasoning='', refusal='', tool_calls=[], usage=None, finish_reason=None)


def test_add_tool_call_appends_with_a_counted_id():
    # a governor originates a call; the id is inj_<n> from loopdata, unique per turn
    result = add_tool_call(_blank_result(), 'load_report', 'inj_a', quarter='Q1')
    result = add_tool_call(result, 'load_report', 'inj_b', quarter='Q2')
    assert [c.id for c in result.tool_calls] == ['inj_a', 'inj_b']
    assert [c.arguments for c in result.tool_calls] == [{'quarter': 'Q1'}, {'quarter': 'Q2'}]


def test_add_tool_call_returns_a_new_object():
    # supersede only sees a change when the object differs
    before = _blank_result()
    after = add_tool_call(before, 'x', 'inj_1')
    assert after is not before


def test_drop_tool_calls_by_name_and_all():
    result = add_tool_call(_blank_result(), 'a', 'inj_1')
    result = add_tool_call(result, 'b', 'inj_2')
    # by name: only a goes
    only_b = drop_tool_calls(result, 'a')
    assert [c.name for c in only_b.tool_calls] == ['b']
    # all: nothing left
    assert drop_tool_calls(result).tool_calls == []
