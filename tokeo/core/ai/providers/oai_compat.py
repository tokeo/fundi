"""
OpenAI-compatible chat provider for Tokeo applications.

A thin synchronous client over the OpenAI-compatible chat-completions transport
-- the protocol that Ollama, llama.cpp, vLLM, MLX and the commercial APIs all
speak, so one provider serves them all. It is a dumb transport: it posts the
messages (and any tool specs) to ```{base_url}/chat/completions``` and maps the
reply into a ```ChatResult```. Tool-call normalization and the native-vs-prompt
decision live in the agent layer, so this provider stays small.

It is sync on purpose: the agent loop is synchronous and
needs the whole result before it can run the next tool round. Streaming, if it
ever lands, is an additive path for the final text turn, not a change here.

Connection settings come from the profile like any other component, so the
```key``` benefits from the normal config resolution (plain text, ```${ENV}```
expansion, or the encrypted vault) without anything special here.
"""

import json

import httpx

from tokeo.core.ai import TokeoAiError, TokeoAiProvider, ChatResult, ToolCall, Usage


class TokeoAiOaiCompatProvider(TokeoAiProvider):
    """
    Provider for any OpenAI-compatible chat-completions endpoint.

    Resolved from a profile whose ```type``` is ```oai_compat```. The profile
    options carry the connection settings; a fresh ```httpx.Client``` is built
    per call, so the provider keeps no per-call state and is safe to reuse.

    ### Notes

    - Options (top-level or under ```options```): ```base_url``` (required, e.g.
        ```http://localhost:11434/v1```), ```model``` (required), ```key```
        (optional bearer token; omit for an open local endpoint), ```timeout```
        (seconds, default 120 -- inference is slow, so the default is generous)
    - The unmodified server reply is kept on ```ChatResult.raw```, so a caller
        can always inspect exactly what came back
    - Errors become a clear ```TokeoAiError```: an unreachable endpoint, a
        timeout, or a non-2xx status (with a short body excerpt) -- never a
        raw transport traceback across the boundary

    """

    def _option(self, profile, key, default=None):
        # a profile field may sit at the top level or inside ```options```; read
        # either place, the same rule the handler uses to resolve model/base_url
        if key in profile:
            return profile[key]
        return (profile.get('options') or {}).get(key, default)

    def chat(self, profile, messages, tools=None, model_params=None):
        """
        Post the messages to the endpoint and return a normalized result.

        ### Args

        - **profile** (dict): The resolved profile; carries ```base_url```,
            ```model```, optional ```key``` and ```timeout```
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): OpenAI-style function specs to offer; sent
            only when non-empty
        - **model_params** (dict | None): Per-call model parameters; merged over
            the profile's ```model_params``` (the call's keys win), so a caller
            can adjust sampling for one request without touching the config

        ### Returns

        - **ChatResult**: text, reasoning, refusal, tool_calls, usage,
            finish_reason, system_fingerprint, and the raw reply

        ### Raises

        - **TokeoAiError**: On missing settings, an unreachable endpoint, a
            timeout, or a non-2xx response

        """
        base_url = self._option(profile, 'base_url')
        model = self._option(profile, 'model')
        if not base_url or not model:
            raise TokeoAiError('oai_compat profile needs both base_url and model')
        key = self._option(profile, 'key')
        timeout = self._option(profile, 'timeout', 120)

        # model parameters: the profile's model_params are the base, the call's
        # override them key by key, so a caller tweaks one request without editing
        # the config. passed straight through to the model -- no validation here,
        # the endpoint owns the valid ranges (they vary by model)
        params = dict(profile.get('model_params') or {})
        if model_params:
            params.update(model_params)

        # model is the one fixed key a param may override: a call can target a
        # different model on the same endpoint without a second profile. it is
        # pulled out of params so it does not appear twice in the body; messages
        # and tools stay fixed and can never be shadowed by a stray param
        use_model = params.pop('model', None) or model

        # the request body: the chat-completions shape. params are spread in
        # first, then the fixed keys, so messages/tools can never be shadowed.
        # tools travel only when offered
        body = {**params, 'model': use_model, 'messages': messages or []}
        if tools:
            body['tools'] = tools
        headers = {'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = f'Bearer {key}'

        url = f'{base_url.rstrip("/")}/chat/completions'
        try:
            # a per-call client: no shared state, closed when the call ends
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.ConnectError as err:
            raise TokeoAiError(f'oai_compat endpoint not reachable at {base_url!r}: {err}')
        except httpx.TimeoutException as err:
            raise TokeoAiError(f'oai_compat request to {base_url!r} timed out after {timeout}s: {err}')
        except httpx.HTTPError as err:
            raise TokeoAiError(f'oai_compat transport error against {base_url!r}: {err}')

        if response.status_code >= 400:
            # surface the status and a short body excerpt, not a traceback
            excerpt = response.text[:200]
            raise TokeoAiError(f'oai_compat endpoint returned HTTP {response.status_code}: {excerpt!r}')

        try:
            data = response.json()
        except ValueError as err:
            raise TokeoAiError(f'oai_compat endpoint returned non-JSON body: {err}')

        return self._to_result(data)

    def _to_result(self, data):
        # map a chat-completions reply into a ChatResult; missing fields degrade
        # to sensible empties rather than raising, so an odd-but-valid reply is
        # still usable and fully visible on ```raw```
        choices = data.get('choices') or [{}]
        first = choices[0] if choices else {}
        message = first.get('message') or {}
        text = message.get('content') or ''
        # some endpoints split the model's thinking into its own field; keep it
        # apart from the answer text when present (non-standard: only reasoning
        # models report it, so it is harvested when there, never required)
        reasoning = message.get('reasoning') or message.get('reasoning_content') or ''
        # a structured-outputs refusal is a real reply, not empty content; keep
        # it apart so a caller can tell "declined" from "no content"
        refusal = message.get('refusal') or ''
        tool_calls = self._tool_calls(message.get('tool_calls') or [])
        usage = self._usage(data.get('usage'))
        finish_reason = first.get('finish_reason')
        return ChatResult(
            text=text,
            reasoning=reasoning,
            refusal=refusal,
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            system_fingerprint=data.get('system_fingerprint'),
            raw=data,
        )

    def _tool_calls(self, raw_calls):
        # turn native function tool_calls into our ToolCall objects. arguments
        # arrive as a JSON STRING in the openai shape, so parse it; a malformed
        # or empty string degrades to an empty dict rather than crashing the run
        calls = []
        for index, call in enumerate(raw_calls):
            function = call.get('function') or {}
            name = function.get('name')
            if not name:
                continue
            raw_args = function.get('arguments')
            if isinstance(raw_args, dict):
                arguments = raw_args
            elif isinstance(raw_args, str) and raw_args.strip():
                try:
                    arguments = json.loads(raw_args)
                except ValueError:
                    arguments = {}
            else:
                arguments = {}
            calls.append(ToolCall(id=call.get('id') or f'call_{index + 1}', name=name, arguments=arguments))
        return calls

    def _usage(self, raw_usage):
        # map the usage block when the endpoint reports one; absent on many
        # local servers, so None is a normal outcome
        if not isinstance(raw_usage, dict):
            return None
        return Usage(
            prompt_tokens=raw_usage.get('prompt_tokens') or 0,
            completion_tokens=raw_usage.get('completion_tokens') or 0,
            total_tokens=raw_usage.get('total_tokens') or 0,
        )
