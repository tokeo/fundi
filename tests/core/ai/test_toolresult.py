"""
Tests for the ToolResult model itself.

```create_tool_result``` and the three coherent views (```as_str```,
```as_json```, ```as_data```) in isolation -- no app, no sandbox. What a
finished tool call carries once the extension handed it over lives in
```tests/ext/ai/test_ai_toolresult.py```.
"""

from tokeo.core.ai import ToolResult
from tokeo.core.ai.tool import create_tool_result


def test_create_tool_result_wraps_a_plain_string():
    # the trivial path: a string becomes the model-facing as_str, and the other
    # views follow (as_json a JSON string, as_data the raw value)
    result = create_tool_result('hello')
    assert isinstance(result, ToolResult)
    assert result.value.as_str == 'hello'
    assert result.value.as_data == 'hello'
    assert result.state.exception is None


def test_create_tool_result_fills_three_coherent_views_for_a_dict():
    # a structured value fills all three views from one input: as_data keeps the
    # object, as_json is its JSON encoding, as_str the model-facing rendering
    result = create_tool_result(dict(answer=42, label='ok'))
    assert result.value.as_data == dict(answer=42, label='ok')
    assert '42' in result.value.as_json and 'answer' in result.value.as_json
    assert result.value.as_str  # a non-empty rendering


def test_create_tool_result_none_yields_an_empty_string_view():
    # create_tool_result(None) always builds a value: a None value has no text,
    # so as_str is the empty string (not the literal 'None'). the "no result"
    # state (value is None) is produced by the sandbox/loop, not by this helper
    result = create_tool_result(None)
    assert result.value is not None
    assert result.value.as_str == ''
    assert result.value.as_data is None


def test_create_tool_result_explicit_as_str_wins_over_the_value():
    # an explicit as_str is the model-facing string even when a value is given,
    # so a tool can render its own wording while keeping the structured value
    result = create_tool_result(dict(answer=42), as_str='the answer is 42')
    assert result.value.as_str == 'the answer is 42'
    assert result.value.as_data == dict(answer=42)


def test_create_tool_result_carries_state_fields():
    # a state dict sets only its named fields onto the derived states, so a tool
    # can record stdout/incomplete alongside its value in one call
    result = create_tool_result('out', state=dict(stdout='logged', incomplete=True))
    assert result.state.stdout == 'logged'
    assert result.state.incomplete is True
    assert result.state.exception is None


# --------------------------------------------------------------------------------------
# the sandbox catches a tool that raises: tool-error A in state.exception
# --------------------------------------------------------------------------------------
