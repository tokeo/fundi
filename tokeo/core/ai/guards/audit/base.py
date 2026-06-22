"""
The ```audit``` guard *type*.

```TokeoAiAuditGuard``` is the type for audit guards: a guard that only observes
the run and never changes it. It does nothing on its own -- it is the class you
derive from to write an audit guard, so the agent's guard list and the trace can
say "this is an audit step" while your subclass decides what to record.

Ready-to-use audit implementations live beside this module in the ```audit```
package (e.g. ```trace.py```'s ```TokeoAiTraceAuditGuard```).
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiAuditGuard(TokeoAiGuard):
    """
    The audit guard *type*: observe the run, never change it.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as an audit guard -- recording, transparency, observation -- without
    carrying any behaviour of its own. Registering this class directly on an
    agent does nothing but make an audit step visible on the stack; it is not
    registered under a config name on purpose, because it is meant to be
    *derived from* in Python, not selected as-is.

    Derive from it when you want a guard whose job is to *watch*: write a log
    line, push a metric, append to an external audit store. Override the stage
    methods you care about and record there; do not change ```decision```,
    ```result```, or the messages -- an audit guard observes, the formers change.

    Always derive from this type (```TokeoAiAuditGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- observation -- on the agent
    and the trace. For *what object each stage hands you and how to read it*
    (```on_begin```/```on_prompt``` see ```ctx.messages```, ```on_answer```/
    ```on_close``` a ```ChatResult```, ```on_call```/```on_return``` an
    ```Invocation```), see the stage guide in the ```TokeoAiGuard``` class
    documentation -- the single reference for building a guard across its
    stations. An audit guard uses those objects read-only.

    An audit guard normally never stops the run. If observation is *required*
    and must not be skipped -- an external audit store is unreachable and the run
    must not proceed unrecorded -- raise ```TokeoAiAuditGuardError```. That raise
    is not caught by the loop: it ends the whole run at once (see the stop-the-run
    section in ```TokeoAiGuard```). Raise only when proceeding unrecorded would be
    wrong; otherwise observe and let the run continue.

    Example -- log only denied tool calls:

    ```python
    class MyDenyAuditGuard(TokeoAiAuditGuard):
        def on_return(self, ctx, invocation):
            if invocation.decision == Invocation.DENY:
                self.app.log.warning(f'denied: {invocation.name}')
    ```

    """

    class Meta:
        """Audit guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}
