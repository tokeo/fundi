"""
Tests for the mock provider's codeact synthesis.

The mock turns a ```text <op> <sentence>``` prompt into model-generated python
and calls a code-running tool with it -- but only when such a tool (one taking a
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


def _synth(prompt, tools=(_CODE_TOOL,)):
    provider = TokeoAiMockProvider(None)
    call = provider._codeact_synthesis(prompt, list(tools))
    return call.arguments['code'] if call is not None else None


def test_mock_codeact_upper_and_synonyms():
    assert _synth('text upper der wochentag') == "result = 'der wochentag'.upper()"
    assert _synth('text gross hallo') == "result = 'hallo'.upper()"
    assert _synth('text groß welt') == "result = 'welt'.upper()"


def test_mock_codeact_reverse_and_synonyms():
    assert _synth('text reverse otto') == "result = 'otto'[::-1]"
    assert _synth('text umkehr abc') == "result = 'abc'[::-1]"
    assert _synth('text gedreht xyz') == "result = 'xyz'[::-1]"


def test_mock_codeact_len_and_synonyms():
    assert _synth('text len wir sehen uns in 7 tagen') == "result = len('wir sehen uns in 7 tagen')"
    assert _synth('text length abcd') == "result = len('abcd')"
    assert _synth('text laenge hello') == "result = len('hello')"
    assert _synth('text länge öäü') == "result = len('öäü')"


def test_mock_codeact_targets_the_code_tool_by_shape():
    # the synthesized call names the offered tool whatever its config name is
    provider = TokeoAiMockProvider(None)
    call = provider._codeact_synthesis('text upper hi', [_CODE_TOOL])
    assert call is not None and call.name == 'coding'


def test_mock_codeact_inactive_without_a_code_tool():
    # no tool takes a code parameter -> no synthesis, the normal paths apply
    assert _synth('text upper hi', tools=(_OTHER_TOOL,)) is None


def test_mock_codeact_ignores_unknown_op_and_empty_sentence():
    assert _synth('text frobnicate hi') is None
    assert _synth('text upper') is None
    assert _synth('calc 2 + 3') is None
