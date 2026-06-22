"""
The ```validate``` guard *type*.

```TokeoAiValidateGuard``` is the type for validate guards: a guard that checks
a tool call against some contract before it runs and flags or denies a call that
does not hold up. It does nothing on its own -- it is the class you derive from
to write a validation guard, so the agent's guard list and the trace can say
"this is a validate step" while your subclass decides what to check.

Ready-to-use validate implementations live beside this module in the
```validate``` package (e.g. ```tool_schema.py``` with its
```TokeoAiToolSchemaValidator```, which checks the arguments against the tool's
declared parameter schema).
"""

from tokeo.core.ai import TokeoAiGuard


class TokeoAiValidateGuard(TokeoAiGuard):
    """
    The validate guard *type*: check a call against a contract before it runs.

    A pass-through base that does nothing by itself. It exists so a guard can be
    *typed* as a validate guard -- argument checking, contract enforcement, input
    sanity -- without carrying any behaviour of its own. Registering this class
    directly on an agent does nothing but make a validate step visible on the
    stack; it is not registered under a config name on purpose, because it is
    meant to be *derived from* in Python, not selected as-is.

    Derive from it when you want a guard whose job is to *check a tool call*:
    confirm the arguments match a schema, enforce a value range, reject a shape
    the tool cannot handle. Validation guards normally act at ```on_call```,
    before the tool runs, so a bad call is caught before it reaches the tool.

    Always derive from this type (```TokeoAiValidateGuard```), not from
    ```TokeoAiGuard```: it states the guard's role -- validation -- on the agent
    and the trace. For *what object each stage hands you and how to read it*
    (```on_begin```/```on_prompt``` see ```ctx.messages```, ```on_answer```/
    ```on_close``` a ```ChatResult```, ```on_call```/```on_return``` an
    ```Invocation```), see the stage guide in the ```TokeoAiGuard``` class
    documentation -- the single reference for building a guard across its
    stations.

    A validate guard usually does not stop the run: it either denies the one
    call (```decision = DENY``` with a ```reason```, deny-and-continue) or flags
    it (a ```reason``` without a deny) and lets the loop continue. If validation
    is *required* and a failure must abort the whole run, raise
    ```TokeoAiValidateGuardError``` -- that raise is not caught by the loop and
    ends the run at once (see the stop-the-run section in ```TokeoAiGuard```).
    """

    class Meta:
        """Validate guard meta-data."""

        # no configurable settings; empty per the config_defaults rule
        config_defaults = {}
