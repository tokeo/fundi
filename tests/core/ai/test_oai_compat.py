"""
Tests for the OpenAI-compatible provider.

The provider is a thin transport, so the tests pin down the two things that can
break: the mapping from a chat-completions reply into a ```TokeoAiChatResult```
(text, tool calls with JSON-string arguments, usage, finish reason, reasoning), and
the error behaviour (unreachable endpoint and non-2xx status become a clear
```TokeoAiError```, not a transport traceback). No network and no server: an
```httpx.MockTransport``` answers every request, injected by patching the client the
provider builds per call.
"""

import json

import httpx
import pytest

from tokeo.core.ai import TokeoAiError
from tokeo.core.ai.providers.oai_compat import TokeoAiOaiCompatProvider

# a profile in the uniform form: connection options under ```options```
_PROFILE = {'type': 'oai_compat', 'model': 'qwen2.5', 'options': {'base_url': 'http://localhost:11434/v1'}}


def _make_provider(handler, monkeypatch):
    # patch httpx.Client so the provider's per-call client routes through a
    # MockTransport running ```handler```; the real .post path is exercised,
    # only the socket is replaced
    original = httpx.Client

    def make_client(*args, **kw):
        kw['transport'] = httpx.MockTransport(handler)
        return original(*args, **kw)

    monkeypatch.setattr(httpx, 'Client', make_client)
    return TokeoAiOaiCompatProvider(None)


def test_oai_compat_maps_text_and_usage(monkeypatch):
    """Text 'hello there', finish 'stop', 7 total tokens fill TokeoAiChatResult."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                'choices': [{'message': {'content': 'hello there'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7},
            },
        )

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert result.text == 'hello there'
    assert result.finish_reason == 'stop'
    assert result.usage.total_tokens == 7
    assert result.tool_calls == []
    assert result.raw['usage']['prompt_tokens'] == 5


def test_oai_compat_maps_tool_calls_with_json_string_args(monkeypatch):
    """Tool call call_abc to calc maps with its JSON-string arguments as dict."""

    def handler(request):
        return httpx.Response(
            200,
            json={
                'choices': [
                    {
                        'message': {
                            'content': '',
                            'tool_calls': [
                                {
                                    'id': 'call_abc',
                                    'function': {'name': 'calc', 'arguments': '{"expr": "2 + 3"}'},
                                }
                            ],
                        },
                        'finish_reason': 'tool_calls',
                    }
                ]
            },
        )

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'calc'}], _PROFILE, tools=[{'name': 'calc'}])
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == 'call_abc' and call.name == 'calc'
    # the json-string arguments are parsed back into a dict
    assert call.arguments == {'expr': '2 + 3'}


def test_oai_compat_tolerates_malformed_tool_arguments(monkeypatch):
    """Tool arguments 'not json' are kept as they came, for the loop to refuse."""

    def handler(request):
        return httpx.Response(
            200,
            json={'choices': [{'message': {'tool_calls': [{'id': 'x', 'function': {'name': 't', 'arguments': 'not json'}}]}}]},
        )

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'x'}], _PROFILE)
    # a malformed argument string degrades to an empty dict, not a crash
    assert result.tool_calls[0].arguments == 'not json'


def test_oai_compat_keeps_reasoning_apart(monkeypatch):
    """Reasoning 'because' lands in reasoning, apart from the text 'answer'."""

    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'content': 'answer', 'reasoning': 'because'}}]})

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert result.text == 'answer' and result.reasoning == 'because'


def test_oai_compat_sends_tools_and_auth_only_when_present(monkeypatch):
    """A key goes out as 'Bearer secret-token' and the tools in the body."""
    seen = {}

    def handler(request):
        seen['auth'] = request.headers.get('Authorization')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    # with a key and tools, both must travel
    profile = {'model': 'm', 'options': {'base_url': 'http://x/v1', 'key': 'secret-token'}}
    provider = _make_provider(handler, monkeypatch)
    provider.chat([{'role': 'user', 'content': 'hi'}], profile, tools=[{'name': 'calc'}])
    assert seen['auth'] == 'Bearer secret-token'
    assert seen['body']['tools'] == [{'name': 'calc'}]
    assert seen['body']['model'] == 'm'


def test_oai_compat_omits_auth_and_tools_when_absent(monkeypatch):
    """Without key and tools no Authorization header and no tools are sent."""
    seen = {}

    def handler(request):
        seen['auth'] = request.headers.get('Authorization')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    provider = _make_provider(handler, monkeypatch)
    provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert seen['auth'] is None
    assert 'tools' not in seen['body']


def test_oai_compat_unreachable_endpoint_raises_clear_error(monkeypatch):
    """A refused connection raises TokeoAiError 'not reachable'."""

    def handler(request):
        raise httpx.ConnectError('connection refused')

    provider = _make_provider(handler, monkeypatch)
    with pytest.raises(TokeoAiError) as err_info:
        provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert 'not reachable' in str(err_info.value)


def test_oai_compat_http_error_status_raises_with_excerpt(monkeypatch):
    """A 401 reply raises TokeoAiError naming 'HTTP 401'."""

    def handler(request):
        return httpx.Response(401, text='unauthorized: bad key')

    provider = _make_provider(handler, monkeypatch)
    with pytest.raises(TokeoAiError) as err_info:
        provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert 'HTTP 401' in str(err_info.value)


def test_oai_compat_requires_base_url_and_model():
    """Without options.base_url, or with model only under options, it raises."""
    provider = TokeoAiOaiCompatProvider(None)
    with pytest.raises(TokeoAiError, match='needs model and options.base_url'):
        provider.chat([{'role': 'user', 'content': 'hi'}], {'model': 'm'})
    with pytest.raises(TokeoAiError, match='needs model and options.base_url'):
        provider.chat([{'role': 'user', 'content': 'hi'}], {'options': {'base_url': 'http://x/v1', 'model': 'm'}})


def test_oai_compat_sends_profile_model_params(monkeypatch):
    """Profile model_params temperature 0.2 and top_p 0.9 go into the body."""
    seen = {}

    def handler(request):
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    profile = {'model': 'm', 'options': {'base_url': 'http://x/v1'}, 'model_params': {'temperature': 0.2, 'top_p': 0.9}}
    provider = _make_provider(handler, monkeypatch)
    provider.chat([{'role': 'user', 'content': 'hi'}], profile)
    assert seen['body']['temperature'] == 0.2
    assert seen['body']['top_p'] == 0.9
    # the fixed keys are never shadowed by a param
    assert seen['body']['model'] == 'm'


def test_oai_compat_call_params_override_profile(monkeypatch):
    """A call temperature 0.9 overrides the profile 0.2; top_p 0.9 stays."""
    seen = {}

    def handler(request):
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    profile = {'model': 'm', 'options': {'base_url': 'http://x/v1'}, 'model_params': {'temperature': 0.2, 'top_p': 0.9}}
    provider = _make_provider(handler, monkeypatch)
    # the call override wins key by key; unset keys keep the profile value
    provider.chat([{'role': 'user', 'content': 'hi'}], profile, model_params={'temperature': 0.9})
    assert seen['body']['temperature'] == 0.9
    assert seen['body']['top_p'] == 0.9


def test_oai_compat_model_param_overrides_profile_model(monkeypatch):
    """model in the call model_params sends 'override-model' instead."""
    seen = {}

    def handler(request):
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})

    profile = {'model': 'from-profile', 'options': {'base_url': 'http://x/v1'}}
    provider = _make_provider(handler, monkeypatch)
    # a model key in model_params targets a different model on the same endpoint
    provider.chat([{'role': 'user', 'content': 'hi'}], profile, model_params={'model': 'override-model'})
    assert seen['body']['model'] == 'override-model'
    # model appears once, not duplicated, and messages stay intact
    assert seen['body']['messages'] == [{'role': 'user', 'content': 'hi'}]


def test_oai_compat_maps_refusal(monkeypatch):
    """Refusal 'I cannot help with that' lands in refusal; text stays ''."""

    def handler(request):
        return httpx.Response(200, json={'choices': [{'message': {'refusal': 'I cannot help with that'}, 'finish_reason': 'stop'}]})

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    # a refusal is kept apart from text, which stays empty
    assert result.refusal == 'I cannot help with that'
    assert result.text == ''


def test_oai_compat_maps_system_fingerprint(monkeypatch):
    """system_fingerprint 'fp_abc123' is mapped onto the TokeoAiChatResult."""

    def handler(request):
        return httpx.Response(200, json={'system_fingerprint': 'fp_abc123', 'choices': [{'message': {'content': 'ok'}}]})

    provider = _make_provider(handler, monkeypatch)
    result = provider.chat([{'role': 'user', 'content': 'hi'}], _PROFILE)
    assert result.system_fingerprint == 'fp_abc123'
