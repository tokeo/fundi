"""
The ```confirm``` guard *type*.

```TokeoAiConfirmGuard``` is the type for confirm guards: a guard that pauses
the run to ask for a human decision before something happens (a prompt about to
be sent, a tool about to run) and lets the answer allow, deny, or change it. It
does nothing on its own -- it is the class you derive from to write a confirm
guard, so the agent's guard list and the trace can say "this is a confirm step"
while your subclass decides what to ask and how to apply the answer.

No ready-to-use confirm implementation ships in this package yet: the confirm
machinery (the input channel, the answer levels, the remembering of choices) is
not built. This is the type it will be built on, available now so a guard can
already be typed as a confirm.
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiConfirmGuard(TokeoAiGuard):
    """
    The confirm guard *type*: pause the run for a human decision.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as a confirm guard -- a human-in-the-loop gate -- without carrying
    any behaviour of its own. Registering this class directly on an agent does
    nothing but make a confirm step visible on the stack; it is not registered
    under a config name on purpose, because it is meant to be *derived from* in
    Python, not selected as-is.

    Derive from it when you want a guard that stops at a stage (typically
    ```on_prompt``` or ```on_call```) and asks a human to allow, deny, or edit
    what is about to happen, then carries that decision back into the run.

    Always derive from this type (```TokeoAiConfirmGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- a human-in-the-loop gate --
    on the agent and the trace. For *what object each stage hands you and how to
    read it* (```on_begin```/```on_prompt``` see ```ctx.messages```,
    ```on_answer```/```on_close``` a ```ChatResult```, ```on_call```/
    ```on_return``` an ```Invocation```), see the stage guide in the
    ```TokeoAiGuard``` class documentation -- the single reference for building a
    guard across its stations.

    A confirm guard that must abort the run rather than continue (the human
    declined and the run must not proceed) raises ```TokeoAiConfirmGuardError```
    -- that raise is not caught by the loop and ends the run at once (see the
    stop-the-run section in ```TokeoAiGuard```).
    """

    class Meta:
        """Confirm guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}
