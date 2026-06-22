"""
The ```policy``` guard *type*.

```TokeoAiPolicyGuard``` is the type for policy guards: a guard that governs
*what an agent may do* -- allowing, denying, or stopping an action. It does
nothing on its own -- it is the class you derive from to write a policy guard,
so the agent's guard list and the trace can say "this is a policy step".

Ready-to-use policy implementations live beside this module in the ```policy```
package (```tool.py```'s name-based allow/deny, ```deny.py```'s soft denial,
```abort.py```'s hard stop).
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiPolicyGuard(TokeoAiGuard):
    """
    The policy guard *type*: govern what the agent may do.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as a policy guard -- action-level governance -- without carrying any
    behaviour of its own. Registering this class directly on an agent does
    nothing but make a policy step visible on the stack; it is not registered
    under a config name on purpose, because it is meant to be *derived from* in
    Python, not selected as-is.

    Derive from it when you want a guard that allows, denies, or stops an action
    at a stage. A tool call can be softly denied via
    ```invocation.decision = Invocation.DENY``` (the loop continues, the model
    sees the denial); a run can be stopped hard by raising
    ```TokeoAiPolicyGuardError```.

    Always derive from this type (```TokeoAiPolicyGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- governance -- on the agent
    and the trace. For *what object each stage hands you and how to act on it*
    (```on_call``` is where a pending ```Invocation``` is denied via its
    ```decision```/```reason```; ```on_begin```/```on_prompt``` see
    ```ctx.messages```; ```on_answer```/```on_close``` a ```ChatResult```), see
    the stage guide in the ```TokeoAiGuard``` class documentation -- the single
    reference for building a guard across its stations.

    The two levels of refusal matter most for a policy guard:

    - **Soft denial** -- ```invocation.decision = Invocation.DENY``` at
        ```on_call```. Skips that one tool call; the loop continues and the model
        sees the denial and may try another way. Use this when only the single
        action is the problem.
    - **Hard abort** -- ```raise TokeoAiPolicyGuardError```. The loop does not
        catch it, so it ends the whole run at once (see the stop-the-run section
        in ```TokeoAiGuard```). Use this when the policy violation must stop
        everything, not just skip a call -- and at the non-tool stages, where a
        soft per-call denial has no meaning, a raise is the only way to refuse.

    Raise the typed ```TokeoAiPolicyGuardError``` (not the bare base) so a caller
    can catch a policy abort specifically.

    """

    class Meta:
        """Policy guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}
