"""
A ready-to-use validate guard that checks a tool call's arguments against its
declared parameter schema.

```TokeoAiToolSchemaValidator``` runs at ```on_call```, before the tool runs,
and checks the arguments the model passed against the schema the handler
attached to the invocation (```invocation.parameters```). A malformed call -- a
hallucinated argument, a missing required one, a wrong basic type -- is caught
here instead of crashing the tool. It covers the schema subset tool definitions
actually use: ```required``` names, declared ```properties``` (an undeclared
argument is rejected unless the schema sets ```additionalProperties: true```),
and the basic ```type``` of each value. A tool without a declared schema is left
unchecked.

How it reacts is the ```strict``` option:

- ```strict: false``` (the default) -- a failing call is *flagged*: the problems
    are written to ```invocation.reason``` (so they show on the trace) and logged
    as a warning, but the call still runs. Use it to surface schema drift without
    blocking.
- ```strict: true``` -- a failing call is *denied* (```decision = DENY``` with the
    problems as the reason), like the tool policy guard. The loop continues and
    the model sees the ```denied: ...``` feedback, so it can correct the call
    (deny-and-continue).

Place it early in the agent's guard list so broken calls are flagged or caught
before the other ```on_call``` guards run.

```yaml
ai:
  guards:
    tool_schema_validate:
      type: tool_schema_validate
      options:
        strict: true
  agents:
    audited:
      type: fundi
      options:
        guards: [tool_schema_validate, audit]
```
"""

from tokeo.core.ai import Invocation
from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL
from tokeo.core.ai.guards.validate.base import TokeoAiValidateGuard


# the basic json-schema types a tool declaration uses, mapped to their
# python counterparts; bool subclasses int, so integer and number exclude
# it explicitly in the check below
_TYPES = {
    'string': str,
    'integer': int,
    'number': (int, float),
    'boolean': bool,
    'array': list,
    'object': dict,
    'null': type(None),
}


class TokeoAiToolSchemaValidator(TokeoAiValidateGuard):
    """
    An ```on_call``` validate guard that checks arguments against the schema.

    Validates against the schema the handler attached to the invocation
    (```invocation.parameters```). All problems are collected into one reason,
    so the model (in strict mode) can fix the whole call at once. With no
    declared schema (no properties and no required names) the call passes
    unchecked. The ```strict``` setting (read from the guard entry's
    ```options```) decides whether a failing call is denied or only flagged.

    """

    class Meta:
        """Tool-schema validator settings, overridden per guard by its options."""

        # the configurable defaults, as one dict; the guard entry's options (and
        # an on_call override) overlay this, read at runtime via _config. strict:
        # when true a failing call is denied (decision DENY + reason), when false
        # it is only flagged (a reason + a logged warning) and still runs
        config_defaults = dict(
            strict=False,
        )

    def on_call(self, ctx, invocation):
        """
        Check the call's arguments against the tool's schema; deny or flag.

        Runs at the tool-call stage, before exec. ```ctx``` is the running state
        (unused here). With no declared schema the call passes unchecked. On a
        failure the behaviour depends on ```strict```: denied (```decision``` set
        to ```deny```) when strict, otherwise flagged (a ```reason``` and a logged
        warning) and the call still runs.

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The tool call to check; on a failure its
            ```reason``` carries every problem, and (in strict mode) its
            ```decision``` is set to ```deny```

        """
        schema = invocation.parameters or {}
        properties = schema.get('properties') or {}
        required = schema.get('required') or []
        # nothing declared means nothing to check; the tool accepts anything
        if not properties and not required:
            return
        arguments = invocation.arguments or {}
        problems = []
        for name in required:
            if name not in arguments:
                problems.append(f'missing required argument {name!r}')
        # an argument outside the declared properties is almost always a
        # hallucinated name and would crash the tool's exec(**arguments);
        # a schema may opt out via additionalProperties: true
        if schema.get('additionalProperties') is not True:
            for name in arguments:
                if name not in properties:
                    problems.append(f'unknown argument {name!r}')
        for name, value in arguments.items():
            declared = (properties.get(name) or {}).get('type')
            expected = _TYPES.get(declared)
            if expected is None:
                continue
            wrong = not isinstance(value, expected)
            # bool passes isinstance checks against int, but a model sending
            # true for a count is a mistake, not a number
            if declared in ('integer', 'number') and isinstance(value, bool):
                wrong = True
            if wrong:
                problems.append(f'argument {name!r} must be of type {declared!r}')
        if not problems:
            return
        reason = 'invalid arguments: ' + '; '.join(problems)
        # always carry the reason so the trace shows what was wrong; strict
        # turns it into a deny (the loop stops this call), otherwise the call
        # runs and the problems are only surfaced (a warning in the log)
        invocation.reason = reason
        if self._config('strict', stage=GOVERNOR_STAGE_ON_CALL):
            invocation.decision = Invocation.DENY
        else:
            self.app.log.warning(f'tool {invocation.name!r} called with {reason}')
