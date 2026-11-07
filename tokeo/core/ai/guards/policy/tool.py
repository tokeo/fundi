"""
A ready-to-use policy guard that allows or denies a tool call by its name.

```TokeoAiToolPolicyGuard``` is the action-level governance baseline: it shapes
what an agent may *do*, not just what it may say. Rules come from the guard
entry's ```options``` (```allow``` and ```deny``` lists). A denied call is not
executed; the loop continues and the model sees a ```denied: ...``` result, so
the agent can react instead of crash (deny-and-continue).

```yaml
ai:
  guards:
    safe:
      type: tool_policy
      options:
        deny: [shell]
    mathonly:
      type: tool_policy
      options:
        allow: [calc]
```
"""

from tokeo.core.ai import Invocation
from tokeo.core.ai.governor import GOVERNOR_STAGE_ON_CALL
from tokeo.core.ai.guards.policy.base import TokeoAiPolicyGuard


class TokeoAiToolPolicyGuard(TokeoAiPolicyGuard):
    """
    An ```on_call``` policy guard that allows or denies tool calls by name.

    The rules are read through ```_config``` from the guard entry's
    ```options``` (and an ```on_call``` override): ```deny``` is a denylist and
    always wins; ```allow```, when set, is an allowlist that restricts calls to
    its members. Both take tool and group names, a group stands for its
    members and a single name is a list of one.
    With neither rule the guard permits every call (it then only documents
    intent). A denial is soft: ```decision``` is set to ```deny``` and the loop
    continues.

    """

    class Meta:
        """Tool policy guard meta-data."""

        # the configurable defaults, as one dict; a guard entry's options (and an
        # on_call override) overlay this, read at runtime via _config
        config_defaults = dict(
            # tools allowed, None means "allow any tool not denied"
            allow=None,
            # tools always denied, a deny wins over the allowlist
            deny=[],
        )

        # the options that hold tool and group names, checked by the linter
        tool_name_options = ('allow', 'deny')

    def on_call(self, ctx, invocation):
        """
        Deny the call when the policy forbids the tool; otherwise allow.

        Runs at the tool-call stage, before exec, so it can stop a call from
        running. ```ctx``` is the running state (unused here).

        ### Args

        - **ctx** (TokeoAiContext): The running state
        - **invocation** (Invocation): The tool call to check; on a denial its
            ```decision``` is set to ```deny``` with a ```reason```

        """
        # tool and group names, a single name is a list of one, a group
        # stands for its members as everywhere in the config
        name = invocation.name
        denied = name in self.app.ai.resolve_tool_names(self._config('deny', stage=GOVERNOR_STAGE_ON_CALL))
        allow = self._config('allow', stage=GOVERNOR_STAGE_ON_CALL)
        if not denied and allow is not None:
            denied = name not in self.app.ai.resolve_tool_names(allow)
        if denied:
            invocation.decision = Invocation.DENY
            invocation.reason = f'tool {name!r} is not permitted by policy'
