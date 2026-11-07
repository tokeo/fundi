r"""
A ready-to-use redact guard that masks secret-looking spans by regex.

```TokeoAiRegexRedactGuard``` masks at the tool boundary, at both tool stages:
at ```on_call``` the call arguments, nested ones too, and the call as the model
sent it in the history and on the trace; at ```on_return``` the returned value
(the model-facing ```as_str``` always, the structured ```as_data``` too unless
```redact_data``` is off), the run states ```stdout```, ```stderr``` and
```error``` and the error of the call. So a secret does not flow into a tool
or from a tool back to the model, the history, the trace or a log line. The
user prompt (```on_prompt```) and the model answer (```on_answer```) are not
masked by this guard.

Redaction is best-effort masking by pattern, not a guarantee. There is no
built-in pattern list: the project supplies the full list through the
```patterns``` option, and it is required wherever the guard runs. A stage with
no patterns, or a pattern that does not compile, raises
```TokeoAiRedactGuardError``` ("missing or invalid pattern") on the first call
that reaches that stage, aborting the run, rather than silently masking nothing
and letting a secret through.

```yaml
ai:
  guards:
    regex_redact:
      type: regex_redact
      options:
        # required: the full list of patterns to mask (no built-in default).
        # each pattern matches the secret span itself, so the whole match is
        # replaced. a missing, empty, or non-compiling list aborts the run
        patterns:
          - '(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}'
          - '\bsk-[A-Za-z0-9]{16,}\b'
          - '\bAKIA[0-9A-Z]{16}\b'
          - '(?i)\b(?:api[_-]?key|secret|password|token)\b\s*[:=]\s*\S+'
        # what each matched span is replaced with
        replacement: '[redacted]'
        # also mask the structured result view (value.as_data), recursively,
        # the string values only; true by default, set false to mask as_str only
        redact_data: true
```
"""

import re

from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL, GOVERNOR_STAGE_ON_RETURN
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.ai.data import ToolResult, ToolStates
from tokeo.core.ai.guards.redact.base import TokeoAiRedactGuard
from tokeo.core.ai.guards.redact.exc import TokeoAiRedactGuardError


class TokeoAiRegexRedactGuard(TokeoAiRedactGuard):
    """
    A redact guard that masks secret-looking spans by regex, at the tool stages.

    Applies each configured pattern to the text it handles and replaces every
    match with the ```replacement``` marker: at ```on_call``` to each string
    value in ```invocation.arguments```, at ```on_return``` to
    ```invocation.result.value.as_str``` (and, when ```redact_data``` is on,
    the string values in ```value.as_data``` too). It never changes the
    ```decision```, so it is
    safe on every agent; when it masks anything it notes the count on
    ```reason``` so the trace shows the value was shaped.

    ### Args

    - **app**: The Tokeo application instance
    - **args**: Positional arguments for the parent initializer

    ### Keyword Args

    - **kw**: Keyword arguments for the parent initializer

    """

    class Meta:
        """Regex redact guard meta-data."""

        # the default options, as one dict (the '_any' base that per-stage
        # options overlay)
        config_defaults = dict(
            # None by default; there is no built-in list. the project supplies
            # the full list of regex patterns through the patterns option (each
            # pattern matches the secret span itself, so the whole match is
            # replaced). it is required wherever the guard runs: a stage with
            # no patterns, or a pattern that does not compile, raises rather
            # than silently masking nothing (deep merge appends lists, so a
            # built-in default could not be replaced, only extended, hence
            # none)
            patterns=None,
            # what each matched span is replaced with
            replacement='[redacted]',
            # when true (the default), the structured result view
            # (value.as_data) is masked too (recursively, the string values
            # only, keys untouched), so a secret cannot survive in
            # as_data/as_json and leak to the trace; set false to mask only the
            # model-facing as_str and leave the structure as the tool returned
            # it
            redact_data=True,
        )

    def __init__(self, app, *args, **kw):
        super(TokeoAiRegexRedactGuard, self).__init__(app, *args, **kw)
        # per-stage compiled-pattern cache, filled on first use in
        # _compile_patterns; pre-declared here so masking never depends on
        # _setup having run
        self._compiled_by_stage = {}

    def _setup(self, app, config_name=None, item=None):
        """
        Reset the per-stage compiled-pattern cache.

        Patterns can differ per stage (a stage may override the list), so they
        are compiled per stage on first use and cached; this clears the cache so
        a re-setup recompiles against the current config.

        ### Args

        - **app**: The Tokeo application instance
        - **config_name** (str, optional): The key the guard is configured under
        - **item** (dict, optional): The raw item

        """
        # super first: builds the stage views the patterns read
        super(TokeoAiRegexRedactGuard, self)._setup(app, config_name, item)
        self._compiled_by_stage = {}

    def _compile_patterns(self, stage):
        """
        Return the compiled patterns for a stage; raise if missing or invalid.

        Compiled once per stage on first use and cached. A stage with no
        patterns, or a pattern that does not compile, raises
        ```TokeoAiRedactGuardError``` ("missing or invalid pattern") on that
        first use, aborting the run.

        ### Args

        - **stage** (str): The stage name (e.g. ```on_call```)

        ### Returns

        - **list**: The compiled regex patterns effective at that stage

        """
        if stage not in self._compiled_by_stage:
            patterns = self._config('patterns', stage=stage)
            # the patterns are required wherever the guard runs: no built-in
            # list exists, and deep merge would only append to one, so a missing
            # or empty list is a misconfiguration, not "mask nothing". raise so
            # the run aborts loudly instead of passing secrets through unmasked
            if not patterns:
                raise TokeoAiRedactGuardError(f'missing or invalid pattern at {stage!r}: no patterns configured')
            compiled = []
            for pattern in patterns:
                try:
                    compiled.append(re.compile(pattern))
                except re.error as err:
                    raise TokeoAiRedactGuardError(f'missing or invalid pattern at {stage!r}: {pattern!r} ({err})')
            self._compiled_by_stage[stage] = compiled
        return self._compiled_by_stage[stage]

    def _mask_text(self, text, stage):
        """
        Mask every pattern match in a single text; return the text and hit count.

        Uses the patterns and replacement effective at ```stage```.

        ### Args

        - **text** (str): The text to mask
        - **stage** (str): The stage whose options apply

        ### Returns

        - **(str, int)**: The masked text and how many spans were replaced

        """
        replacement = self._config('replacement', stage=stage)
        hits = 0
        for pattern in self._compile_patterns(stage):
            text, count = pattern.subn(replacement, text)
            hits += count
        return text, hits

    def _mask_data(self, obj, stage):
        # mask string values recursively through the structure: a dict's values
        # (never its keys), a list's items, a bare string. non-strings pass
        # through. returns the masked copy and the hit count, so as_data is
        # cleaned the same way as_str is; a secret that the text view hid must
        # not survive in the structured view
        if isinstance(obj, str):
            return self._mask_text(obj, stage)
        if isinstance(obj, dict):
            out, hits = {}, 0
            for key, value in obj.items():
                masked, count = self._mask_data(value, stage)
                out[key] = masked
                hits += count
            return out, hits
        if isinstance(obj, (list, tuple)):
            out, hits = [], 0
            for item in obj:
                masked, count = self._mask_data(item, stage)
                out.append(masked)
                hits += count
            return out, hits
        return obj, 0

    def _add_note(self, invocation, hits):
        """Record on the invocation's reason how many spans were masked."""
        note = f'redacted {hits} secret(s)'
        invocation.reason = f'{invocation.reason}; {note}' if invocation.reason else note

    def on_call(self, ctx, invocation):
        """
        Mask secret-looking spans in the call's arguments; never denies.

        Runs at the tool-call stage, before exec. Every string in
        ```invocation.arguments``` is masked, also inside nested dicts and
        lists, so the tool gets no secret. The call as the model sent it is
        masked too: the assistant turn in ```ctx.messages``` and the answer in
        ```ctx.results``` that carry it, so no secret stays in the history or
        the trace.

        ### Args

        - **ctx** (TokeoAiContext): The running state with the model's turn
        - **invocation** (Invocation): The tool call whose arguments are masked
            when a pattern matches

        """
        hits = 0
        if invocation.arguments:
            masked, hits = self._mask_data(invocation.arguments, GOVERNOR_STAGE_ON_CALL)
            if hits:
                invocation.arguments = masked
        if ctx is not None:
            self._mask_request(ctx, invocation.id)
        if hits:
            self._add_note(invocation, hits)

    def _mask_request(self, ctx, call_id):
        # the model's own request of the call: the tool call in the assistant
        # turn of the history, and the tool call and the raw response of the
        # answer it came with. masked in place, so every view of the trace that
        # holds these objects shows the masked form
        for message in ctx.messages:
            for call in message.get('tool_calls') or []:
                if call.get('id') == call_id:
                    masked, count = self._mask_data(call, GOVERNOR_STAGE_ON_CALL)
                    if count:
                        call.clear()
                        call.update(masked)
        for result in ctx.results:
            for call in result.tool_calls:
                if call.id == call_id:
                    masked, count = self._mask_data(call.arguments, GOVERNOR_STAGE_ON_CALL)
                    if count:
                        call.arguments = masked
                    if result.raw is not None:
                        result.raw, _ = self._mask_data(result.raw, GOVERNOR_STAGE_ON_CALL)

    def on_return(self, ctx, invocation):
        """
        Mask secret-looking spans in the result and its errors; never denies.

        Runs at the tool-return stage, after the tool ran. ```ctx``` is the
        running state (unused here). The model-facing ```value.as_str``` is
        always masked, as are the run states ```stdout```, ```stderr``` and
        ```error``` and the ```error``` of the invocation, which reach the
        model and the trace as error text. When ```redact_data``` is on (the
        default), the structured ```value.as_data``` is masked too
        (recursively, the string values only), and the value is rebuilt so
        ```as_json``` carries no secret either. With ```redact_data``` off,
        the structure is left as the tool returned it.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The completed tool call whose result and
            error are masked in place when a pattern matches

        """
        stage = GOVERNOR_STAGE_ON_RETURN
        hits = 0
        if invocation.error:
            invocation.error, hits = self._mask_text(invocation.error, stage)
        result = invocation.result
        if result is None:
            if hits:
                self._add_note(invocation, hits)
            return
        state = {}
        for name in ('stdout', 'stderr', 'error'):
            text = getattr(result.state, name)
            if isinstance(text, str):
                text, count = self._mask_text(text, stage)
                hits += count
            state[name] = text
        state['is_incomplete'] = result.state.is_incomplete
        value = result.value
        if value is None:
            if hits:
                invocation.result = ToolResult(value=None, state=ToolStates(**state))
                self._add_note(invocation, hits)
            return
        # the text view is always masked, even when it is the tool's own wording
        # rather than a copy of the data, so both are cleaned on their own terms
        masked_str, count = self._mask_text(value.as_str or '', stage)
        hits += count
        masked_data = value.as_data
        if self._config('redact_data', stage=stage) and value.as_data is not None:
            masked_data, count = self._mask_data(value.as_data, stage)
            hits += count
        if not hits:
            return
        # rebuild the value so all three views are coherent: as_data masked,
        # as_json re-encoded from it, as_str set explicitly to the masked text
        # (the explicit string wins, keeping a reshaped wording); the run state
        # carries the masked streams and error
        invocation.result = create_tool_result(masked_data, as_str=masked_str, state=state)
        self._add_note(invocation, hits)
