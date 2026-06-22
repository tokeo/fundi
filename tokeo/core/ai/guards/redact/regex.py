r"""
A ready-to-use redact guard that masks secret-looking spans by regex.

```TokeoAiRegexRedactGuard``` masks at both tool stages: the outgoing call
arguments (```on_call```) and the returned result text (```on_return```), so a
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
```
"""

import re

from tokeo.core.ai.guard import GUARD_STAGE_ON_CALL, GUARD_STAGE_ON_RETURN
from tokeo.core.ai.guards.redact.base import TokeoAiRedactGuard
from tokeo.core.ai.guards.redact.exc import TokeoAiRedactGuardError


class TokeoAiRegexRedactGuard(TokeoAiRedactGuard):
    """
    A redact guard that masks secret-looking spans by regex, at the tool stages.

    Applies each configured pattern to the text it handles and replaces every
    match with the ```replacement``` marker: at ```on_call``` to each string
    value in ```invocation.arguments```, at ```on_return``` to
    ```invocation.result.text```. It never changes the ```decision```, so it is
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
        # replacement: what each matched span is replaced with
        config_defaults = dict(
            patterns=None,
            replacement='[redacted]',
        )

    def __init__(self, app, *args, **kw):
        """
        Initialize the regex redact guard.

        ### Args

        - **app**: The Tokeo application instance
        - ***args**: Positional arguments for the parent initializer
        - ****kw**: Keyword arguments for the parent initializer

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
                masked, count = self._mask(value, GUARD_STAGE_ON_CALL)
                if count:
                    invocation.arguments[key] = masked
                    hits += count
        if hits:
            self._note(invocation, hits)

    def on_return(self, ctx, invocation):
        """
        Mask secret-looking spans in the result text; never blocks.

        Runs at the tool-return stage, after the tool ran. ```ctx``` is the
        running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The completed tool call whose
            ```result.text``` is masked in place when a pattern matches

        """
        if invocation.result is None:
            return
        masked, hits = self._mask(invocation.result.text or '', GUARD_STAGE_ON_RETURN)
        if hits:
            invocation.result.text = masked
            self._note(invocation, hits)
