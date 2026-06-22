"""
Mock ai provider for Tokeo applications.

A deterministic, dependency-free provider for tests and demos. It needs no
model, server, or network, yet it covers the three behaviours an agent loop
relies on, so the loop can be shown and built without a real model:

- a plain reply (a canned answer for "ping", an echo otherwise),
- a tool call when the prompt names an available tool, and
- a final answer once a tool result comes back.

The reply is a pure function of the input, so the same messages always produce
the same result, and the ```[mock]``` marker keeps it obvious that no real model
was involved.
"""

from tokeo.core.ai import TokeoAiProvider, ChatResult, ToolCall, Usage


class TokeoAiMockProvider(TokeoAiProvider):
    """
    Deterministic provider that fakes a chat completion.

    ### Notes

    - A trailing ```tool``` message makes the mock answer with the result, which
        closes an agent loop
    - Otherwise, if the first word of the prompt names a provided tool, the
        mock requests that tool, filling its first declared parameter (a
        required one if any) with the rest of the prompt; if not, it replies
        in plain text
    - As a codeact demo, the keyword ```text``` plus an operation word
        (upper/reverse/len, with a few language synonyms) and a sentence makes
        the mock SYNTHESIZE python that computes the value and call the
        untrusted exec tool with it -- but only when that tool is on offer

    """

    def chat(self, profile, messages, tools=None, model_params=None):
        """
        Return a deterministic reply, tool call, or final answer.

        ### Args

        - **profile** (dict): The resolved profile (ignored beyond its type)
        - **messages** (list): Chat messages as plain OpenAI-style dicts
        - **tools** (list | None): Tool definitions the mock may call

        ### Returns

        - **ChatResult**: The faked, deterministic response

        """
        messages = messages or []
        # a tool result on top closes the loop: answer with that result
        last = messages[-1] if messages else {}
        if isinstance(last, dict) and last.get('role') == 'tool':
            return self._result(f'Done. The tool returned: {last.get("content") or ""}')
        # the most recent user turn drives the reply
        prompt = ''
        for message in messages:
            if isinstance(message, dict) and message.get('role') == 'user':
                prompt = message.get('content') or ''
        # codeact demo: a plain ```text <op> <sentence>``` keyword makes the mock
        # SYNTHESIZE python that computes the value and call the untrusted exec
        # tool with it -- the model writes code, the sandbox runs it. only
        # active when that tool is actually offered to this agent
        synth = self._codeact_synthesis(prompt, tools)
        if synth is not None:
            return self._result('', tool_calls=[synth], finish_reason='tool_calls')
        # if the prompt names an available tool, request that tool with its
        # first declared parameter filled from the rest of the prompt
        match = self._match_tool(prompt, tools)
        if match is not None:
            spec, argument = match
            call = ToolCall(
                id='call_1',
                name=self._tool_name(spec),
                arguments=self._tool_arguments(spec, argument),
            )
            return self._result('', tool_calls=[call], finish_reason='tool_calls')
        # plain reply: a canned answer for ping, an echo otherwise
        if prompt.strip().lower() == 'ping':
            return self._result('pong')
        return self._result(f'[mock] {prompt}' if prompt else '[mock] (no prompt)')

    # the codeact keyword: ```text``` plus an operation word, then the sentence.
    # each operation maps (with a few language synonyms) to a python expression
    # template, filled with the sentence as a literal. deterministic on purpose
    _CODEACT_OPS = {
        'upper': '{s!r}.upper()',
        'gross': '{s!r}.upper()',
        'groß': '{s!r}.upper()',
        'reverse': '{s!r}[::-1]',
        'umkehr': '{s!r}[::-1]',
        'gedreht': '{s!r}[::-1]',
        'len': 'len({s!r})',
        'length': 'len({s!r})',
        'laenge': 'len({s!r})',
        'länge': 'len({s!r})',
    }

    def _codeact_synthesis(self, prompt, tools):
        # recognise ```text <op> <sentence>``` and, when a code-running tool is
        # on offer, build a tool call whose code computes the value. returns a
        # ToolCall or None (so the normal tool/echo paths still apply). the tool
        # is matched by SHAPE -- it takes a single ```code``` parameter -- not by
        # a fixed name, since the config alias is the project's to choose
        if not prompt or not tools:
            return None
        coder = self._code_tool(tools)
        if coder is None:
            return None
        head, _, rest = prompt.strip().partition(' ')
        if head.lower() != 'text':
            return None
        op, _, sentence = rest.partition(' ')
        template = self._CODEACT_OPS.get(op.lower())
        if template is None or not sentence.strip():
            return None
        # the synthesized snippet: assign the computed value to ```result```,
        # which the exec tool returns. the sentence is embedded as a literal
        code = f'result = {template.format(s=sentence.strip())}'
        return ToolCall(id='call_1', name=self._tool_name(coder), arguments={'code': code})

    def _code_tool(self, tools):
        # the code-running tool is the one whose only/first parameter is
        # ```code``` -- a stable shape, independent of the config alias name
        for tool in tools:
            names, _ = self._tool_params(tool)
            if names and names[0] == 'code':
                return tool
        return None

    def _match_tool(self, prompt, tools):
        # the first word of the prompt selects a tool when it matches one of
        # the provided tool names; the rest of the prompt is its argument value
        if not prompt or not tools:
            return None
        head, _, rest = prompt.strip().partition(' ')
        for tool in tools:
            if self._tool_name(tool) == head:
                return tool, rest.strip()
        return None

    def _tool_name(self, tool):
        # accept a plain name or an openai-style function tool definition
        if isinstance(tool, str):
            return tool
        if isinstance(tool, dict):
            if tool.get('name'):
                return tool['name']
            function = tool.get('function')
            if isinstance(function, dict):
                return function.get('name')
        return None

    def _tool_params(self, tool):
        # read the declared parameter names and the required ones from an
        # openai-style function spec; dicts preserve declaration order
        schema = {}
        if isinstance(tool, dict):
            function = tool.get('function')
            container = function if isinstance(function, dict) else tool
            schema = container.get('parameters') or {}
        properties = schema.get('properties') or {}
        names = list(properties)
        required = [name for name in (schema.get('required') or []) if name in properties]
        return names, required

    def _tool_arguments(self, tool, value):
        # fill the first declared parameter (a required one if any) with the
        # prompt remainder, so the mock drives any tool, not only one named
        # ```input```; a parameterless tool is called with no arguments
        names, required = self._tool_params(tool)
        key = required[0] if required else (names[0] if names else None)
        return {key: value} if key else {}

    def _result(self, text, tool_calls=None, finish_reason='stop'):
        # build a ChatResult with deterministic, faked token usage
        return ChatResult(
            text=text,
            tool_calls=tool_calls or [],
            usage=Usage(completion_tokens=len(text.split()), total_tokens=len(text.split())),
            finish_reason=finish_reason,
            raw={'provider': 'mock', 'text': text},
        )
