"""
Tests for the ToolResult model itself.

```create_tool_result``` and the three coherent views (```as_str```,
```as_json```, ```as_data```) in isolation: no app, no sandbox. What a
finished tool call carries once the extension handed it over lives in
```tests/ext/ai/test_toolresult.py```.
"""

from tokeo.core.ai import ToolResult
from tokeo.core.ai.tool import create_tool_result


def test_create_tool_result_wraps_a_plain_string():
    """'hello' is as_str and raw as_data (as_json a JSON string), no exception."""
    result = create_tool_result('hello')
    assert isinstance(result, ToolResult)
    assert result.value.as_str == 'hello'
    assert result.value.as_data == 'hello'
    assert result.state.exception is None


def test_create_tool_result_fills_three_coherent_views_for_a_dict():
    """A dict fills all views: as_data the dict, as_json its JSON, as_str text."""
    result = create_tool_result(dict(answer=42, label='ok'))
    assert result.value.as_data == dict(answer=42, label='ok')
    assert '42' in result.value.as_json and 'answer' in result.value.as_json
    assert result.value.as_str  # a non-empty rendering


def test_create_tool_result_none_yields_an_empty_string_view():
    """
    None still builds a value: as_str is '' (not 'None'), as_data None.

    The "no result" state (value is None) comes from the sandbox or the
    loop, not from this helper.
    """
    result = create_tool_result(None)
    assert result.value is not None
    assert result.value.as_str == ''
    assert result.value.as_data is None


def test_create_tool_result_explicit_as_str_wins_over_the_value():
    """as_str 'the answer is 42' wins; as_data keeps the dict(answer=42)."""
    result = create_tool_result(dict(answer=42), as_str='the answer is 42')
    assert result.value.as_str == 'the answer is 42'
    assert result.value.as_data == dict(answer=42)


def test_create_tool_result_carries_state_fields():
    """state sets only its fields stdout 'logged' and incomplete True."""
    result = create_tool_result('out', state=dict(stdout='logged', incomplete=True))
    assert result.state.stdout == 'logged'
    assert result.state.incomplete is True
    assert result.state.exception is None


# ---- the sandbox catches a tool that raises: tool-error A in state.exception ----
