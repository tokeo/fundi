"""
Tests for the mock provider's codeact synthesis.

The mock turns a ```text <op> <sentence>``` prompt into model-generated python
and calls a code-running tool with it, but only when such a tool (one taking a
```code``` parameter) is on offer. These tests cover the synthesis in isolation:
they check the produced code and the activation rules, with no wasm guest, since
the synthesis only builds the tool call (the sandbox would run it).
"""

from tokeo.core.ai.providers.mock import TokeoAiMockProvider

# a tool spec shaped like the code-running tool: a single ```code``` parameter
_CODE_TOOL = {
    'type': 'function',
    'function': {
        'name': 'coding',
        'parameters': {'properties': {'code': {'type': 'string'}}, 'required': ['code']},
    },
}
# an unrelated tool, to prove synthesis does not fire without a code tool
_OTHER_TOOL = {'type': 'function', 'function': {'name': 'calc', 'parameters': {'properties': {'expr': {}}}}}


def _find_codeact_code(prompt, tools=(_CODE_TOOL,)):
    provider = TokeoAiMockProvider(None)
    call = provider._create_codeact_call(prompt, list(tools))
    return call.arguments['code'] if call is not None else None


def test_mock_codeact_upper_and_synonyms():
    """upper, gross and groß synthesize result = '<sentence>'.upper()."""
    assert _find_codeact_code('text upper der wochentag') == "result = 'der wochentag'.upper()"
    assert _find_codeact_code('text gross hallo') == "result = 'hallo'.upper()"
    assert _find_codeact_code('text groß welt') == "result = 'welt'.upper()"


def test_mock_codeact_reverse_and_synonyms():
    """reverse, umkehr and gedreht synthesize result = '<sentence>'[::-1]."""
    assert _find_codeact_code('text reverse otto') == "result = 'otto'[::-1]"
    assert _find_codeact_code('text umkehr abc') == "result = 'abc'[::-1]"
    assert _find_codeact_code('text gedreht xyz') == "result = 'xyz'[::-1]"


def test_mock_codeact_len_and_synonyms():
    """len, length, laenge and länge synthesize result = len('<sentence>')."""
    assert _find_codeact_code('text len wir sehen uns in 7 tagen') == "result = len('wir sehen uns in 7 tagen')"
    assert _find_codeact_code('text length abcd') == "result = len('abcd')"
    assert _find_codeact_code('text laenge hello') == "result = len('hello')"
    assert _find_codeact_code('text länge öäü') == "result = len('öäü')"


def test_mock_codeact_targets_the_code_tool_by_shape():
    """The call names the offered code tool, here 'coding', whatever its name."""
    provider = TokeoAiMockProvider(None)
    call = provider._create_codeact_call('text upper hi', [_CODE_TOOL])
    assert call is not None and call.name == 'coding'


def test_mock_codeact_inactive_without_a_code_tool():
    """No tool takes a code parameter: no synthesis, the normal paths apply."""
    assert _find_codeact_code('text upper hi', tools=(_OTHER_TOOL,)) is None


def test_mock_codeact_ignores_unknown_op_and_empty_sentence():
    """An unknown op, a missing sentence or a non-text prompt yield no call."""
    assert _find_codeact_code('text frobnicate hi') is None
    assert _find_codeact_code('text upper') is None
    assert _find_codeact_code('calc 2 + 3') is None
