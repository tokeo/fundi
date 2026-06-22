"""
A ready-to-use audit guard that logs every step of the run.

```TokeoAiTraceAuditGuard``` is built on the audit type and makes the whole
guard chain visible in the application log: one line per step, at every stage.
It is both the standard transparency layer and a worked example of an audit
guard that acts at many stages.
"""

from tokeo.core.utils import date
from tokeo.core.ai.guards.audit.base import TokeoAiAuditGuard


class TokeoAiTraceAuditGuard(TokeoAiAuditGuard):
    """
    A ready-to-use audit guard that logs every step at every stage.

    Built on the audit type, it overrides all six stage methods and writes one
    log line per step -- the stage, the run object in hand, and (at the tool
    stages) the call's outcome. So the whole pipeline becomes visible in the
    log: each model round, each tool call, each guard that ran before it. It
    changes nothing; registered as ```trace_audit```, an agent opts in via its
    guard list.

    It is both the standard transparency layer and a worked example of an audit
    guard that acts at many stages.

    """

    class Meta:
        """Trace audit guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}

    def _log(self, message):
        """
        Write one audit line, prefixed with a UTC timestamp.

        ### Args

        - **message** (str): The trace line to log; a UTC time string is
            prepended so the audit log carries the same UTC stamp the trace
            step records

        """
        self.app.log.info(f'ai trace [{date.to_utc_timestring(date.utc_now())}]: {message}')

    def on_begin(self, ctx):
        """Log the start of the run (the raw incoming request)."""
        self._log(f'begin, {len(ctx.messages)} message(s)')

    def on_prompt(self, ctx):
        """Log a model call about to be made, with the outgoing message count."""
        self._log(f'prompt, {len(ctx.messages)} message(s) to the model')

    def on_answer(self, ctx, result):
        """Log the model's answer (text and/or tool calls)."""
        calls = len(result.tool_calls) if result.tool_calls else 0
        self._log(f'answer, {calls} tool call(s) requested')

    def on_call(self, ctx, invocation):
        """Log a tool call about to run, and whether a guard already denied it."""
        if invocation.decision == invocation.DENY:
            self._log(f'call {invocation.name!r} denied: {invocation.reason}')
        else:
            self._log(f'call {invocation.name!r} arguments={invocation.arguments!r}')

    def on_return(self, ctx, invocation):
        """Log the outcome of a completed tool call (denied, errored, or result)."""
        if invocation.decision == invocation.DENY:
            self._log(f'return {invocation.name!r} denied: {invocation.reason}')
        elif invocation.error is not None:
            # name WHERE it ran when a sandbox was reached (a denied call never
            # reaches one, so sandbox stays None there)
            where = f' in sandbox {invocation.sandbox!r}' if invocation.sandbox else ''
            self._log(f'return {invocation.name!r} errored{where}: {invocation.error}')
        else:
            where = f' in sandbox {invocation.sandbox!r}' if invocation.sandbox else ''
            text = invocation.result.text if invocation.result is not None else ''
            self._log(f'return {invocation.name!r} ran{where}, returned: {text!r}')

    def on_close(self, ctx, result):
        """Log the final result of the run."""
        self._log(f'close, final answer of {len(result.text or "")} char(s)')
