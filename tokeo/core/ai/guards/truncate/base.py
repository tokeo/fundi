"""
The ```truncate``` guard *type*.

```TokeoAiTruncateGuard``` is the type for truncate guards: a guard that caps
over-long text so a large payload cannot blow the context budget or flood the
trace and log. It does nothing on its own -- it is the class you derive from to
write a truncate guard, so the agent's guard list and the trace can say "this is
a truncate step" while your subclass decides what to shorten and where.

Unlike audit/policy/redact, no ready-to-use truncate implementation ships in the
core: a generated project carries one as an editable example (its own
```core/ai/guards/truncate.py```), named by its full dotted class path in the
config. The core provides only this derivable type.
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiTruncateGuard(TokeoAiGuard):
    """
    The truncate guard *type*: cap over-long text, never block.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as a truncate guard -- shortening, not deciding -- without carrying
    any behaviour of its own. Registering this class directly on an agent does
    nothing but make a truncate step visible on the stack; it is not registered
    under a config name on purpose, because it is meant to be *derived from* in
    Python, not selected as-is.

    Derive from it when you want a guard that shortens the text the run carries
    on -- a long tool result, a large final answer -- keeping a head and marking
    what was cut. A truncate guard shortens and never changes ```decision```; it
    shapes the text, it does not allow or deny.

    Always derive from this type (```TokeoAiTruncateGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- shortening -- on the agent
    and the trace. For *what object each stage hands you and which text to cap*
    (```on_return``` the ```invocation.result.text``` of a completed tool call;
    ```on_close``` the final ```ChatResult.text``` of the whole run;
    ```on_answer``` a per-round ```ChatResult.text```), see the stage guide in
    the ```TokeoAiGuard``` class documentation -- the single reference for
    building a guard across its stations.

    A truncate guard normally only shortens and never stops the run. If
    shortening is *required* and cannot be applied -- the run must not carry an
    oversized payload on and the cap cannot be made -- raise
    ```TokeoAiTruncateGuardError```. That raise is not caught by the loop: it
    ends the whole run at once (see the stop-the-run section in
    ```TokeoAiGuard```). Raise only when proceeding oversized would be wrong;
    otherwise shorten and let the run continue.

    """

    class Meta:
        """Truncate guard meta-data."""

        # no configurable settings; empty per the config_defaults rule. a
        # concrete truncate guard declares its own (e.g. limit, marker)
        config_defaults = {}
