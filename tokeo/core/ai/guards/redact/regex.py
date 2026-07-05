r"""
A ready-to-use redact guard that masks secret-looking spans by regex.

```TokeoAiRegexRedactGuard``` masks at both tool stages: the outgoing call
arguments (```on_call```) and the returned result value (```on_return``` -- the
model-facing ```as_str``` always, the structured ```as_data``` too unless
```sanitize_data``` is off), so a
secret does not flow on into the tool, the message history, the trace, or a log
line -- whether it sits in what is sent or in what comes back.

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
        sanitize_data: true
```
"""

import re

from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL, GOVERNOR_STAGE_ON_RETURN
from tokeo.core.ai.tool import create_tool_result
from tokeo.core.ai.guards.redact.base import TokeoAiRedactGuard
from tokeo.core.ai.guards.redact.exc import TokeoAiRedactGuardError


class TokeoAiRegexRedactGuard(TokeoAiRedactGuard):
    """
    A redact guard that masks secret-looking spans by regex, at the tool stages.

    Applies each configured pattern to the text it handles and replaces every
    match with the ```replacement``` marker: at ```on_call``` to each string
    value in ```invocation.arguments```, at ```on_return``` to
    ```invocation.result.value.as_str``` (and, when ```sanitize_data``` is on,
    the string values in ```value.as_data``` too). It never changes the
    ```decision```, so it is
    safe on every agent; when it masks anything it notes the count on
    ```reason``` so the trace shows the value was shaped.

    """

    class Meta:
        """Redact rules, overridden per guard by its entry's options."""

        # the default settings, as one dict (the '_any' base that per-stage
        # options overlay). patterns: None by default -- there is no built-in
        # list. the project supplies the full list of regex patterns through the
        # patterns option (each pattern matches the secret span itself, so the
        # whole match is replaced). it is required wherever the guard runs: a
        # stage with no patterns, or a pattern that does not compile, raises
        # rather than silently masking nothing (deep merge appends lists, so a
        # built-in default could not be replaced, only extended -- hence none).
        # replacement: what each matched span is replaced with. sanitize_data:
        # when true (the default), the structured result view (value.as_data) is
        # masked too -- recursively, the string values only, keys untouched -- so
        # a secret cannot survive in as_data/as_json and leak to the trace; set
        # false to mask only the model-facing as_str and leave the structure as
        # the tool returned it
        config_defaults = dict(
            patterns=None,
            replacement='[redacted]',
            sanitize_data=True,
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the regex redact guard.

        ### Args

        - **app**: The Tokeo application instance
        - **args**: Positional arguments for the parent initializer

        ### Keyword Args

        - **kw**: Keyword arguments for the parent initializer

        """
        super(TokeoAiRegexRedactGuard, self).__init__(app, *args, **kw)
        # per-stage compiled-pattern cache, filled on first use in _compiled;
        # pre-declared here so masking never depends on _setup having run
        self._compiled_by_stage = {}

    def _setup(self, app):
        """
        Reset the per-stage compiled-pattern cache.

        Patterns can differ per stage (a stage may override the list), so they
        are compiled per stage on first use and cached; this clears the cache so
        a re-setup recompiles against the current config.

        ### Args

        - **app**: The Tokeo application instance

        """
        self._compiled_by_stage = {}

    def _compiled(self, stage):
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
            patterns = self._config(stage).get('patterns')
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

    def _mask(self, text, stage):
        """
        Mask every pattern match in a single text; return the text and hit count.

        Uses the patterns and replacement effective at ```stage```.

        ### Args

        - **text** (str): The text to mask
        - **stage** (str): The stage whose settings apply

        ### Returns

        - **(str, int)**: The masked text and how many spans were replaced

        """
        replacement = self._config(stage).get('replacement')
        hits = 0
        for pattern in self._compiled(stage):
            text, count = pattern.subn(replacement, text)
            hits += count
        return text, hits

    def _mask_data(self, obj, stage):
        # mask string values recursively through the structure: a dict's values
        # (never its keys), a list's items, a bare string. non-strings pass
        # through. returns the masked copy and the hit count, so as_data is
        # cleaned the same way as_str is -- a secret that the text view hid must
        # not survive in the structured view
        if isinstance(obj, str):
            return self._mask(obj, stage)
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

    def _note(self, invocation, hits):
        """Record on the invocation's reason how many spans were masked."""
        note = f'redacted {hits} secret(s)'
        invocation.reason = f'{invocation.reason}; {note}' if invocation.reason else note

    def on_call(self, ctx, invocation):
        """
        Mask secret-looking spans in the call's string arguments; never blocks.

        Runs at the tool-call stage, before exec. Each string value in
        ```invocation.arguments``` is masked in place (non-string values are
        left as they are). ```ctx``` is the running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The tool call whose string arguments are
            masked in place when a pattern matches

        """
        if not invocation.arguments:
            return
        hits = 0
        for key, value in invocation.arguments.items():
            if isinstance(value, str):
                masked, count = self._mask(value, GOVERNOR_STAGE_ON_CALL)
                if count:
                    invocation.arguments[key] = masked
                    hits += count
        if hits:
            self._note(invocation, hits)

    def on_return(self, ctx, invocation):
        """
        Mask secret-looking spans in the result; never blocks.

        Runs at the tool-return stage, after the tool ran. ```ctx``` is the
        running state (unused here). The model-facing ```value.as_str``` is
        always masked. When ```sanitize_data``` is on (the default), the
        structured ```value.as_data``` is masked too -- recursively, the string
        values only -- and the value is rebuilt so ```as_json``` carries no
        secret either. With ```sanitize_data``` off, only ```as_str``` is masked
        and the structure is left as the tool returned it.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The completed tool call whose
            ```result.value``` is masked in place when a pattern matches

        """
        if invocation.result is None or invocation.result.value is None:
            return
        value = invocation.result.value
        # the text view is always masked, even when it is the tool's own wording
        # rather than a copy of the data, so both are cleaned on their own terms
        masked_str, hits = self._mask(value.as_str or '', GOVERNOR_STAGE_ON_RETURN)
        masked_data = value.as_data
        if self._config(GOVERNOR_STAGE_ON_RETURN).get('sanitize_data') and value.as_data is not None:
            masked_data, data_hits = self._mask_data(value.as_data, GOVERNOR_STAGE_ON_RETURN)
            hits += data_hits
        if not hits:
            return
        # rebuild the value so all three views are coherent: as_data masked,
        # as_json re-encoded from it, as_str set explicitly to the masked text
        # (the explicit string wins, keeping a reshaped wording). the run state
        # carries over unchanged
        invocation.result = create_tool_result(
            masked_data,
            as_str=masked_str,
            state=dict(
                incomplete=invocation.result.state.incomplete,
                stdout=invocation.result.state.stdout,
                stderr=invocation.result.state.stderr,
                exception=invocation.result.state.exception,
            ),
        )
        self._note(invocation, hits)
