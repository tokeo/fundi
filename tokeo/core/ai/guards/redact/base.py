"""
The ```redact``` guard *type*.

```TokeoAiRedactGuard``` is the type for redact guards: a guard that masks
secret-looking content so a value does not flow on into the message history, the
trace, or a log line. It does nothing on its own -- it is the class you derive
from to write a redact guard, so the agent's guard list and the trace can say
"this is a redact step" while your subclass decides what and how to mask.

Ready-to-use redact implementations live beside this module in the ```redact```
package (e.g. ```regex.py```'s ```TokeoAiRegexRedactGuard```).
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiRedactGuard(TokeoAiGuard):
    """
    The redact guard *type*: mask secret-looking content, never block.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as a redact guard -- masking, not deciding -- without carrying any
    behaviour of its own. Registering this class directly on an agent does
    nothing but make a redact step visible on the stack; it is not registered
    under a config name on purpose, because it is meant to be *derived from* in
    Python, not selected as-is.

    Derive from it when you want a guard that rewrites content to hide secrets
    -- by pattern, by an external classifier, by field name. A redact guard
    masks and never changes ```decision```; it shapes the text the run carries
    on, it does not allow or deny.

    Always derive from this type (```TokeoAiRedactGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- masking -- on the agent and
    the trace. For *what object each stage hands you and which text to mask*
    (```on_call``` the string values in ```invocation.arguments```;
    ```on_return``` the ```invocation.result.text```; ```on_answer```/
    ```on_close``` a ```ChatResult.text```; ```on_begin```/```on_prompt``` the
    contents in ```ctx.messages```), see the stage guide in the
    ```TokeoAiGuard``` class documentation -- the single reference for building a
    guard across its stations.

    A redact guard normally only masks and never stops the run. If masking is
    *required* and cannot be done -- a masking backend it depends on is
    unreachable and the run must not proceed with unmasked content -- raise
    ```TokeoAiRedactGuardError```. That raise is not caught by the loop: it ends
    the whole run at once (see the stop-the-run section in ```TokeoAiGuard```).
    Raise only when proceeding unmasked would be wrong; otherwise mask and let
    the run continue.

    """

    class Meta:
        """Redact guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}
